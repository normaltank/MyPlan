"""Lease ownership and university publication must survive a busy system.

Regression guard for the chain found in review: an HTTP heartbeat that gave up
on its first error, publication holding a tasks.db write lock while building
workbooks, a double publication per sync, and a worker that could reclaim a
college it was still running once that lease lapsed.
"""

import asyncio
import contextlib
import json
import sqlite3
import time

import httpx
import pytest

import server.browser_batch_tool as browser_batch_tool
from server.agent_queue import AgentTaskQueue
from server.agent_worker import AgentWorker
from server.browser_batch_tool import lease_keepalive, run_with_lease


def _report(tmp_path):
    audit = tmp_path / "audit.json"
    audit.write_text("{}", encoding="utf-8")
    return {"status": "completed", "delivery_ready": True, "unresolved": [],
            "excel_path": str(tmp_path / "college.xlsx"), "audit_path": str(audit)}


def _lease_until(queue, task_id):
    with queue._connect() as con:
        return con.execute("SELECT lease_until FROM tasks WHERE task_id=?", (task_id,)).fetchone()[0]


# ── Heartbeat ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_transient_renewal_errors_do_not_abandon_a_valid_lease(tmp_path):
    statuses = iter([503, 500, 200, 200, 200, 200, 200, 200, 200, 200])
    seen = []

    def responder(request):
        status = next(statuses, 200)
        seen.append(status)
        return httpx.Response(status)

    async def harvest():
        await asyncio.sleep(0.2)
        return _report(tmp_path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder),
                                 base_url="http://127.0.0.1:8000") as client:
        result = await run_with_lease(harvest, "task-1", "worker-1", "lease-1", client=client,
                                      interval_seconds=0.02, lease_seconds=5)

    assert seen[:3] == [503, 500, 200]  # kept renewing after two server errors
    assert result["delivery_ready"] is True
    assert result["unresolved"] == []


@pytest.mark.asyncio
async def test_renewal_failing_until_lease_expiry_blocks_delivery(tmp_path):
    def responder(_request):
        raise httpx.ConnectError("web service restarting")

    async def harvest():
        await asyncio.sleep(0.2)
        return _report(tmp_path)

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder),
                                 base_url="http://127.0.0.1:8000") as client:
        result = await run_with_lease(harvest, "task-1", "worker-1", "lease-1", client=client,
                                      interval_seconds=0.02, lease_seconds=0.1)

    assert result["delivery_ready"] is False
    assert result["unresolved"][-1]["status"] == "lease_renewal_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize('renewal_delay', [0, .2])
async def test_database_renewer_extends_the_stored_lease(tmp_path, renewal_delay):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    task = queue.claim_task("worker-1", task_id=queue.submit_task("U", "C", "https://e.edu/").task_id,
                            lease_seconds=300)
    before = _lease_until(queue, task.task_id)
    worker = AgentWorker(db_path=queue.db_path, output_dir=tmp_path / "out")
    renew = worker._lease_renewer(task)
    succeeded = asyncio.Event()

    async def observed_renewal():
        # Scheduling or database work can exceed the old fixed 0.15-second wait.
        await asyncio.sleep(renewal_delay)
        owned = await renew()
        if owned:
            succeeded.set()
        return owned

    async with lease_keepalive(observed_renewal, interval_seconds=0.02, lease_seconds=300) as lease:
        await asyncio.wait_for(succeeded.wait(), timeout=5)

    assert lease["error"] is None
    assert _lease_until(queue, task.task_id) > before


