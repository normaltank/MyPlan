"""Rare-surname names are neither admitted nor dropped: a person confirms them."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from server import app as web
from server import name_review
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue
from server.faculty_extractor import FacultyDirectoryExtractor
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
from server.image_bio_review import render_review_page
from server.task_image_review import (carry_review_decisions, has_image_review, name_items,
                                      review_complete)
import iso_mining

ROSTER_URL = "https://cs.x.edu.cn/list.htm"
BRANCH = {"url": ROSTER_URL, "card_selector": "#roster li"}
NAMES = ["王建国", "李明", "张伟", "刘洋", "阚文涛"]


def _roster(names=NAMES, linked=False):
    cards = "".join(
        f'<li class="t"><a href="/t/{i}.htm">{name}</a><span>教授</span></li>' if linked else
        f'<li class="t"><span class="n">{name}</span><span>教授</span><p>研究方向：数据库</p></li>'
        for i, name in enumerate(names))
    return f'<html><body><div class="nav"><a href="/">首页</a></div><ul id="roster">{cards}</ul></body></html>'


@pytest.mark.asyncio
@pytest.mark.parametrize("unresolved, recon_calls", [
    ([{"status": name_review.STATUS, "url": ROSTER_URL}], 1),
    ([{"status": name_review.STATUS, "url": ROSTER_URL},
      {"status": "card_selector_empty", "url": ROSTER_URL}], 3),
])
async def test_name_review_alone_does_not_spend_structure_repair(tmp_path, monkeypatch,
                                                                  unresolved, recon_calls):
    from types import SimpleNamespace
    from server import agent_recon, agent_worker
    worker = agent_worker.AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    task = SimpleNamespace(school="University", college="College", url=ROSTER_URL)
    plan = {"school": task.school, "college": task.college, "source_kind": "university_faculty_roster",
            "branches": [{"url": ROSTER_URL, "card_selector": "#roster li", "single_page": True}]}
    calls = []

    async def recon(*args, **kwargs):
        calls.append(1)
        branch = {**plan["branches"][0], "card_selector": f"#roster li:nth-of-type(n+{len(calls)})"}
        return agent_recon.ReconResult({**plan, "branches": [branch]}, 0, "completed", "fixture")

    async def collect(*args):
        return {"delivery_ready": False, "unresolved": unresolved}

    async def keep_all(*args):
        return None

    monkeypatch.setattr(agent_worker, "reconnoiter", recon)
    monkeypatch.setattr(worker, "_run_browser_plan", collect)
    monkeypatch.setattr(worker, "_unaffected_branches", keep_all)
    result = await worker._harvest_with_repair(task, tmp_path / "out")
    assert not result["delivery_ready"] and len(calls) == recon_calls


@pytest.mark.parametrize("text, expected", [
    ("阚文涛", True), ("百里明华", True), ("宿舍", True),
    ("张伟", False), ("欧阳明", False), ("Smith", False), ("阚", False), ("阚2", False),
])
def test_rare_surname_name(text, expected):
    assert iso_mining.rare_surname_name(text) is expected


def test_rare_tier_does_not_touch_the_admission_prior():
    # Scoring is unchanged, so roster mining picks the same groups as before.
    assert iso_mining.name_prob("阚文涛") == 0.08
    assert iso_mining.name_prob("张伟") > 0.5


def test_rare_surname_file_excludes_common_surnames_and_corpus_typos():
    common = set(iso_mining._SURNAMES) | set(iso_mining._COMPOUND)
    rare = iso_mining._RARE_SINGLE | iso_mining._RARE_COMPOUND
    assert not rare & common
    assert not rare & {"殴阳", "冷狐", "夏候", "曽"}
    assert {"阚", "权", "仝", "冼"} <= iso_mining._RARE_SINGLE
    assert "百里" in iso_mining._RARE_COMPOUND


def test_unlinked_rare_name_goes_to_review_and_plain_extract_is_unchanged():
    found, review = FacultyDirectoryExtractor.extract_with_review(_roster(), ROSTER_URL)
    assert [c.name for c in found] == NAMES[:4]
    assert [c.name for c in review] == ["阚文涛"]
    assert [c.name for c in FacultyDirectoryExtractor.extract(_roster(), ROSTER_URL)] == NAMES[:4]


def test_linked_cards_with_trusted_peers_still_admit_rare_names_directly():
    found, review = FacultyDirectoryExtractor.extract_with_review(_roster(linked=True), ROSTER_URL)
    assert [c.name for c in found] == NAMES and review == []


def _reconcile(decisions=None):
    return reconcile_rosters([CapturedRosterPage(ROSTER_URL, _roster(), BRANCH)], {},
                             name_decisions=decisions)


def test_undecided_name_blocks_with_its_own_status_not_a_card_mismatch():
    result = _reconcile()
    assert [c.name for c in result.candidates] == NAMES[:4]
    statuses = [item["status"] for item in result.unresolved]
    assert statuses == [name_review.STATUS]
    item = result.unresolved[0]
    assert item["name"] == "阚文涛" and item["url"] == ROSTER_URL and "阚文涛" in item["card_text"]
    assert result.observations[0]["visible_cards"] == 5


def test_confirmed_name_is_admitted_and_rejected_name_leaves_the_card_unexplained(tmp_path):
    item = _reconcile().unresolved[0]
    path = tmp_path / name_review.DECISIONS_FILE
    name_review.save_decision(path, item, "person", "阚文涛")
    confirmed = _reconcile(name_review.load_decisions(path))
    assert sorted(c.name for c in confirmed.candidates) == sorted(NAMES)
    assert confirmed.unresolved == []

    name_review.save_decision(path, item, "not_person")
    rejected = _reconcile(name_review.load_decisions(path))
    assert [c.name for c in rejected.candidates] == NAMES[:4]
    assert [u["status"] for u in rejected.unresolved] == ["roster_card_mismatch"]


def test_decision_binds_to_the_exact_card_evidence(tmp_path):
    item = _reconcile().unresolved[0]
    path = tmp_path / name_review.DECISIONS_FILE
    name_review.save_decision(path, item, "person", "阚文涛")
    changed = _roster().replace("研究方向：数据库</p></li></ul>", "研究方向：网络</p></li></ul>")
    result = reconcile_rosters([CapturedRosterPage(ROSTER_URL, changed, BRANCH)], {},
                               name_decisions=name_review.load_decisions(path))
    assert [u["status"] for u in result.unresolved] == [name_review.STATUS]


def test_invalid_decisions_are_refused(tmp_path):
    item = _reconcile().unresolved[0]
    path = tmp_path / name_review.DECISIONS_FILE
    with pytest.raises(ValueError):
        name_review.save_decision(path, item, "maybe")
    with pytest.raises(ValueError):
        name_review.save_decision(path, item, "person", "  ")
    with pytest.raises(ValueError):
        name_review.save_decision(path, item, "person", "阚文\n涛")


def _audit(root: Path, task, unresolved) -> Path:
    attempt = root / "tasks" / task.task_id / "attempt-1"
    attempt.mkdir(parents=True)
    audit = attempt / "sample_audit.json"
    audit.write_text(json.dumps({"school": task.school, "college": task.college,
                                 "audit_path": str(audit), "unresolved": unresolved},
                                ensure_ascii=False), encoding="utf-8")
    return audit


def test_review_page_lists_the_name_with_its_evidence_and_safe_links(tmp_path):
    item = dict(_reconcile().unresolved[0], profile_url="javascript:alert(1)")
    item["id"] = name_review.review_id(item["url"], item["profile_url"], item["name"], item["card_text"])
    html = render_review_page([], [], name_items=[item], name_decision_url="/api/x/review/name")
    assert "姓名待确认：阚文涛" in html and "研究方向：数据库" in html
    assert 'href="https://cs.x.edu.cn/list.htm"' in html
    assert 'href="javascript' not in html
    assert "/api/x/review/name" in html


def test_tampered_review_item_is_rejected(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    task = queue.submit_task("甲大学", "甲学院", "https://a.edu")
    item = dict(_reconcile().unresolved[0], name="王建国")
    audit = _audit(tmp_path / "outputs", task, [item])
    with pytest.raises(ValueError):
        name_items(audit)


def test_name_review_through_the_web_and_retry(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    store = AccountStore(queue.db_path)
    alice = store.create_user("alice", "Alice", "alice password 12345")
    store.create_user("bob", "Bob", "bob password 12345")
    task = queue.submit_task("甲大学", "甲学院", "https://a.edu", owner_user_id=alice.id)
    item = _reconcile().unresolved[0]
    audit = _audit(tmp_path / "outputs", task, [item])
    assert has_image_review(audit) and not review_complete(audit, [], [], name_items(audit))
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',error='审计未通过交付门禁' WHERE task_id=?",
                    (task.task_id,))
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "account_store", store)
    monkeypatch.setattr(web, "OUTPUT_DIR", str(tmp_path / "outputs"))

    def login(name, password):
        client = TestClient(web.app)
        assert client.post("/api/auth/login", json={"username": name, "password": password}).status_code == 200
        return client, {"X-CSRF-Token": client.get("/api/auth/me").json()["csrf_token"]}

    owner, headers = login("alice", "alice password 12345")
    other, other_headers = login("bob", "bob password 12345")
    page = owner.get(f"/review/task/{task.task_id}")
    assert page.status_code == 200 and "姓名待确认：阚文涛" in page.text
    assert owner.get(f"/api/task/{task.task_id}").json()["review_url"] == f"/review/task/{task.task_id}"
    url = f"/api/task/{task.task_id}/review/name"
    retry = f"/api/task/{task.task_id}/review/retry"
    assert owner.post(retry, headers=headers).status_code == 409
    payload = {"id": item["id"], "verdict": "person", "verified_name": "阚文涛"}
    assert other.post(url, json=payload, headers=other_headers).status_code == 404
    assert owner.post(url, json=payload).status_code == 403
    assert owner.post(url, json={**payload, "verdict": "maybe"}, headers=headers).status_code == 409
    assert owner.post(url, json=payload, headers=headers).status_code == 204
    assert owner.post(retry, headers=headers).status_code == 200
    assert queue.get_task(task.task_id).status == "queued"

    next_attempt = audit.parent.parent / "attempt-2"
    carry_review_decisions(audit.parent.parent, next_attempt)
    decisions = name_review.load_decisions(next_attempt / name_review.DECISIONS_FILE)
    assert decisions[item["id"]]["verified_name"] == "阚文涛"
