import json

import httpx
import openpyxl
import pytest

from server.agent_queue import AgentTaskQueue
from server.browser_batch_sync import sync_report


def record(college, name="Person", url="https://example.edu/p/1", email=""):
    return {"序号": 1, "姓名": name, "职称": "Professor", "邮箱": email,
            "学校": "University", "学院": college, "采集人": "fixture",
            "采集日期": "2026-09-26", "简介": "Full biography", "主页": url}


def finish(queue, task, rows):
    claimed = queue.claim_task("fixture", task_id=task.task_id)
    n = sum(bool(r["邮箱"]) for r in rows)
    queue.complete_task(task.task_id, rows, {"total": len(rows), "with_email": n,
        "no_email": len(rows)-n}, worker_id="fixture", lease_token=claimed.lease_token)


def test_batch_scope_blocks_pending_and_failed_colleges(tmp_path):
    queue = AgentTaskQueue(tmp_path / "queue.db")
    first = queue.submit_task("University", "A", mode="batch", batch_id="batch")
    second = queue.submit_task("University", "B", mode="batch", batch_id="batch")
    finish(queue, first, [record("A")])
    scope = queue.get_delivery_scope("batch", "University")
    assert scope["state"] == "collecting"
    assert scope["target_colleges"] == ["A", "B"]
    assert scope["completed_colleges"] == ["A"]
    claimed = queue.claim_task("fixture", task_id=second.task_id)
    queue.sync_task_result(second.task_id, [], {}, "", "failed", "fixture", claimed.lease_token)
    assert queue.get_delivery_scope("batch", "University")["state"] == "blocked"


def test_current_snapshot_replaces_college_preserves_history_and_homonyms(tmp_path):
    queue = AgentTaskQueue(tmp_path / "queue.db")
    old = queue.submit_task("University", "A")
    finish(queue, old, [record("A", "Old")])
    newest = queue.submit_task("University", "A")
    finish(queue, newest, [record("A", "Same", "https://example.edu/1")])
    other = queue.submit_task("University", "B")
    finish(queue, other, [record("B", "Same", "https://example.edu/2")])
    snapshot = queue.get_university_snapshot("University")
    assert len(snapshot["records"]) == 2
    assert snapshot["task_ids"] == [newest.task_id, other.task_id]
    assert len(snapshot["identity_conflicts"]) == 1
    assert queue.get_task(old.task_id).records[0]["姓名"] == "Old"