@pytest.mark.asyncio
async def test_database_renewer_reports_definitive_loss(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    task = queue.claim_task("worker-1", task_id=queue.submit_task("U", "C", "https://e.edu/").task_id)
    with queue._connect() as con:  # another claimant took over
        con.execute("UPDATE tasks SET lease_token='someone-else' WHERE task_id=?", (task.task_id,))
    worker = AgentWorker(db_path=queue.db_path, output_dir=tmp_path / "out")

    async with lease_keepalive(worker._lease_renewer(task), interval_seconds=0.02) as lease:
        await asyncio.sleep(0.1)

    assert lease["error"] == "lease no longer owned by worker"


# ── Claiming ─────────────────────────────────────────────────────────────────
def test_claim_skips_excluded_tasks_even_with_expired_lease(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    task = queue.claim_task("worker-1", task_id=queue.submit_task("U", "C", "https://e.edu/").task_id,
                            lease_seconds=0.01)
    time.sleep(0.05)
    assert queue.claim_task("worker-1", exclude_task_ids={task.task_id}) is None
    assert queue.claim_task("worker-1").task_id == task.task_id


@pytest.mark.asyncio
async def test_worker_never_reclaims_a_college_it_is_still_running(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    queued = worker.queue.submit_task("U", "C", "https://e.edu/")
    starts = []
    release = asyncio.Event()

    async def process(task):
        starts.append(task.task_id)
        with worker.queue._connect() as con:  # simulate a lapsed lease mid-run
            con.execute("UPDATE tasks SET lease_until=0 WHERE task_id=?", (task.task_id,))
        await release.wait()

    monkeypatch.setattr(worker, "_process", process)
    runner = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.sleep(0.8)  # several claim-loop iterations
        assert starts == [queued.task_id]
    finally:
        release.set()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner


@pytest.mark.asyncio
async def test_lease_is_renewed_while_waiting_to_sync(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out",
                         lease_renew_seconds=0.02)
    task = worker.queue.claim_task("server-worker", task_id=worker.queue.submit_task(
        "U", "C", "https://e.edu/").task_id)
    report = _report(tmp_path)

    async def harvested(*_args, **_kwargs):
        return report

    observed = {}

    async def slow_sync(synced_task, _report):
        start = _lease_until(worker.queue, synced_task.task_id)
        await asyncio.sleep(0.2)  # e.g. waiting behind another college of the same school
        observed["renewed"] = _lease_until(worker.queue, synced_task.task_id) > start
        return True

    monkeypatch.setattr(browser_batch_tool, "run_with_lease", harvested)
    monkeypatch.setattr(worker, "_sync", slow_sync)
    await worker._process(task)
    assert observed == {"renewed": True}


# ── Publication ──────────────────────────────────────────────────────────────
def _record(college, name="Person", url="https://example.edu/p/1"):
    return {"序号": 1, "姓名": name, "职称": "Professor", "邮箱": "",
            "学校": "University", "学院": college, "采集人": "fixture",
            "采集日期": "2026-09-26", "简介": "Full biography", "主页": url}


def test_publication_does_not_write_lock_the_task_queue(tmp_path, monkeypatch):
    from server import excel_export
    from server.university_delivery import publish_delivery

    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A")
    claimed = queue.claim_task("fixture", task_id=task.task_id)
    queue.complete_task(task.task_id, [_record("A")], {"total": 1, "with_email": 0, "no_email": 1},
                        worker_id="fixture", lease_token=claimed.lease_token)
    other = queue.submit_task("University", "B")
    original = excel_export.export_dual_sheet_excel
    probes = []

    def exporting(records, path):
        # While a workbook is being generated, other colleges must still be
        # able to claim, renew and sync without waiting for publication.
        writer = sqlite3.connect(queue.db_path, timeout=0.2)
        try:
            writer.execute("BEGIN IMMEDIATE")
            writer.execute("UPDATE tasks SET error=NULL WHERE task_id=?", (other.task_id,))
            writer.commit()
            probes.append("write-ok")
        finally:
            writer.close()
        return original(records, path)

    monkeypatch.setattr(excel_export, "export_dual_sheet_excel", exporting)
    delivery = publish_delivery(queue, task.batch_id, "University", tmp_path / "outputs")

    assert delivery["university_excel"]
    assert probes and set(probes) == {"write-ok"}


@pytest.mark.asyncio
async def test_sync_report_reuses_publication_done_by_the_web_endpoint(tmp_path, monkeypatch):
    import server.university_delivery as university_delivery
    from server.browser_batch_sync import sync_report

    queue = AgentTaskQueue(tmp_path / "queue.db")
    task = queue.submit_task("University", "A")
    claimed = queue.claim_task("fixture", task_id=task.task_id)
    root = tmp_path / "outputs"
    root.mkdir()
    excel = root / "college.xlsx"
    excel.write_bytes(b"placeholder")
    records = tmp_path / "records.json"
    records.write_text(json.dumps([_record("A")]), encoding="utf-8")
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"school": "University", "college": "A", "delivery_ready": True,
        "unresolved": [], "total": 1, "with_email": 0, "no_email": 1, "directory_pages": 1,
        "candidate_count": 1, "records_path": str(records), "excel_path": str(excel)}), encoding="utf-8")
    calls = []
    real_publish = university_delivery.publish_delivery

    def counting_publish(*args, **kwargs):
        calls.append(args[1:3])
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(university_delivery, "publish_delivery", counting_publish)

    def responder(request):  # behaves like the Web endpoint: persist, then publish once
        payload = json.loads(request.content)
        queue.sync_task_result(task.task_id, payload["records"], payload["stats"], payload["download_url"],
                               worker_id=payload["worker_id"], lease_token=payload["lease_token"])
        delivery = university_delivery.publish_delivery(queue, task.batch_id, "University", root)
        return httpx.Response(200, json={"status": "synced", "delivery_scope": delivery["delivery_scope"],
                                         "university_excel": delivery.get("university_excel"),
                                         "university_download_url": delivery.get("download_url")})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url="http://localhost") as client:
        result = await sync_report(task.task_id, report, "fixture", claimed.lease_token,
                                   output_root=root, db_path=queue.db_path, client=client)

    assert len(calls) == 1
    assert result["delivery_scope"]["state"] == "ready"
    assert result["university_excel"]


def test_constructing_a_queue_has_no_filesystem_side_effects(tmp_path):
    AgentTaskQueue(tmp_path / "tasks.db")
    assert not (tmp_path / "outputs").exists()
