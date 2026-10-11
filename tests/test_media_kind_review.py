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
            await page.locator('.media-kind-card .status').filter(has_text="已保存：仅为人物照片，不含简介或邮箱").wait_for()
            assert "可选择其他选项更改" in await page.locator('.media-kind-card .status').inner_text()
            assert not errors and submitted == [{"id": items[0]["id"], "verdict": "photo"}]
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_media_decision_retry_carries_exact_evidence(tmp_path, monkeypatch):
    from server import app as web
    from server.access_control import AccountStore
    from server.agent_queue import AgentTaskQueue
    from test_authenticated_image_review import _client
    from fastapi.testclient import TestClient
    queue = AgentTaskQueue(tmp_path / "queue.db")
    store = AccountStore(queue.db_path)
    user = store.sign_in("alice")
    store.sign_in("bob")
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
    owner, csrf = _client(store, "alice")
    other, _ = _client(store, "bob")
    page = owner.get(f"/review/task/{task.task_id}")
    assert page.status_code == 200 and "原图类型复核" in page.text
    assert "纯人物照片" in page.text and "data:image/png;base64," in page.text
    item = media_kind_review.collect_items(audit)[0]
    url = f"/api/task/{task.task_id}/review/media-kind"
    decision = {"id": item["id"], "verdict": "photo"}
    assert TestClient(web.app).post(url, json=decision).status_code == 401
    assert other.post(url, json=decision, headers={"X-CSRF-Token": csrf}).status_code == 403
    assert owner.post(url, json=decision).status_code == 403
    assert owner.post(url, json=decision, headers={"X-CSRF-Token": csrf}).status_code == 204
    retry = owner.post(f"/api/task/{task.task_id}/review/retry", headers={"X-CSRF-Token": csrf})
    assert retry.status_code == 200 and queue.get_task(task.task_id).status == "queued"
    target = attempt.parent / "attempt-2"
    carry_review_decisions(attempt.parent, target)
    assert media_kind_review.photo_confirmed(target / media_kind_review.DECISIONS_FILE,
        item["profile_url"], item["media_url"], item["source_sha256"])


def ocr_lines(lines):
    def ocr(_data, use_det=True, use_cls=True, use_rec=True):
        return [([[5, 5 + index * 40], [150, 5 + index * 40], [150, 30 + index * 40], [5, 30 + index * 40]],
                 value, confidence) for index, (value, confidence) in enumerate(lines)], None
    return ocr


async def artwork_image(tmp_path, *, lines, pixel="white", decisions=None):
    data = BytesIO()
    Image.new("RGB", (400, 600), pixel).save(data, format="PNG")
    async def fetch(_url):
        return data.getvalue()
    return await audit_profile_media(
        {"https://faculty.example.edu/p": '<article><img src="/art.png"></article>'},
        {"https://faculty.example.edu/p": {"张富有"}}, tmp_path / "media",
        fetch_asset=fetch, ocr_engine=ocr_lines(lines), media_kind_decisions_path=decisions)