@pytest.mark.asyncio
async def test_sync_partial_does_not_publish_complete_batch(tmp_path):
    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A", mode="batch", batch_id="batch")
    queue.submit_task("University", "B", mode="batch", batch_id="batch")
    claimed = queue.claim_task("fixture", task_id=task.task_id)
    root = tmp_path / "outputs"
    root.mkdir()
    excel = root / "college.xlsx"
    excel.write_bytes(b"placeholder")
    rows = [record("A")]
    records = tmp_path / "records.json"
    records.write_text(json.dumps(rows), encoding="utf-8")
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"school": "University", "college": "A",
        "delivery_ready": True, "unresolved": [], "total": 1, "with_email": 0,
        "no_email": 1, "directory_pages": 1, "candidate_count": 1,
        "records_path": str(records), "excel_path": str(excel)}), encoding="utf-8")

    def responder(request):
        payload = json.loads(request.content)
        queue.sync_task_result(task.task_id, payload["records"], payload["stats"],
            payload["download_url"], worker_id=payload["worker_id"], lease_token=payload["lease_token"])
        return httpx.Response(200, json={"status": "synced"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url="http://localhost") as client:
        result = await sync_report(task.task_id, report, "fixture", claimed.lease_token,
            output_root=root, db_path=queue.db_path, client=client)
    assert result["university_excel"] is None
    assert result["delivery_scope"]["state"] == "collecting"
    assert queue.get_task(task.task_id).download_url == "/download/college.xlsx"
    assert not (root / "University.xlsx").exists()


def test_versioned_delivery_is_immutable_and_tracks_affiliations(tmp_path):
    from server.university_delivery import publish_delivery

    queue = AgentTaskQueue(tmp_path / "queue.db")
    first = queue.submit_task("University", "A", mode="batch", batch_id="one")
    second = queue.submit_task("University", "B", mode="batch", batch_id="one")
    finish(queue, first, [record("A")])
    finish(queue, second, [record("B")])
    delivery = publish_delivery(queue, "one", "University", tmp_path / "outputs")
    assert delivery["total"] == 1
    manifest = json.loads(open(delivery["manifest_path"], encoding="utf-8").read())
    assert manifest["identities"][0]["colleges"] == ["A", "B"]
    assert manifest["scope"]["target_colleges"] == ["A", "B"]
    old_bytes = open(delivery["excel_path"], "rb").read()
    repeat = queue.submit_task("University", "A")
    finish(queue, repeat, [record("A", "Replacement")])
    newer = publish_delivery(queue, repeat.batch_id, "University", tmp_path / "outputs")
    assert newer["version"] != delivery["version"]
    assert open(delivery["excel_path"], "rb").read() == old_bytes
    workbook = openpyxl.load_workbook(tmp_path / "outputs" / "University.xlsx")
    assert sum(sheet.max_row - 1 for sheet in workbook.worksheets) == 2


def test_conflicting_same_profile_blocks_publication(tmp_path):
    from server.university_delivery import publish_delivery
    queue = AgentTaskQueue(tmp_path / "queue.db")
    for college, email in [("A", "first@example.edu"), ("B", "other@example.edu")]:
        task = queue.submit_task("University", college, mode="batch", batch_id="batch")
        finish(queue, task, [record(college, email=email)])
    result = publish_delivery(queue, "batch", "University", tmp_path / "outputs")
    assert result["delivery_scope"]["state"] == "blocked"
    assert result["university_excel"] is None
    assert queue.get_delivery_scope("batch", "University")["state"] == "blocked"


def test_concurrent_publication_returns_one_version(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from server.university_delivery import publish_delivery
    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A")
    finish(queue, task, [record("A")])
    def publish(_):
        return publish_delivery(AgentTaskQueue(queue.db_path), task.batch_id, "University", tmp_path / "outputs")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, range(2)))
    assert results[0]["version"] == results[1]["version"]
    with queue._connect() as con:
        assert con.execute("SELECT COUNT(*) FROM university_deliveries").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_app_partial_export_and_status_have_explicit_scope(tmp_path, monkeypatch):
    from server import app as web
    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A", mode="batch", batch_id="batch")
    queue.submit_task("University", "B", mode="batch", batch_id="batch")
    finish(queue, task, [record("A")])
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "OUTPUT_DIR", str(tmp_path / "outputs"))
    assert web.export_batch_by_university("batch") == []
    status = await web.get_batch_status("batch")
    assert status["delivery_state"] == "collecting"
    assert "download_url" not in status


@pytest.mark.asyncio
async def test_app_single_sync_publishes_scoped_download(tmp_path, monkeypatch):
    from server import app as web
    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A")
    claimed = queue.claim_task("fixture", task_id=task.task_id)
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "OUTPUT_DIR", str(tmp_path / "outputs"))
    result = await web.sync_task(task.task_id, web.TaskSyncRequest(
        records=[record("A")], stats={"total": 1, "with_email": 0, "no_email": 1},
        download_url="/download/college.xlsx", worker_id="fixture", lease_token=claimed.lease_token))
    assert result["delivery_scope"]["target_colleges"] == ["A"]
    assert result["university_excel"]
    assert result["task"]["download_url"].startswith("/download/versions/")


def test_repeated_college_in_batch_uses_last_completed_snapshot(tmp_path):
    from server.university_delivery import publish_delivery
    queue = AgentTaskQueue(tmp_path / "queue.db")
    first, second = queue.submit_batch([
        ("University", "A", None), ("University", "A", None)], "batch")
    finish(queue, first, [record("A", "Old")])
    finish(queue, second, [record("A", "New")])
    result = publish_delivery(queue, "batch", "University", tmp_path / "outputs")
    assert result["total"] == 1
    manifest = json.loads(open(result["manifest_path"], encoding="utf-8").read())
    assert manifest["college_snapshots"][0]["task_id"] == second.task_id


def test_repeated_publication_preserves_version_and_workbook_bytes(tmp_path):
    from server.university_delivery import publish_delivery
    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A")
    finish(queue, task, [record("A")])
    root = tmp_path / "outputs"
    first = publish_delivery(queue, task.batch_id, "University", root)
    old_workbook = open(first["excel_path"], "rb").read()
    old_manifest = open(first["manifest_path"], "rb").read()
    old_current = (root / "University.xlsx").read_bytes()
    second = publish_delivery(queue, task.batch_id, "University", root)
    assert second["version"] == first["version"]
    assert open(second["excel_path"], "rb").read() == old_workbook
    assert open(second["manifest_path"], "rb").read() == old_manifest
    assert (root / "University.xlsx").read_bytes() == old_current


