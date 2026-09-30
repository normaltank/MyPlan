"""Human source identity resolution survives receipt, publication and download audit."""
import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import identity_review, app as web
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue
from server.faculty_identity import identity_review_issues
from server.university_delivery import manifest_snapshot_is_safe, publish_delivery


@pytest.mark.browser
@pytest.mark.asyncio
async def test_identity_review_browser_sends_source_partition_and_escapes_biographies(tmp_path):
    from playwright.async_api import async_playwright
    from server.image_bio_review import render_review_page
    rows = records()
    rows[0]['简介'] = '</pre><script>window.leaked=true</script>完整原文'
    item = identity_review.pending_items(rows)[0]
    body = render_review_page([], identity_items=[item], csrf_token='fixture-csrf',
                              identity_decision_url='/identity')
    messages, errors = [], []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        async def route(request):
            if request.request.method == 'POST':
                messages.append(request.request.post_data_json)
                assert request.request.headers['x-csrf-token'] == 'fixture-csrf'
                await request.fulfill(json={'ok': True})
            else:
                await request.fulfill(body=body, content_type='text/html')
        await page.route('**/*', route)
        try:
            await page.goto('http://fixture.local/review')
            await page.locator('.identity-source select').nth(1).select_option(rows[0]['主页'])
            await page.locator('button[data-verdict="partition"]').click()
            await page.locator('.identity-card .status').filter(has_text='已保存身份决定').wait_for()
            assert messages[0]['assignments'] == dict.fromkeys([row['主页'] for row in rows], rows[0]['主页'])
            assert messages[0]['id'] == item['id'] and messages[0]['verdict'] == 'partition'
            assert await page.evaluate('window.leaked === undefined')
            assert not errors
            await page.screenshot(path=str(tmp_path / 'identity-review.png'), full_page=True)
        finally:
            await browser.close()


def records():
    return [{"序号": i, "姓名": "张富有", "职称": "教授", "邮箱": email,
             "学校": "甲大学", "学院": "甲学院", "采集人": "Fixture", "采集日期": "2026-09-30",
             "简介": bio, "主页": f"https://example.edu/p/{i}"}
            for i,email,bio in [(1,"zhangfuyou@example.edu","来源一完整履历与论文"),
                               (2,"zhangfuyou2@example.edu","来源二完整教学与项目")]]


