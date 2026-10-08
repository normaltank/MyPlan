"""Synthetic OCR plus a real fixture browser, human API and deterministic rerun."""
from copy import deepcopy
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient
from PIL import Image
from playwright.async_api import async_playwright
import pytest

from server import app as web
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue
from server.browser_batch_tool import _validate_plan, run_plan
from tests.test_image_table_roster import scanned_rows


@pytest.mark.browser
@pytest.mark.asyncio
async def test_table_image_is_not_one_person_and_review_replay_releases_two_rows(tmp_path, monkeypatch):
    buffer = BytesIO()
    Image.new("RGB", (300, 120), "white").save(buffer, format="PNG")
    image = buffer.getvalue()
    scanned = scanned_rows([["序号", "姓名", "职称"], ["1", "张伟", "教授"],
                            ["2", "李明", "高级实验师"]])
    scanned["source_sha256"] = sha256(image).hexdigest()
    def scan(_original, **_kwargs):
        assert _original == image
        return deepcopy(scanned)
    monkeypatch.setattr("server.image_table_roster.scan_cells", scan)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = image if self.path == "/table.png" else b'<article><img src="/table.png"></article>'
            self.send_response(200)
            self.send_header("Content-Type", "image/png" if self.path == "/table.png" else "text/html")
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/roster"
    queue = AgentTaskQueue(tmp_path / "queue.db")
    store = AccountStore(queue.db_path)
    owner = store.create_user("alice", "Alice", "alice password 12345")
    store.create_user("bob", "Bob", "bob password 12345")
    task = queue.submit_task("样本大学", "样本学院", url, owner_user_id=owner.id)
    root = tmp_path / "outputs"
    attempt = root / "tasks" / task.task_id / "attempt-1"
    plan = {"school": task.school, "college": task.college, "source_kind": "university_faculty_roster",
            "branches": [{"url": url, "kind": "image_table", "single_page": True, "card_selector": "article img"}]}
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context()
            first = await run_plan(plan, context, attempt)
            assert first["total"] == 2 and not first["delivery_ready"] and first["excel_path"] is None
            assert not any(i["status"] == "image_roster_coverage_mismatch" for i in first["unresolved"])
            assert {i["status"] for i in first["unresolved"]} == {"table_full_image_review_missing"}
            assert first["image_table_evidence"]
            with queue._connect() as con:
                con.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (task.task_id,))
            monkeypatch.setattr(web, "agent_queue", queue)
            monkeypatch.setattr(web, "account_store", store)
            monkeypatch.setattr(web, "OUTPUT_DIR", str(root))
            client = TestClient(web.app)
            assert client.post("/api/auth/login", json={"username": "alice", "password": "alice password 12345"}).status_code == 200
            headers = {"X-CSRF-Token": client.get("/api/auth/me").json()["csrf_token"]}
            assert "图片表格逐格复核" in client.get(f"/review/task/{task.task_id}").text
            assert client.get(f"/review/task/{task.task_id}/table").status_code == 200
            payload = {"id": first["image_table_evidence"][0]["full_review_id"], "expected_count": 2}
            decision_url = f"/api/task/{task.task_id}/review/table"
            assert client.post(decision_url, json=payload).status_code == 403
            assert client.post(decision_url, json=payload, headers=headers).status_code == 204
            assert client.post(f"/api/task/{task.task_id}/review/retry", headers=headers).status_code == 200
            second = await run_plan(plan, context, attempt)
            assert second["delivery_ready"] and second["total"] == 2 and not second["unresolved"]
            rows = json.loads(Path(second["records_path"]).read_text(encoding="utf-8"))
            assert all(row["主页"] == "" and row["简介"] == "" for row in rows)
            assert rows[1]["职称"] == "高级实验师"
            assert all(len(row) == 10 for row in rows)
            cells_path = Path(first["image_table_evidence"][0]["cells_path"])
            cells_path.write_text("changed OCR", encoding="utf-8")
            with pytest.raises(ValueError, match="OCR changed"):
                from server.image_table_review import items
                items(first["audit_path"])
            await browser.close()
    finally:
        server.shutdown()
        server.server_close()


def test_table_poster_mixing_and_non_http_plan_are_rejected():
    branch = {"url": "https://example.edu/table", "kind": "image_table",
              "single_page": True, "card_selector": "article img"}
    plan = {"school": "U", "college": "C", "branches": [branch],
            "poster_reviews": [{"source_url": branch["url"], "reviewed_full_image": True, "expected_count": 1}]}
    with pytest.raises(ValueError, match="no longer accepted"):
        _validate_plan(plan)
    plan.pop("poster_reviews")
    branch["url"] = "file:///C:/local.html"
    with pytest.raises(ValueError, match="official URL"):
        _validate_plan(plan)


def test_table_review_does_not_hide_other_review_kinds(tmp_path, monkeypatch):
    from server import image_table_review, task_image_review
    audit = tmp_path / "a_audit.json"
    audit.write_text(json.dumps({"image_table_evidence": [{"url": "u"}], "unresolved": [
        {"status": "table_full_image_review_missing"},
        {"status": task_image_review.name_review.STATUS}]}), encoding="utf-8")
    monkeypatch.setattr(image_table_review, "review_complete", lambda path: True)
    pending = [{"id": "name-1"}]
    assert not task_image_review.review_complete(audit, [], [], names=pending)
    monkeypatch.setattr(task_image_review.name_review, "review_complete", lambda path, items: True)
    assert task_image_review.review_complete(audit, [], [], names=pending)
    audit.write_text(json.dumps({"image_table_evidence": [{"url": "u"}],
                                 "unresolved": [{"status": "table_cell_low_confidence"}]}), encoding="utf-8")
    assert task_image_review.review_complete(audit, [], [])
    monkeypatch.setattr(image_table_review, "review_complete", lambda path: False)
    assert not task_image_review.review_complete(audit, [], [])
