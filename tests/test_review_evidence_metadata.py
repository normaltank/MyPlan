"""Reviewed sources survive resume; only a proved CMS counter may vary."""

import hashlib
import json

import pytest

from server import missing_media_review
from server.task_image_review import prepare_review_resume, carry_review_decisions

URL = "https://faculty.example.edu/info/1042/1852.htm"
MEDIA = "https://faculty.example.edu/photo.jpg"


def page(nonce, count, body="完整履历。"):
    return (
        '<html><head><script src="/system/resource/js/dynclicks.js"></script></head><body>'
        '<article id="profile"><h1>王芳</h1><div class="detail-tit"><p><span>'
        '<script>_showDynClicks("wbnews", 2062419276, 1852)</script>'
        f'<span id="dynclicks_wbnews_1852_{nonce}" name="dynclicks_wbnews_1852_{nonce}">{count}</span>'
        f'</span></p></div><p>{body}</p><img src="{MEDIA}"></article></body></html>'
    )


def source(tmp_path):
    task_root = tmp_path / "tasks" / "fixture-task"
    attempt = task_root / "attempt-1"
    captures = attempt / "snapshots"
    captures.mkdir(parents=True)
    html = page(98, 450)
    snapshot = captures / "profile.html"
    snapshot.write_text(html, encoding="utf8")
    item = missing_media_review.prepare_item(
        html, URL, "article#profile", None, MEDIA, 404
    )
    item.update(snapshot_path=str(snapshot), missing_on_two_opens=True)
    audit = attempt / "audit.json"
    audit.write_text(
        json.dumps(
            {
                "school": "样本大学",
                "college": "样本学院",
                "audit_path": str(audit),
                "snapshots": [{"url": URL, "kind": "profile", "path": str(snapshot)}],
                "missing_profile_media_evidence": [item],
                "unresolved": [
                    {"status": missing_media_review.STATUS, "evidence_id": item["id"]}
                ],
                "coverage": {
                    "field_sources": [
                        {
                            "profile_url": URL,
                            "profile_content_selector": "article#profile",
                            "profile_content_start_selector": None,
                            "selected_html_sha256": item["selected_html_sha256"],
                        }
                    ]
                },
            }
        ),
        encoding="utf8",
    )
    missing_media_review.save_decision(
        attempt / "media_kind_reviews.json", item, "photo", reviewed_by=7
    )
    return task_root, attempt, audit, snapshot, item


def test_resume_seals_reviewed_original_before_same_attempt_overwrite(tmp_path):
    from server.review_evidence import archived_sources

    root, attempt, audit, snapshot, item = source(tmp_path)
    original = hashlib.sha256(snapshot.read_bytes()).hexdigest()
    prepare_review_resume(audit, root.name)
    carry_review_decisions(root, attempt)
    snapshot.write_text(page(13, 451), encoding="utf8")
    bundles = archived_sources(root)
    assert len(bundles) == 1
    assert bundles[0].source_html(URL) == page(98, 450)
    assert bundles[0].original_hash(URL) == original
    assert (
        bundles[0].decisions("media_kind_reviews.json")[item["id"]]["verdict"]
        == "photo"
    )


def test_same_reviewed_photo_survives_only_native_counter_change(tmp_path):
    root, attempt, audit, snapshot, item = source(tmp_path)
    prepare_review_resume(audit, root.name)
    newer = page(13, 451)
    fresh = missing_media_review.prepare_item(
        newer, URL, "article#profile", None, MEDIA, 404
    )
    assert (
        fresh["selected_html_sha256"] != item["selected_html_sha256"]
        and fresh["id"] != item["id"]
    )
    assert missing_media_review.confirmed(
        attempt / "media_kind_reviews.json", fresh, source_html=newer
    )
    assert (
        json.loads((attempt / "media_kind_reviews.json").read_text(encoding="utf8"))[
            "decisions"
        ][0]["id"]
        == item["id"]
    )


