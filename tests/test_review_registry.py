"""The registry is the only place that joins college review kinds."""
from dataclasses import replace
import json

import pytest

from server import html_email_review, image_bio_review, recovery_policy, review_registry
from server.task_image_review import carry_review_decisions


def _audit(tmp_path, unresolved, **extra):
    path = tmp_path / "attempt-1" / "a_audit.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"unresolved": unresolved, **extra}), encoding="utf-8")
    return path


def test_kind_names_and_decision_files_are_unique():
    assert len(review_registry.BY_NAME) == len(review_registry.KINDS)
    assert len(set(review_registry.DECISION_FILES)) == len(review_registry.DECISION_FILES)


def test_every_kind_status_is_a_review_code():
    review = {code for code, action in recovery_policy.POLICIES.items() if action == "review"}
    assert review_registry.STATUSES - review == set()


@pytest.mark.parametrize("kind", review_registry.KINDS, ids=lambda kind: kind.name)
def test_a_flagged_issue_without_a_review_item_is_never_complete(tmp_path, kind):
    status = sorted(kind.statuses)[0]
    issue = {"status": status, "kind": "image", "姓名": "某", "url": "https://x.edu/p", "name": "某"}
    path = _audit(tmp_path, [issue], html_email_review_items=[])
    audit = json.loads(path.read_text(encoding="utf-8"))
    assert kind.is_triggered(audit) and not kind.rerun_only(audit)
    assert not kind.complete(path, audit, [])


def test_resumed_attempt_receives_every_kind_decision_file(tmp_path):
    source = _audit(tmp_path, [])
    for filename in review_registry.DECISION_FILES:
        (source.parent / filename).write_text('{"schema": 1, "decisions": []}', encoding="utf-8")
    (tmp_path / "review-resume.json").write_text(json.dumps(
        {"task_id": tmp_path.name, "audit_path": str(source)}), encoding="utf-8")
    target = tmp_path / "attempt-2"
    carry_review_decisions(tmp_path, target)
    assert {path.name for path in target.iterdir()} == set(review_registry.DECISION_FILES)


def test_email_conflict_without_a_reviewable_item_holds_the_retry(tmp_path):
    flagged = [{"status": image_bio_review.EMAIL_STATUS, "url": "https://x.edu/a", "name": "甲"},
               {"status": image_bio_review.EMAIL_STATUS, "url": "https://x.edu/b", "name": "乙"}]
    path = _audit(tmp_path, flagged)
    item = {"id": "e1", "profile_url": "https://x.edu/a", "owner_name": "甲", "choices": ["a@x.edu", "b@x.edu"]}
    image_bio_review.save_email_decision(path.parent / image_bio_review.EMAIL_DECISIONS_FILE, item, "a@x.edu")
    audit = json.loads(path.read_text(encoding="utf-8"))
    assert not image_bio_review.email_review_complete(path, audit, [item])
    audit["unresolved"] = flagged[:1]
    assert image_bio_review.email_review_complete(path, audit, [item])


def test_issues_no_kind_owns_do_not_hold_a_finished_table_review(tmp_path, monkeypatch):
    path = _audit(tmp_path, [{"status": "table_cell_low_confidence"}, {"status": "title_field_conflict"},
                             {"status": "table_asset_failed"}], image_table_evidence=[{"url": "u"}])
    decided = {"done": False}
    monkeypatch.setattr(review_registry, "KINDS", tuple(
        replace(kind, collect=lambda path: [], complete=lambda path, audit, items: decided["done"])
        if kind.name == "image_table" else kind for kind in review_registry.KINDS))
    assert not review_registry.review_complete(path)
    decided["done"] = True
    assert review_registry.review_complete(path)


def test_nothing_to_decide_is_not_complete(tmp_path):
    assert not review_registry.review_complete(_audit(tmp_path, [{"status": "title_field_conflict"}]))


def test_shown_and_waiting_issues_keep_their_previous_meaning(tmp_path):
    pdf = _audit(tmp_path, [{"status": "image_bio_ocr_incomplete", "kind": "pdf"}])
    assert not review_registry.has_review(pdf) and not review_registry.review_complete(pdf)
    nameless = _audit(tmp_path, [{"status": "mailto_address_conflict", "url": "https://x.edu/list"}])
    assert review_registry.has_review(nameless) and not review_registry.review_complete(nameless)
    old = _audit(tmp_path, [{"status": "ambiguous_shared_email", "姓名": "某", "主页": "https://x.edu/p"}])
    assert html_email_review.evidence_missing(json.loads(old.read_text(encoding="utf-8")))
    assert review_registry.review_complete(old)  # only a rerun can produce the evidence


def test_decision_store_replaces_by_id_and_keeps_other_rows(tmp_path):
    from server import review_store
    path = tmp_path / "attempt-1" / "x_reviews.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema": 1, "decisions": [{"id": "a", "v": 1}, {"note": "kept"}]}), encoding="utf-8")
    review_store.put(path, {"id": "a", "v": 2})
    review_store.put(path, {"id": "b", "v": 3})
    assert review_store.rows(path) == [{"note": "kept"}, {"id": "a", "v": 2}, {"id": "b", "v": 3}]
    assert review_store.load(path) == {}  # a row without an id makes the file unusable as a whole
    assert [p.name for p in path.parent.iterdir()] == ["x_reviews.json"]
    path.write_text(json.dumps({"schema": 2, "decisions": [{"id": "a"}]}), encoding="utf-8")
    assert review_store.rows(path) == [] and review_store.load(tmp_path / "missing.json") == {}