@pytest.mark.asyncio
@pytest.mark.parametrize("lines, status", [
    # A signature line OCR could not read on the teacher's own page.
    ((("张富有 教授", .99), ("完整学习经历与任职信息以及研究方向", .98), ("无字", .55)), "image_bio_low_confidence"),
    # Calligraphy whose characters name nobody on the page.
    (tuple((ch * 30, .9) for ch in "幼学壮行"), "image_bio_owner_unresolved"),
])
async def test_irrelevant_image_settles_ocr_review_and_drops_only_that_original(tmp_path, lines, status):
    from server.image_bio_review import collect_review_items, text_review_complete
    first = await artwork_image(tmp_path, lines=lines)
    assert [issue["status"] for issue in first["unresolved"]] == [status]
    audit = save_audit(tmp_path, first)
    items = collect_review_items(audit)
    assert items and not review_complete(audit) and not text_review_complete(audit, items)
    decisions = tmp_path / media_kind_review.DECISIONS_FILE
    media_kind_review.save_decision(decisions, items[0], media_kind_review.IRRELEVANT, 1)
    # One verdict covers every line card of the image.
    assert all(item["saved_irrelevant"] for item in collect_review_items(audit))
    assert review_complete(audit)
    saved = json.loads(decisions.read_text(encoding="utf-8"))["decisions"]
    assert [row["verdict"] for row in saved] == ["irrelevant"]
    final = await artwork_image(tmp_path, lines=lines, decisions=decisions)
    assert final["unresolved"] == [] and final["biographies"] == {} and final["assigned_biographies"] == {}
    assert final["evidence"][0]["media_kind_review"] == "irrelevant"
    changed = await artwork_image(tmp_path, lines=lines, pixel="#eeeeee", decisions=decisions)
    assert [issue["status"] for issue in changed["unresolved"]] == [status]


def test_irrelevant_verdict_is_refused_for_roster_name_lines_and_pdf_pages():
    from server.image_bio_review import irrelevant_allowed
    base = {"profile_url": "https://faculty.example.edu/p", "kind": "image", "original_preview": b"png"}
    assert irrelevant_allowed(base)
    assert not irrelevant_allowed({**base, "purpose": "segment_name"})
    assert not irrelevant_allowed({**base, "kind": "pdf"})
    assert not irrelevant_allowed({**base, "profile_url": None})
    # Without the whole image on the page (e.g. an animated GIF) a line crop cannot justify it.
    assert not irrelevant_allowed({**base, "original_preview": None})


@pytest.mark.asyncio
async def test_irrelevant_verdict_settles_an_ambiguous_media_kind_item(tmp_path):
    audit = save_audit(tmp_path, await ambiguous_image(tmp_path))
    item = media_kind_review.collect_items(audit)[0]
    decisions = tmp_path / media_kind_review.DECISIONS_FILE
    media_kind_review.save_decision(decisions, item, "irrelevant", 1)
    assert review_complete(audit)
    final = await ambiguous_image(tmp_path, decisions=decisions)
    assert final["unresolved"] == [] and final["evidence"][0]["media_kind_review"] == "irrelevant"
    with pytest.raises(ValueError):
        media_kind_review.save_decision(decisions, item, "irrelevant", 0)
    with pytest.raises(ValueError):
        media_kind_review.save_decision(decisions, {**item, "profile_url": ""}, "irrelevant", 1)


@pytest.mark.asyncio
@pytest.mark.browser
async def test_irrelevant_button_settles_every_card_of_the_image_in_browser(tmp_path):
    from playwright.async_api import async_playwright
    from server.image_bio_review import collect_review_items, render_review_page
    lines = (("张富有 教授", .99), ("完整学习经历与任职信息以及研究方向", .5), ("无字", .55))
    audit = save_audit(tmp_path, await artwork_image(tmp_path, lines=lines))
    items = collect_review_items(audit)
    assert len(items) == 2
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            errors, submitted = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            async def response(route):
                submitted.append((route.request.url, route.request.post_data_json))
                await route.fulfill(status=204)
            await page.route("https://review.example.edu/**", response)
            await page.goto("about:blank")
            await page.set_content(render_review_page(items, decision_url="https://review.example.edu/ocr",
                irrelevant_decision_url="https://review.example.edu/irrelevant"))
            await page.locator("button.irrelevant").first.click()
            await page.locator(".card .status").nth(1).filter(has_text="已判定为无关图片").wait_for()
            assert not errors
            assert submitted == [("https://review.example.edu/irrelevant", {"id": items[0]["id"]})]
            assert await page.locator(".card .status", has_text="已判定为无关图片").count() == 2
            # The whole original is shown once per image, not once per line card.
            assert await page.locator('img[alt="整张原图"]').count() == 1
        finally:
            await browser.close()