def test_current_unreadable_decision_overrides_old_photo(tmp_path):
    root, attempt, audit, snapshot, item = source(tmp_path)
    prepare_review_resume(audit, root.name)
    newer = page(13, 451)
    fresh = missing_media_review.prepare_item(
        newer, URL, "article#profile", None, MEDIA, 404
    )
    missing_media_review.save_decision(
        attempt / "media_kind_reviews.json", fresh, "unreadable", reviewed_by=7
    )
    assert not missing_media_review.confirmed(
        attempt / "media_kind_reviews.json", fresh, source_html=newer
    )


def test_counter_proof_does_not_hide_other_native_serialization_changes():
    from server.review_evidence import equivalence, sha

    before = page(98, 450).replace("<p>完整履历。", '<p data-a="1" data-b="2">完整履历。')
    after = page(13, 451).replace("<p>完整履历。", '<p data-b="2" data-a="1">完整履历。')
    assert equivalence(before, after, URL, (None, None), (None, None), sha(before), sha(after)) is None


def test_scoped_media_consumer_rejects_non_counter_native_attribute_change(tmp_path):
    root, attempt, audit, snapshot, item = source(tmp_path)
    before = page(98, 450).replace("<p>完整履历。", '<p data-a="1" data-b="2">完整履历。')
    after = page(13, 451).replace("<p>完整履历。", '<p data-b="2" data-a="1">完整履历。')
    snapshot.write_text(before, encoding="utf8")
    old = missing_media_review.prepare_item(before, URL, "article#profile", None, MEDIA, 404)
    old.update(snapshot_path=str(snapshot), missing_on_two_opens=True)
    value = json.loads(audit.read_text(encoding="utf8"))
    value["missing_profile_media_evidence"] = [old]
    value["coverage"]["field_sources"][0]["selected_html_sha256"] = old["selected_html_sha256"]
    audit.write_text(json.dumps(value), encoding="utf8")
    missing_media_review.save_decision(attempt / "media_kind_reviews.json", old, "photo", reviewed_by=7)
    prepare_review_resume(audit, root.name)
    fresh = missing_media_review.prepare_item(after, URL, "article#profile", None, MEDIA, 404)
    assert not missing_media_review.confirmed(attempt / "media_kind_reviews.json", fresh, source_html=after)
    assert "review_equivalence" not in fresh