def prepare(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    store = AccountStore(queue.db_path)
    alice = store.create_user("alice", "Alice", "alice password 12345")
    store.create_user("bob", "Bob", "bob password 12345")
    admin = store.create_user("admin", "Admin", "admin password 12345", "admin")
    task = queue.submit_task("甲大学", "甲学院", "https://example.edu/", owner_user_id=alice.id)
    claim = queue.claim_task("worker", task_id=task.task_id)
    queue.sync_task_result(task.task_id, [], {}, "", "身份待复核", worker_id="worker", lease_token=claim.lease_token)
    attempt = tmp_path / "outputs/tasks" / task.task_id / "attempt-1"
    attempt.mkdir(parents=True)
    audit = attempt / "fixture_audit.json"
    items = identity_review.pending_items(records(), {r["主页"]: "a" * 64 for r in records()})
    report = {"school": task.school, "college": task.college, "audit_path": str(audit),
              "unresolved": identity_review_issues(records()), "identity_review_items": items}
    audit.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return queue, store, admin, task, audit, items[0]


def client(store, username):
    browser = TestClient(web.app)
    login = browser.post("/api/auth/login", json={"username": username, "password": username + " password 12345"})
    assert login.status_code == 200
    return browser, {"X-CSRF-Token": browser.cookies.get("faculty_csrf")}


@pytest.mark.parametrize("verdict,expected", [("same", 1), ("distinct", 2), ("insufficient", 2)])
def test_review_applies_only_exact_original_evidence_and_preserves_full_biographies(tmp_path, verdict, expected):
    _queue, _store, admin, _task, audit, item = prepare(tmp_path)
    path = audit.parent / identity_review.DECISIONS_FILE
    identity_review.save_decision(path, item, verdict, item["observations"][0]["url"], reviewed_by=admin.id)
    hashes = {r["主页"]: "a" * 64 for r in records()}
    resolved, pending, certificates = identity_review.apply_decisions(records(), hashes, path)
    assert len(resolved) == expected
    assert bool(pending) == (verdict == "insufficient")
    if verdict == "same":
        assert resolved[0]["邮箱"] == "zhangfuyou@example.edu"
        assert all(r["简介"] in resolved[0]["简介"] for r in records())
    if certificates:
        pairs = identity_review.validated_pairs(resolved, certificates, path)
        assert not identity_review_issues(resolved, pairs)
    changed = records()
    changed[1]["简介"] += "新增经历"
    assert identity_review.apply_decisions(changed, hashes, path)[1]
    assert identity_review.apply_decisions(records(), {**hashes, records()[1]["主页"]: "b" * 64}, path)[1]


def test_partition_handles_two_people_with_one_alias_without_dropping_original_bio(tmp_path):
    rows = records()
    rows.append({**rows[0], "主页": "https://example.edu/p/3", "简介": "第三来源全文"})
    hashes = {r["主页"]: "a" * 64 for r in rows}
    item = identity_review.pending_items(rows, hashes)[0]
    path = tmp_path / "identity_reviews.json"
    mapping = {rows[0]["主页"]: rows[0]["主页"], rows[1]["主页"]: rows[1]["主页"], rows[2]["主页"]: rows[0]["主页"]}
    identity_review.save_decision(path, item, "partition", assignments=mapping, reviewed_by=1)
    resolved, pending, certificates = identity_review.apply_decisions(rows, hashes, path)
    assert len(resolved) == 2 and not pending
    assert rows[0]["简介"] in resolved[0]["简介"] and rows[2]["简介"] in resolved[0]["简介"]
    assert not identity_review_issues(resolved, identity_review.validated_pairs(resolved, certificates, path))
    with pytest.raises(ValueError):
        identity_review.save_decision(path, item, "partition", assignments={rows[0]["主页"]: rows[1]["主页"]}, reviewed_by=1)


def test_admin_review_owner_isolation_and_insufficient_decision_cannot_requeue(tmp_path, monkeypatch):
    queue, store, _admin, task, audit, item = prepare(tmp_path)
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "account_store", store)
    monkeypatch.setattr(web, "OUTPUT_DIR", str(tmp_path / "outputs"))
    owner, owner_headers = client(store, "alice")
    other, other_headers = client(store, "bob")
    admin, admin_headers = client(store, "admin")
    page = f"/review/task/{task.task_id}"
    decision = f"/api/task/{task.task_id}/review/identity"
    retry = f"/api/task/{task.task_id}/review/retry"
    payload = {"id": item["id"], "verdict": "distinct"}
    assert other.get(page).status_code == 404
    assert other.post(decision, json=payload, headers=other_headers).status_code == 404
    assert owner.get(page).status_code == 200
    assert owner.post(decision, json=payload, headers=owner_headers).status_code == 403
    html = admin.get(page).text
    assert "同名身份复核" in html and all(r["简介"] in html for r in records())
    assert admin.post(decision, json=payload).status_code == 403
    assert admin.post(decision, json={**payload, "verdict": "insufficient"}, headers=admin_headers).status_code == 204
    assert admin.post(retry, headers=admin_headers).status_code == 409
    assert admin.post(decision, json=payload, headers=admin_headers).status_code == 204
    assert owner.post(retry, headers=owner_headers).status_code == 200
    assert queue.get_task(task.task_id).owner_user_id == task.owner_user_id
    from server.task_image_review import carry_review_decisions
    next_attempt = audit.parent.parent / "attempt-2"
    carry_review_decisions(audit.parent.parent, next_attempt)
    assert (next_attempt / identity_review.DECISIONS_FILE).is_file()


def test_reviewed_homonyms_pass_receipt_and_version_audit_but_changed_or_forged_evidence_does_not(tmp_path):
    queue, _store, admin, task, audit, item = prepare(tmp_path)
    path = audit.parent / identity_review.DECISIONS_FILE
    identity_review.save_decision(path, item, "distinct", reviewed_by=admin.id)
    hashes = {r["主页"]: "a" * 64 for r in records()}
    rows, pending, certificates = identity_review.apply_decisions(records(), hashes, path)
    assert not pending
    assert queue.retry_failed_review_task(task.task_id)
    claim = queue.claim_task("worker", task_id=task.task_id)
    stats = {"total": 2, "with_email": 2, "no_email": 0, "identity_reviews": certificates,
             "identity_review_path": str(path), "audit_path": str(audit)}
    forged = copy.deepcopy(stats)
    forged["identity_reviews"][0]["result_sha256s"] = ["b" * 64]
    with pytest.raises(ValueError, match="identity"):
        queue.complete_task(task.task_id, rows, forged, worker_id="worker", lease_token=claim.lease_token)
    queue.complete_task(task.task_id, rows, stats, worker_id="worker", lease_token=claim.lease_token)
    result = publish_delivery(queue, task.batch_id, task.school, tmp_path / "outputs")
    assert result["delivery_scope"]["state"] == "ready" and result["total"] == 2
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest_snapshot_is_safe(queue, manifest)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["decisions"][0]["item"]["observations"][0]["record"]["简介"] += "来源已变化"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert not manifest_snapshot_is_safe(queue, manifest)
