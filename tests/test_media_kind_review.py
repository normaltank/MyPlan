"""Uncertain, page-shaped photos must have an actionable evidence review."""
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from server import media_kind_review
from server.image_biography import audit_profile_media
from server.review_registry import has_review, review_complete
from server.task_image_review import carry_review_decisions


def test_unreadable_profile_image_is_routed_to_original_media_review(tmp_path):
    path = tmp_path / "audit.json"
    path.write_text(json.dumps({"unresolved": [
        {"status": "image_bio_ocr_incomplete", "kind": "image",
         "media_url": "https://faculty.example.edu/photo.jpg"}
    ]}), encoding="utf-8")
    assert has_review(path)


def unread_lines(_data, use_det=True, use_cls=True, use_rec=True):
    """OCR reads nothing, while the detector still outlines five text lines."""
    if not use_rec:
        return [[[80, y], [720, y], [720, y + 26], [80, y + 26]] for y in range(80, 260, 36)], None
    return [], None


async def ambiguous_image(tmp_path, *, pixel="white", decisions=None):
    data = BytesIO()
    Image.new("RGB", (1000, 1500), pixel).save(data, format="PNG")
    async def fetch(_url):
        return data.getvalue()
    return await audit_profile_media(
        {"https://faculty.example.edu/p": '<article><img src="/photo.png"></article>'},
        {"https://faculty.example.edu/p": {"张富有"}}, tmp_path / "media",
        fetch_asset=fetch, ocr_engine=unread_lines, media_kind_decisions_path=decisions)


def save_audit(tmp_path, result):
    path = tmp_path / "sample_audit.json"
    path.write_text(json.dumps({"image_biography_evidence": result["evidence"],
        "unresolved": result["unresolved"]}), encoding="utf-8")
    return path


@pytest.mark.asyncio
async def test_photo_decision_applies_only_to_same_profile_url_and_original(tmp_path):
    initial = await ambiguous_image(tmp_path)
    assert [item["status"] for item in initial["unresolved"]] == ["image_bio_ocr_incomplete"]
    audit = save_audit(tmp_path, initial)
    items = media_kind_review.collect_items(audit)
    assert len(items) == 1 and Image.open(BytesIO(items[0]["preview_bytes"])).size == (1000, 1500)
    decisions = tmp_path / media_kind_review.DECISIONS_FILE
    assert not review_complete(audit)
    media_kind_review.save_decision(decisions, items[0], "unreadable", 1)
    assert not review_complete(audit)
    media_kind_review.save_decision(decisions, items[0], "photo", 1)
    assert review_complete(audit)
    final = await ambiguous_image(tmp_path, decisions=decisions)
    assert final["unresolved"] == [] and final["biographies"] == {}
    assert final["evidence"][0]["media_kind_review"] == "photo"
    assert Path(final["evidence"][0]["original_path"]).is_file()
    changed = await ambiguous_image(tmp_path, pixel="#eeeeee", decisions=decisions)
    assert changed["unresolved"][0]["status"] == "image_bio_ocr_incomplete"
    assert not media_kind_review.photo_confirmed(decisions, "https://faculty.example.edu/other",
        items[0]["media_url"], items[0]["source_sha256"])


@pytest.mark.asyncio
async def test_media_review_rejects_changed_original_and_path_escape(tmp_path):
    audit = save_audit(tmp_path, await ambiguous_image(tmp_path))
    payload = json.loads(audit.read_text(encoding="utf-8"))
    original = Path(payload["image_biography_evidence"][0]["original_path"])
    original.write_bytes(b"changed")
    with pytest.raises(ValueError, match="original changed"):
        media_kind_review.collect_items(audit)
    payload["image_biography_evidence"][0]["original_path"] = str(tmp_path.parent / "foreign.png")
    audit.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="outside audit"):
        media_kind_review.collect_items(audit)


def test_unreadable_pdf_stays_blocked_by_media_review(tmp_path):
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"unresolved": [{"status": "image_bio_ocr_incomplete",
        "kind": "pdf", "media_url": "https://faculty.example.edu/cv.pdf"}]}), encoding="utf-8")
    assert not has_review(audit)
    assert not review_complete(audit)


@pytest.mark.asyncio
@pytest.mark.browser
async def test_media_review_buttons_execute_in_browser_without_ocr_handler_collision(tmp_path):
    from playwright.async_api import async_playwright
    from server.image_bio_review import render_review_page
    audit = save_audit(tmp_path, await ambiguous_image(tmp_path))
    items = media_kind_review.collect_items(audit)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            errors, submitted = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            async def response(route):
                submitted.append(route.request.post_data_json)
                await route.fulfill(status=204)
            await page.route("https://review.example.edu/decision", response)
            await page.goto("about:blank")
            await page.set_content(render_review_page([], media_kind_items=items,
                media_kind_decision_url="https://review.example.edu/decision"))
            await page.locator('.media-kind-card button[data-verdict="photo"]').click()
            await page.locator('.media-kind-card .status').filter(has_text="已确认非简历照片").wait_for()
            assert not errors and submitted == [{"id": items[0]["id"], "verdict": "photo"}]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_owner_scoped_media_decision_retry_carries_exact_evidence(tmp_path, monkeypatch):
    from server import app as web
    from server.access_control import AccountStore
    from server.agent_queue import AgentTaskQueue
    from test_authenticated_image_review import _client
    from fastapi.testclient import TestClient
    queue = AgentTaskQueue(tmp_path / "queue.db")
    store = AccountStore(queue.db_path)
    user = store.create_user("alice", "Alice", "alice password 12345")
    store.create_user("bob", "Bob", "bob password 12345")
    task = queue.submit_task("测试大学", "测试学院", "https://faculty.example.edu/", owner_user_id=user.id)
    out = tmp_path / "outputs"
    attempt = out / "tasks" / task.task_id / "attempt-1"
    attempt.mkdir(parents=True)
    audit = save_audit(attempt, await ambiguous_image(attempt))
    payload = json.loads(audit.read_text(encoding="utf-8"))
    payload.update(school=task.school, college=task.college, audit_path=str(audit))
    audit.write_text(json.dumps(payload), encoding="utf-8")
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "account_store", store)
    monkeypatch.setattr(web, "OUTPUT_DIR", str(out))
    owner, csrf = _client(store, "alice", "alice password 12345")
    other, other_csrf = _client(store, "bob", "bob password 12345")
    page = owner.get(f"/review/task/{task.task_id}")
    assert page.status_code == 200 and "原图类型复核" in page.text
    assert "非简历照片" in page.text and "data:image/png;base64," in page.text
    item = media_kind_review.collect_items(audit)[0]
    url = f"/api/task/{task.task_id}/review/media-kind"
    decision = {"id": item["id"], "verdict": "photo"}
    assert TestClient(web.app).post(url, json=decision).status_code == 401
    assert other.post(url, json=decision, headers={"X-CSRF-Token": other_csrf}).status_code == 404
    assert owner.post(url, json=decision).status_code == 403
    assert owner.post(url, json=decision, headers={"X-CSRF-Token": csrf}).status_code == 204
    retry = owner.post(f"/api/task/{task.task_id}/review/retry", headers={"X-CSRF-Token": csrf})
    assert retry.status_code == 200 and queue.get_task(task.task_id).status == "queued"
    target = attempt.parent / "attempt-2"
    carry_review_decisions(attempt.parent, target)
    assert media_kind_review.photo_confirmed(target / media_kind_review.DECISIONS_FILE,
        item["profile_url"], item["media_url"], item["source_sha256"])