@pytest.mark.parametrize("given", ["none", "source-only", "sha-only"])
def test_external_audit_never_uses_implicit_local_registration(tmp_path, given):
    import shutil
    from server.review_evidence import register_source

    root, attempt, audit, snapshot, item = source(tmp_path)
    backup = tmp_path / "backup" / "attempt-1"
    shutil.copytree(attempt, backup)
    kwargs = {}
    if given == "source-only":
        kwargs["source_root"] = backup
    elif given == "sha-only":
        kwargs["expected_audit_sha256"] = hashlib.sha256(audit.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        register_source(root, backup / "audit.json", root.name, **kwargs)
    assert not list((root / "review-evidence" / "sources").glob("*.json"))


@pytest.mark.parametrize("target", ["audit", "snapshot", "decision", "ledger"])
def test_original_registration_rejects_symlink_escape(tmp_path, target):
    from server.review_evidence import register_source

    root, attempt, audit, snapshot, item = source(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    if target == "ledger":
        link = root / "review-evidence"
        original = outside
    else:
        link = {"audit": audit, "snapshot": snapshot,
                "decision": attempt / "media_kind_reviews.json"}[target]
        original = outside / link.name
        original.write_bytes(link.read_bytes())
        link.unlink()
    try:
        link.symlink_to(original, target_is_directory=target == "ledger")
    except OSError:
        pytest.skip("host cannot create symbolic links")
    with pytest.raises(ValueError):
        register_source(root, audit, root.name)
    assert not list((root / "review-evidence" / "sources").glob("*.json"))


def test_explicit_import_rejects_unknown_decision_schema(tmp_path):
    import shutil
    from server.review_evidence import register_source

    root, attempt, audit, snapshot, item = source(tmp_path)
    backup = tmp_path / "backup" / "attempt-1"
    shutil.copytree(attempt, backup)
    decision = backup / "media_kind_reviews.json"
    value = json.loads(decision.read_text(encoding="utf8"))
    value["schema"] = 2
    decision.write_text(json.dumps(value), encoding="utf8")
    with pytest.raises(ValueError):
        register_source(root, backup / "audit.json", root.name, source_root=backup,
                        expected_audit_sha256=hashlib.sha256(audit.read_bytes()).hexdigest())
    assert not list((root / "review-evidence" / "sources").glob("*.json"))


def test_native_counter_equivalence_rejects_unimplemented_start_boundary():
    from server.review_evidence import equivalence, sha
    from server.profile_content import scope_profile_html

    before = page(98, 450)
    after = page(13, 451)
    start = "article#profile > h1"
    old_scope, _, _ = scope_profile_html(before, "article#profile", URL, start_selector=start)
    new_scope, _, _ = scope_profile_html(after, "article#profile", URL, start_selector=start)
    assert equivalence(before, after, URL, ("article#profile", start), ("article#profile", start),
                       sha(old_scope), sha(new_scope)) is None


@pytest.mark.parametrize("bad", ["task", "digest", "missing", "scope", "snapshot-owner", "no-original"])
def test_relocated_original_registration_rejects_unbound_material(tmp_path, bad):
    from server.review_evidence import register_source

    root, attempt, audit, snapshot, item = source(tmp_path)
    digest = hashlib.sha256(audit.read_bytes()).hexdigest()
    if bad == "task":
        root = root.parent / "other-task"
    elif bad == "digest":
        digest = "0" * 64
    elif bad == "missing":
        snapshot.unlink()
    elif bad in {"scope", "snapshot-owner", "no-original"}:
        value = json.loads(audit.read_text(encoding="utf8"))
        if bad == "scope":
            value["missing_profile_media_evidence"][0]["selected_html_sha256"] = "0" * 64
        elif bad == "snapshot-owner":
            value["snapshots"][0]["path"] = str(snapshot).replace("fixture-task", "other-task")
        else:
            value["snapshots"] = []
        audit.write_text(json.dumps(value), encoding="utf8")
        digest = hashlib.sha256(audit.read_bytes()).hexdigest()
    with pytest.raises((ValueError, FileNotFoundError)):
        register_source(root, audit, root.name, source_root=attempt, expected_audit_sha256=digest)
    assert not list((root / "review-evidence" / "sources").glob("*.json"))


def test_pinned_relocated_original_registers_without_rewriting_human_decision(tmp_path):
    import shutil
    from server.review_evidence import register_source, archived_sources

    root, attempt, audit, snapshot, item = source(tmp_path)
    original = snapshot.read_bytes()
    decision_path = attempt / "media_kind_reviews.json"
    decision = decision_path.read_bytes()
    backup = tmp_path / "backup" / "attempt-1"
    shutil.copytree(attempt, backup)
    snapshot.write_text(page(13, 451), encoding="utf8")
    register_source(root, backup / "audit.json", root.name, source_root=backup,
                    expected_audit_sha256=hashlib.sha256(audit.read_bytes()).hexdigest())
    assert archived_sources(root)[0].original_hash(URL) == hashlib.sha256(original).hexdigest()
    assert decision_path.read_bytes() == decision
    assert (backup / "media_kind_reviews.json").read_bytes() == decision


@pytest.mark.parametrize(
    "change", ["body", "owner", "resource", "status", "selector", "unknown-digit"]
)
def test_non_counter_changes_do_not_reuse_a_photo_decision(tmp_path, change):
    root, attempt, audit, snapshot, item = source(tmp_path)
    prepare_review_resume(audit, root.name)
    newer = page(13, 451, "完整履历。2025" if change == "body" else "完整履历。")
    if change == "owner":
        newer = newer.replace("2062419276", "1234")
    if change == "resource":
        newer = newer.replace(MEDIA, MEDIA + "?v=2")
    if change == "unknown-digit":
        newer = newer.replace("完整履历。", "完整履历。<span>123</span>")
    selector = "article" if change == "selector" else "article#profile"
    resource = MEDIA + "?v=2" if change == "resource" else MEDIA
    fresh = missing_media_review.prepare_item(
        newer, URL, selector, None, resource, 410 if change == "status" else 404
    )
    assert not missing_media_review.confirmed(
        attempt / "media_kind_reviews.json", fresh, source_html=newer
    )


def test_revoked_human_decision_is_not_resurrected_from_archive(tmp_path):
    from server import review_store

    root, attempt, audit, snapshot, item = source(tmp_path)
    prepare_review_resume(audit, root.name)
    decision = review_store.load(attempt / "media_kind_reviews.json")[item["id"]]
    decision["verdict"] = "unreadable"
    review_store.put(attempt / "media_kind_reviews.json", decision)
    newer = page(13, 451)
    fresh = missing_media_review.prepare_item(
        newer, URL, "article#profile", None, MEDIA, 404
    )
    assert not missing_media_review.confirmed(
        attempt / "media_kind_reviews.json", fresh, source_html=newer
    )


def test_altered_archived_original_cannot_certify_equivalence(tmp_path):
    from server.review_evidence import archived_sources

    root, attempt, audit, snapshot, item = source(tmp_path)
    prepare_review_resume(audit, root.name)
    bundle = archived_sources(root)[0]
    archived = (
        root / "review-evidence" / "objects" / (bundle.original_hash(URL) + ".html")
    )
    archived.write_text("changed", encoding="utf8")
    newer = page(13, 451)
    fresh = missing_media_review.prepare_item(
        newer, URL, "article#profile", None, MEDIA, 404
    )
    assert not missing_media_review.confirmed(
        attempt / "media_kind_reviews.json", fresh, source_html=newer
    )


def test_visit_number_is_not_arbitrary_numeric_text(tmp_path):
    root, attempt, audit, snapshot, item = source(tmp_path)
    prepare_review_resume(audit, root.name)
    newer = page(13, 451).replace(
        '_showDynClicks("wbnews", 2062419276, 1852)', "someOtherController(1852)"
    )
    fresh = missing_media_review.prepare_item(
        newer, URL, "article#profile", None, MEDIA, 404
    )
    assert not missing_media_review.confirmed(
        attempt / "media_kind_reviews.json", fresh, source_html=newer
    )


def test_windows_snapshot_newlines_recover_the_original_browser_text(tmp_path):
    from server.review_evidence import archived_sources

    root, attempt, audit, snapshot, item = source(tmp_path)
    snapshot.write_bytes(page(98, 450).replace("<p>完整", "\r\n<p>完整").encode())
    html = snapshot.read_text(encoding="utf8")
    # The snapshot material, not an invented new decision, is archived. This
    # check isolates byte retention from the original universal-newline reader.
    prepare_review_resume(audit, root.name)
    bundle = archived_sources(root)[0]
    assert bundle.source_html(URL) == html
    assert (
        bundle.original_hash(URL) == hashlib.sha256(snapshot.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("changed_field", [None, "职称"])
def test_identity_consumer_revalidates_same_human_decision_on_certified_current_rows(
    tmp_path, changed_field
):
    from server import identity_review
    from server.profile_content import scope_profile_html
    from server.detail_mecc import LayoutAwareTokenizer
    from bs4 import BeautifulSoup

    root, attempt, audit, snapshot, media = source(tmp_path)
    old_html = page(98, 450)
    new_html = page(13, 451)
    old_scope, old_e, _ = scope_profile_html(old_html, "article#profile", URL)
    new_scope, new_e, _ = scope_profile_html(new_html, "article#profile", URL)
    biography = lambda h: LayoutAwareTokenizer.get_clean_text(
        BeautifulSoup(h, "lxml").body, preserve_content=True
    )
    first = {
        "姓名": "王芳",
        "主页": URL,
        "学校": "样本大学",
        "学院": "样本学院",
        "职称": "教授",
        "邮箱": "",
        "简介": biography(old_scope),
    }
    second = {**first, "主页": "https://faculty.example.edu/list", "简介": ""}
    old_hashes = {URL: old_e["selected_html_sha256"], second["主页"]: "a" * 64}
    old_item = identity_review.pending_items([first, second], old_hashes)[0]
    identity_review.save_decision(
        attempt / "identity_reviews.json", old_item, "same", URL, reviewed_by=7
    )
    value = json.loads(audit.read_text(encoding="utf8"))
    value["identity_review_items"] = [old_item]
    audit.write_text(json.dumps(value), encoding="utf8")
    prepare_review_resume(audit, root.name)
    current = {**first, "简介": biography(new_scope)}
    if changed_field:
        current[changed_field] = "副教授"
    updated, pending, certificates = identity_review.apply_decisions(
        [current, second],
        {**old_hashes, URL: new_e["selected_html_sha256"]},
        attempt / "identity_reviews.json",
        source_html={URL: new_html},
        source_evidence={URL: new_e},
    )
    if changed_field:
        assert len(pending) == 1 and not certificates
    else:
        assert (
            not pending
            and len(updated) == 1
            and certificates[0]["id"] == old_item["id"]
        )
        assert (
            certificates[0]["source_equivalence"][0]["old_bound_source_sha256"]
            == old_e["selected_html_sha256"]
        )
        proof = certificates[0]["source_equivalence"][0]
        start, end = proof["old_native_text_span"]
        assert proof["old_raw_sha256"] == hashlib.sha256(old_html[start:end].encode()).hexdigest()
        assert proof["old_raw_sha256"] != proof["old_bound_source_sha256"]
        identity_review.validated_pairs(
            updated,
            json.loads(json.dumps(certificates)),
            attempt / "identity_reviews.json",
        )


@pytest.mark.parametrize("changed_candidate", [False, True])
def test_html_email_consumer_keeps_address_and_owner_constraints(
    tmp_path, changed_candidate
):
    from server import html_email_review
    from server.profile_content import scope_profile_html
    from server.faculty_extractor import FacultyCandidate

    root, attempt, audit, snapshot, media = source(tmp_path)
    old_html = page(98, 450, "邮箱：a@example.edu 完整履历。")
    snapshot.write_text(old_html, encoding="utf8")
    old_scope, old_e, _ = scope_profile_html(old_html, "article#profile", URL)
    issue = {
        "姓名": "王芳",
        "主页": URL,
        "status": "mailto_address_conflict",
        "matches": ["a@example.edu", "b@example.edu"],
    }
    row = {"姓名": "王芳", "主页": URL, "邮箱": "", "简介": ""}
    candidate = FacultyCandidate("王芳", URL)
    old_item = html_email_review.build_items(
        [issue], [row], [candidate], {URL: old_scope}
    )[0]
    html_email_review.save_decision(
        attempt / "html_email_reviews.json",
        old_item,
        "select",
        "a@example.edu",
        task_id=root.name,
        reviewed_by=7,
    )
    value = json.loads(audit.read_text(encoding="utf8"))
    value["html_email_review_items"] = [old_item]
    value["missing_profile_media_evidence"] = []
    value["coverage"]["field_sources"][0].update(old_e)
    audit.write_text(json.dumps(value), encoding="utf8")
    prepare_review_resume(audit, root.name)
    newer = page(13, 451, "邮箱：a@example.edu 完整履历。")
    scoped, evidence, _ = scope_profile_html(newer, "article#profile", URL)
    current_issue = (
        {**issue, "matches": ["a@example.edu", "c@example.edu"]}
        if changed_candidate
        else issue
    )
    remaining, pending, certificates = html_email_review.apply_decisions(
        [current_issue],
        [row],
        [candidate],
        {URL: scoped},
        attempt,
        raw_source_html={URL: newer},
        source_evidence={URL: evidence},
    )
    if changed_candidate:
        assert remaining and pending and not certificates
    else:
        assert not remaining and not pending and row["邮箱"] == "a@example.edu"
        assert certificates[0]["original_decision_id"] == old_item["id"]
