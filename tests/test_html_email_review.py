"""Ordinary HTML e-mail conflicts: evidence-bound human decisions and deterministic resume.

Every page is served from this file (browser routing or saved HTML); no school
site and no model is contacted. Addresses and names are synthetic.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import app as web
from server import faculty_extractor, html_email_review
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue
from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline
from server.review_registry import review_complete
from server.task_image_review import carry_review_decisions

ROOT = "https://review.example.edu"
PLAN = {"school": "样本大学", "college": "样本学院", "source_kind": "university_faculty_roster"}
ROSTER = "<main><ul><li><a href='/person'>王晓岚</a><span>教授</span></li></ul></main>"
BIO = "<h1>王晓岚</h1><p>教授，主要从事数据库研究。</p><p>2010年获博士学位。</p>"
# Neither address is on the school domain, so the labelled pair stays for review.
TWO_LABELS = "<p>邮箱：sunny2008@qq.com</p><p>邮箱：wangxiaolan@163.com</p>"


def profile(contacts: str) -> str:
    return "<article id='profile'>" + BIO + contacts + "</article><footer>学院版权所有</footer>"


@pytest.fixture(autouse=True)
def no_production_wrapper_cache(monkeypatch):
    monkeypatch.setattr(faculty_extractor, "get_default_l1_executor", lambda: None)


async def _run(out: Path, pages: dict[str, str]):
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import run_plan

    plan = {**PLAN, "branches": [{"url": ROOT + "/roster", "single_page": True,
                                  "card_selector": "main li", "profile_content_selector": "#profile"}]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context()

            async def route(request):
                body = pages.get(request.request.url)
                if body is None:
                    await request.abort()
                else:
                    await request.fulfill(body=body, content_type="text/html; charset=utf-8")

            await context.route("**/*", route)
            report = await run_plan(plan, context, out)
        finally:
            await browser.close()
    return report, json.loads(Path(report["records_path"]).read_text(encoding="utf-8"))


def _decide(attempt: Path, task_id: str, report: dict, verdict: str, email: str = "") -> dict:
    item = report["html_email_review_items"][0]
    return html_email_review.save_decision(attempt / html_email_review.DECISIONS_FILE, item, verdict, email,
                                           task_id=task_id, reviewed_by=7)


@pytest.mark.asyncio
@pytest.mark.browser
async def test_two_labelled_addresses_block_then_reviewed_choice_resumes_delivery(tmp_path):
    task_root = tmp_path / "tasks" / "task-1"
    first = task_root / "attempt-1"
    pages = {ROOT + "/roster": ROSTER, ROOT + "/person": profile(TWO_LABELS)}
    report, records = await _run(first, pages)
    assert report["delivery_ready"] is False
    assert [item["status"] for item in report["unresolved"]] == ["ambiguous_email_assignment"]
    assert records[0]["邮箱"] == "" and "2010年获博士学位。" in records[0]["简介"]
    [item] = report["html_email_review_items"]
    assert item["name"] == "王晓岚" and item["url"] == ROOT + "/person" and item["source_sha256"]
    assert [(o["email"], o["selectable"]) for o in item["candidates"]] == [
        ("sunny2008@qq.com", True), ("wangxiaolan@163.com", True)]
    assert all("邮箱：" in o["page_context"][0] for o in item["candidates"])
    # Both addresses are labelled on the teacher's own page: nothing shows the field is empty.
    assert item["none_allowed"] is False
    with pytest.raises(ValueError):
        _decide(first, "task-1", report, "none")
    with pytest.raises(ValueError):
        _decide(first, "task-1", report, "select", "guess@example.edu")
    assert review_complete(Path(report["audit_path"])) is False
    _decide(first, "task-1", report, "select", "wangxiaolan@163.com")
    assert review_complete(Path(report["audit_path"])) is True

    (task_root / "review-resume.json").write_text(json.dumps(
        {"task_id": "task-1", "audit_path": report["audit_path"]}), encoding="utf-8")
    second = task_root / "attempt-2"
    carry_review_decisions(task_root, second)
    resumed, records = await _run(second, pages)
    assert resumed["delivery_ready"] is True, resumed["unresolved"]
    assert resumed["unresolved"] == [] and resumed["html_email_review_items"] == []
    assert len(records) == 1 and records[0]["邮箱"] == "wangxiaolan@163.com"
    assert "2010年获博士学位。" in records[0]["简介"] and "学院版权所有" not in records[0]["简介"]
    assert resumed["html_email_reviews"][0]["reviewed_by"] == 7
    assert (resumed["total"], resumed["with_email"], resumed["no_email"]) == (1, 1, 0)


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize("changed_page", [
    profile(TWO_LABELS + "<p>2026年新增：获省级教学奖。</p>"),  # evidence version changed
    profile(TWO_LABELS + "<p>邮箱：wxl@126.com</p>"),   # candidate set changed
], ids=["source_changed", "candidates_changed"])
async def test_stale_decision_does_not_release_changed_evidence(tmp_path, changed_page):
    task_root = tmp_path / "tasks" / "task-1"
    pages = {ROOT + "/roster": ROSTER, ROOT + "/person": profile(TWO_LABELS)}
    report, _ = await _run(task_root / "attempt-1", pages)
    _decide(task_root / "attempt-1", "task-1", report, "select", "wangxiaolan@163.com")
    (task_root / "review-resume.json").write_text(json.dumps(
        {"task_id": "task-1", "audit_path": report["audit_path"]}), encoding="utf-8")
    carry_review_decisions(task_root, task_root / "attempt-2")
    resumed, records = await _run(task_root / "attempt-2", {**pages, ROOT + "/person": changed_page})
    assert resumed["delivery_ready"] is False and records[0]["邮箱"] == ""
    assert resumed["html_email_reviews"] == []
    assert resumed["html_email_review_items"][0]["id"] != report["html_email_review_items"][0]["id"]


@pytest.mark.asyncio
@pytest.mark.browser
async def test_decision_from_another_task_is_never_reused(tmp_path):
    pages = {ROOT + "/roster": ROSTER, ROOT + "/person": profile(TWO_LABELS)}
    own = tmp_path / "tasks" / "task-1" / "attempt-1"
    report, _ = await _run(own, pages)
    _decide(own, "task-1", report, "select", "wangxiaolan@163.com")
    other = tmp_path / "tasks" / "task-2" / "attempt-1"
    other.mkdir(parents=True)
    shutil.copyfile(own / html_email_review.DECISIONS_FILE, other / html_email_review.DECISIONS_FILE)
    foreign, records = await _run(other, pages)
    assert foreign["delivery_ready"] is False and records[0]["邮箱"] == ""
    assert foreign["html_email_review_items"][0]["id"] == report["html_email_review_items"][0]["id"]


SHARED = "https://cs.univ.edu.cn/all.htm"
SHARED_PAGE = ("<html><body><div class='list'>"
               "<div class='card'><h3>王晓岚</h3><p>教授 邮箱：sunflower88@163.com</p></div>"
               "<div class='card'><h3>李强</h3><p>副教授 邮箱：cs-team@univ.edu.cn</p></div>"
               "<div class='card'><h3>赵敏</h3><p>讲师 邮箱：cs-team@univ.edu.cn</p></div>"
               "</div></body></html>")


def _shared_harvest():
    candidates = [FacultyCandidate(name="王晓岚", url=SHARED, card_context="王晓岚 教授 邮箱：sunflower88@163.com"),
                  FacultyCandidate(name="李强", url=SHARED, card_context="李强 副教授 邮箱：cs-team@univ.edu.cn"),
                  FacultyCandidate(name="赵敏", url=SHARED, card_context="赵敏 讲师 邮箱：cs-team@univ.edu.cn")]
    result = FacultyHarvestPipeline().harvest_candidates(
        candidates=candidates, profile_fetcher=lambda url: SHARED_PAGE, base_url="https://cs.univ.edu.cn/",
        school="测试大学", college="计科院")
    return candidates, result["sheet1_with_email"] + result["sheet2_no_email"], result["unresolved"]


def test_public_address_cannot_be_assigned_but_empty_field_can_be_confirmed(tmp_path):
    candidates, records, unresolved = _shared_harvest()
    unrelated = {"姓名": "李强", "主页": SHARED, "status": "title_field_conflict", "matches": ["教授", "副教授"]}
    unresolved = [*unresolved, unrelated]
    items = html_email_review.build_items(unresolved, records, candidates, {SHARED: SHARED_PAGE})
    assert sorted(item["name"] for item in items) == ["李强", "赵敏"]
    for item in items:
        [option] = item["candidates"]
        assert option["email"] == "cs-team@univ.edu.cn" and option["selectable"] is False
        assert "shared_contact" in option["reasons"] and item["none_allowed"] is True
        with pytest.raises(ValueError):
            html_email_review.save_decision(tmp_path / "attempt-1" / html_email_review.DECISIONS_FILE, item,
                                            "select", "cs-team@univ.edu.cn", task_id="t", reviewed_by=1)
    attempt = tmp_path / "tasks" / "t" / "attempt-1"
    html_email_review.save_decision(attempt / html_email_review.DECISIONS_FILE, items[0], "none",
                                    task_id="t", reviewed_by=1)
    remaining, pending, certificates = html_email_review.apply_decisions(
        unresolved, records, candidates, {SHARED: SHARED_PAGE}, attempt)
    assert [c["name"] for c in certificates] == [items[0]["name"]] and len(pending) == 1
    # The other teacher's conflict and every non-e-mail issue still block delivery.
    assert unrelated in remaining
    assert any(issue["status"] == "ambiguous_shared_email" and issue["姓名"] == pending[0]["name"]
               for issue in remaining)
    assert not any(issue["status"] == "ambiguous_shared_email" and issue["姓名"] == items[0]["name"]
                   for issue in remaining)
    emails = {row["姓名"]: row["邮箱"] for row in records}
    assert emails == {"王晓岚": "sunflower88@163.com", "李强": "", "赵敏": ""}


def test_address_spelling_a_colleague_cannot_be_given_to_the_card_owner(tmp_path):
    candidates = [FacultyCandidate(name="王晓岚", url=SHARED, card_context="王晓岚 教授 liqiang@univ.edu.cn"),
                  FacultyCandidate(name="李强", url=SHARED, card_context="李强 副教授")]
    page = ("<div><div class='card'><h3>王晓岚</h3><p>教授 liqiang@univ.edu.cn</p></div>"
            "<div class='card'><h3>李强</h3><p>副教授</p></div></div>")
    records = [{"姓名": c.name, "主页": SHARED, "邮箱": ""} for c in candidates]
    issue = {"姓名": "王晓岚", "主页": SHARED, "status": "conflicting_card_email", "email": "liqiang@univ.edu.cn"}
    [item] = html_email_review.build_items([issue], records, candidates, {SHARED: page})
    [option] = item["candidates"]
    assert option["selectable"] is False and option["spells_others"] == ["李强"]
    assert item["none_allowed"] is True


def _web_fixture(tmp_path, monkeypatch, audit_payload):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    store = AccountStore(queue.db_path)
    alice = store.sign_in("alice")
    store.sign_in("bob")
    task = queue.submit_task("样本大学", "样本学院", ROOT, owner_user_id=alice.id)
    attempt = tmp_path / "outputs" / "tasks" / task.task_id / "attempt-1"
    attempt.mkdir(parents=True)
    audit = attempt / "sample_audit.json"
    audit.write_text(json.dumps({"school": task.school, "college": task.college, "audit_path": str(audit),
                                 **audit_payload}, ensure_ascii=False), encoding="utf-8")
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',error='审计未通过交付门禁' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "account_store", store)
    monkeypatch.setattr(web, "OUTPUT_DIR", str(tmp_path / "outputs"))
    return queue, task, audit


def _client(name):
    client = TestClient(web.app)
    assert client.post("/api/auth/name", json={"display_name": name}).status_code == 200
    return client, {"X-CSRF-Token": client.get("/api/auth/me").json()["csrf_token"]}


def _saved_conflict_audit():
    candidates = [FacultyCandidate(name="王晓岚", url=ROOT + "/person", card_context="王晓岚 教授")]
    page = profile(TWO_LABELS + "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>")
    records = [{"姓名": "王晓岚", "主页": ROOT + "/person", "邮箱": "", "简介": "原文"}]
    issue = {"姓名": "王晓岚", "主页": ROOT + "/person", "status": "ambiguous_email_assignment",
             "matches": ["sunny2008@qq.com", "wangxiaolan@example.edu"]}
    items = html_email_review.build_items([issue], records, candidates, {ROOT + "/person": page})
    return {"unresolved": [issue], "html_email_review_items": items}


def test_web_review_is_shared_evidence_bound_and_resumes_one_task(tmp_path, monkeypatch):
    queue, task, audit = _web_fixture(tmp_path, monkeypatch, _saved_conflict_audit())
    owner, headers = _client("alice")
    other, _ = _client("bob")
    page_url = f"/review/task/{task.task_id}"
    save_url = f"/api/task/{task.task_id}/review/html-email"
    retry_url = f"/api/task/{task.task_id}/review/retry"
    assert TestClient(web.app).get(page_url).status_code == 401
    assert other.get(page_url).status_code == 200
    page = owner.get(page_url)
    assert page.status_code == 200 and "邮箱归属复核：王晓岚" in page.text
    assert "邮箱：sunny2008@qq.com" in page.text and "<script>alert" not in page.text
    assert "确认此教师无个人邮箱" not in page.text
    assert owner.get(f"/api/task/{task.task_id}").json()["review_url"] == page_url
    item_id = html_email_review.collect_items(audit)[0]["id"]
    choice = {"id": item_id, "verdict": "select", "selected_email": "wangxiaolan@example.edu"}
    assert other.post(save_url, json=choice, headers=headers).status_code == 403
    assert owner.post(save_url, json=choice).status_code == 403
    assert owner.post(save_url, json={**choice, "selected_email": "typed@example.edu"},
                      headers=headers).status_code == 409
    assert owner.post(save_url, json={**choice, "verdict": "none", "selected_email": ""},
                      headers=headers).status_code == 409
    assert owner.post(save_url, json={**choice, "id": "0" * 64}, headers=headers).status_code == 404
    assert owner.post(retry_url, headers=headers).status_code == 409
    # Saving again replaces the earlier choice for the same evidence; it is not duplicated.
    assert owner.post(save_url, json={**choice, "selected_email": "sunny2008@qq.com"},
                      headers=headers).status_code == 204
    assert owner.post(save_url, json=choice, headers=headers).status_code == 204
    saved = json.loads((audit.parent / html_email_review.DECISIONS_FILE).read_text(encoding="utf-8"))
    assert [(d["email"], d["task_id"]) for d in saved["decisions"]] == [("wangxiaolan@example.edu", task.task_id)]
    assert "已保存邮箱：wangxiaolan@example.edu" in owner.get(page_url).text
    assert owner.post(retry_url, headers=headers).status_code == 200
    current = queue.get_task(task.task_id)
    assert current.status == "queued" and current.owner_user_id is not None
    # Once requeued the review is closed; repeating the retry or a save cannot race the worker.
    assert owner.post(retry_url, headers=headers).status_code == 409
    assert owner.post(save_url, json=choice, headers=headers).status_code == 409
    carry_review_decisions(audit.parent.parent, audit.parent.parent / "attempt-2")
    assert (audit.parent.parent / "attempt-2" / html_email_review.DECISIONS_FILE).is_file()


def test_tampered_evidence_is_rejected(tmp_path, monkeypatch):
    payload = _saved_conflict_audit()
    payload["html_email_review_items"][0]["candidates"][0]["selectable"] = True
    payload["html_email_review_items"][0]["candidates"].append(
        {"email": "typed@example.edu", "selectable": True, "reasons": [], "spells_teacher": False,
         "spells_others": [], "card_context": [], "page_context": []})
    _, task, _ = _web_fixture(tmp_path, monkeypatch, payload)
    owner, _ = _client("alice")
    assert owner.get(f"/review/task/{task.task_id}").status_code == 409


def test_old_audit_without_evidence_can_only_rerun_to_regenerate_it(tmp_path, monkeypatch):
    issue = {"姓名": "王晓岚", "主页": ROOT + "/person", "status": "ambiguous_email_assignment",
             "matches": ["sunny2008@qq.com", "wangxiaolan@example.edu"]}
    queue, task, audit = _web_fixture(tmp_path, monkeypatch, {"unresolved": [issue]})
    owner, headers = _client("alice")
    assert owner.get(f"/review/task/{task.task_id}").status_code == 200
    assert owner.post(f"/api/task/{task.task_id}/review/retry", headers=headers).status_code == 200
    assert queue.get_task(task.task_id).status == "queued"


def test_new_audit_with_unreviewable_conflict_cannot_be_released(tmp_path, monkeypatch):
    issue = {"姓名": "王晓岚", "主页": ROOT + "/person", "status": "ambiguous_email_assignment",
             "matches": ["sunny2008@qq.com"]}
    _, task, _ = _web_fixture(tmp_path, monkeypatch, {"unresolved": [issue], "html_email_review_items": []})
    owner, headers = _client("alice")
    assert owner.post(f"/api/task/{task.task_id}/review/retry", headers=headers).status_code == 409


# Shapes observed on live saved pages (2026-10-01 case library), names and addresses replaced.
OWN = "https://maths.example.edu/p/zhangzhengming.htm"
PEER = "https://maths.example.edu/p/zhanghuaming.htm"
GROUP = "https://maths.example.edu/group/physics.htm"


def test_own_labelled_address_spelling_an_unnamed_colleague_stays_selectable():
    # The address happens to spell a colleague too, but the colleague appears
    # nowhere in this teacher's card or page: spelling alone is not a hard gate.
    candidates = [FacultyCandidate(name="张正明", url=OWN, card_context="张正明 副教授 zhmzhang@example.edu"),
                  FacultyCandidate(name="张华明", url=PEER, card_context="张华明 教授")]
    pages = {OWN: "<main><h1>张正明</h1><ul><li>电子邮箱： zhmzhang@example.edu</li></ul></main>",
             PEER: "<main><h1>张华明</h1><p>从事几何研究。</p></main>"}
    records = [{"姓名": c.name, "主页": c.url, "邮箱": ""} for c in candidates]
    issue = {"姓名": "张正明", "主页": OWN, "status": "ambiguous_email_assignment", "matches": ["zhmzhang@example.edu"]}
    [item] = html_email_review.build_items([issue], records, candidates, pages)
    [option] = item["candidates"]
    assert option["selectable"] is True and option["spells_others"] == []
    assert "spells_other_teacher" not in option["reasons"] and item["none_allowed"] is False
    # Named on the teacher's own page, the colleague's spelling still disqualifies the address.
    pages[OWN] = pages[OWN].replace("</ul>", "</ul><p>合作者：张华明</p>")
    [item] = html_email_review.build_items([issue], records, candidates, pages)
    assert item["candidates"][0]["selectable"] is False and item["none_allowed"] is True


def test_group_page_linked_as_one_profile_is_not_another_teachers_own_scope():
    # One roster link points at a group listing that repeats every member's address.
    candidates = [FacultyCandidate(name="周立新", url=OWN, card_context="周立新 教授 lxzhou@example.edu"),
                  FacultyCandidate(name="杨成峰", url=GROUP, card_context="杨成峰 助理教授"),
                  FacultyCandidate(name="许文海", url=PEER, card_context="许文海 讲师")]
    pages = {OWN: "<main><h1>周立新</h1><p>lxzhou@example.edu</p></main>",
             GROUP: ("<main><div>周立新 lxzhou@example.edu</div><div>杨成峰</div>"
                     "<div>许文海 whxu@example.edu</div></main>"),
             PEER: "<main><h1>许文海</h1></main>"}
    records = [{"姓名": c.name, "主页": c.url, "邮箱": ""} for c in candidates]
    issue = {"姓名": "周立新", "主页": OWN, "status": "ambiguous_email_assignment", "matches": ["lxzhou@example.edu"]}
    [item] = html_email_review.build_items([issue], records, candidates, pages)
    assert item["candidates"][0]["selectable"] is True, item["candidates"][0]["reasons"]
    # A second real profile showing the address still marks it as someone else's.
    pages[PEER] = "<main><h1>许文海</h1><p>lxzhou@example.edu</p></main>"
    [item] = html_email_review.build_items([issue], records, candidates, pages)
    assert "in_other_teacher_scope" in item["candidates"][0]["reasons"]