@pytest.mark.asyncio
async def test_stale_lease_in_real_database_cannot_publish(tmp_path):
    from server.agent_queue import LeaseConflictError
    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A")
    queue.claim_task("fixture", task_id=task.task_id)
    root = tmp_path / "outputs"
    root.mkdir()
    previous = root / "University.xlsx"
    previous.write_bytes(b"previous delivery")
    college = root / "college.xlsx"
    college.write_bytes(b"placeholder")
    records = tmp_path / "records.json"
    records.write_text(json.dumps([record("A")]), encoding="utf-8")
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"school": "University", "college": "A",
        "delivery_ready": True, "unresolved": [], "total": 1, "with_email": 0,
        "no_email": 1, "directory_pages": 1, "candidate_count": 1,
        "records_path": str(records), "excel_path": str(college)}), encoding="utf-8")
    def responder(request):
        payload = json.loads(request.content)
        try:
            queue.sync_task_result(task.task_id, payload["records"], payload["stats"],
                payload["download_url"], worker_id=payload["worker_id"], lease_token=payload["lease_token"])
        except LeaseConflictError:
            return httpx.Response(409)
        return httpx.Response(200, json={"status": "synced"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url="http://localhost") as client:
        with pytest.raises(httpx.HTTPStatusError):
            await sync_report(task.task_id, report, "fixture", "stale-token",
                output_root=root, db_path=queue.db_path, client=client)
    assert previous.read_bytes() == b"previous delivery"
    assert queue.get_task(task.task_id).status == "running"
    assert not (root / "versions").exists()


@pytest.mark.asyncio
async def test_version_download_is_safe_and_retains_university_filename(tmp_path, monkeypatch):
    from server import app as web
    from fastapi import HTTPException
    root = tmp_path / "outputs"
    version = root / "versions" / "one" / "University.xlsx"
    version.parent.mkdir(parents=True)
    version.write_bytes(b"workbook")
    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A")
    finish(queue, task, [record("A")])
    (version.parent / "manifest.json").write_text(json.dumps({"school": "University",
        "scope": {"batch_id": task.batch_id}, "college_snapshots": [{"task_id": task.task_id}]}), encoding="utf-8")
    monkeypatch.setattr(web, "agent_queue", queue)
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"outside")
    monkeypatch.setattr(web, "OUTPUT_DIR", str(root))
    response = await web.download_file("versions/one/University.xlsx")
    assert response.filename == "University.xlsx"
    with pytest.raises(HTTPException):
        await web.download_file("../outside.xlsx")


@pytest.mark.asyncio
async def test_app_stale_lease_returns_conflict_with_isolated_queue(tmp_path, monkeypatch):
    from server import app as web
    from fastapi import HTTPException
    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A")
    queue.claim_task("fixture", task_id=task.task_id)
    monkeypatch.setattr(web, "agent_queue", queue)
    with pytest.raises(HTTPException) as error:
        await web.sync_task(task.task_id, web.TaskSyncRequest(
            records=[record("A")], stats={"total": 1, "with_email": 0, "no_email": 1},
            worker_id="fixture", lease_token="stale"))
    assert error.value.status_code == 409


def test_unrelated_current_conflict_does_not_expand_requested_batch_scope(tmp_path):
    from server.university_delivery import publish_delivery
    queue = AgentTaskQueue(tmp_path / "queue.db")
    old = queue.submit_task("University", "A")
    finish(queue, old, [record("A", email="one@example.edu")])
    root = tmp_path / "outputs"
    publish_delivery(queue, old.batch_id, "University", root)
    old_current = (root / "University.xlsx").read_bytes()
    conflict = queue.submit_task("University", "B")
    finish(queue, conflict, [record("B", email="two@example.edu")])
    requested = queue.submit_task("University", "C")
    finish(queue, requested, [record("C", "New", "https://example.edu/new")])
    result = publish_delivery(queue, requested.batch_id, "University", root)
    assert result["delivery_scope"]["state"] == "ready"
    assert result["delivery_scope"]["target_colleges"] == ["C"]
    assert result["total"] == 1
    assert result["current_snapshot_state"] == "blocked"
    assert result["current_identity_conflicts"]
    assert (root / "University.xlsx").read_bytes() == old_current
