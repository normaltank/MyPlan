"""Remaining P1 review items: queue polling, task deadline, self-check status, proxy-safe local checks."""

import sqlite3
import sys
import time
from types import SimpleNamespace

import pytest

from server import agent_worker
from server.agent_queue import AgentTaskQueue
from server.agent_recon import ReconResult
from server.agent_worker import AgentWorker

PLAN = {"school": "U", "college": "C", "source_kind": "university_faculty_roster",
        "branches": [{"url": "https://e.edu/roster", "card_selector": "main li", "single_page": True}]}


# ── 9. queue construction and idle polling ───────────────────────────────────
def test_constructing_a_queue_does_not_load_every_task(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    for index in range(5):
        queue.submit_task("U", f"C{index}", "https://e.edu/")

    def decode(*_args):
        raise AssertionError("tasks must not be decoded while constructing a queue")

    monkeypatch.setattr(AgentTaskQueue, "_decode", decode)
    AgentTaskQueue(tmp_path / "tasks.db")


def test_idle_claim_does_not_wait_for_the_write_lock(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    writer = sqlite3.connect(queue.db_path, timeout=0.1)
    try:
        writer.execute("BEGIN IMMEDIATE")        # another process is writing
        started = time.monotonic()
        assert queue.claim_task("worker-1") is None
        assert time.monotonic() - started < 1
    finally:
        writer.rollback()
        writer.close()
    queue.submit_task("U", "C", "https://e.edu/")
    assert queue.claim_task("worker-1") is not None


@pytest.mark.asyncio
async def test_worker_backs_off_while_the_queue_is_empty(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out", min_free_memory_mb=0)
    delays = []

    class Stop(Exception):
        pass

    async def sleep(seconds):
        delays.append(seconds)
        if len(delays) == 7:
            raise Stop

    monkeypatch.setattr(agent_worker.asyncio, "sleep", sleep)
    with pytest.raises(Stop):
        await worker.run_forever()
    assert delays == [0.2, 0.4, 0.8, 1.6, 2.0, 2.0, 2.0]


# ── 8. recon repairs respect the task deadline ───────────────────────────────
def test_recon_window_budgets_against_the_deadline(tmp_path):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    now = time.monotonic()
    assert worker._recon_window(None, 1) == 900
    assert 890 < worker._recon_window(now + 5000, 1) <= 900
    assert 390 < worker._recon_window(now + 1000, 0) <= 400
    assert worker._recon_window(now + 700, 0) == agent_worker.MIN_RECON_SECONDS
    assert worker._recon_window(now + 700, 1) is None


async def _run_repairs(tmp_path, monkeypatch, deadline):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    task = SimpleNamespace(school="U", college="C", url=PLAN["branches"][0]["url"], task_id="deadline-task")
    timeouts = []

    async def scout(*_args, **kwargs):
        timeouts.append(kwargs["timeout_seconds"])
        # Each repair proposes a different selector; an unchanged plan stops early.
        branch = {**PLAN["branches"][0], "card_selector": f"main ul:nth-of-type({len(timeouts)}) > li"}
        return ReconResult({**PLAN, "branches": [branch]}, 0, "completed", "deadline-scout")

    async def browser(*_args):
        return {"delivery_ready": False,
                "unresolved": [{"status": "roster_card_mismatch", "url": PLAN["branches"][0]["url"]}]}

    async def nothing(*_args):
        return None

    monkeypatch.setattr(agent_worker, "reconnoiter", scout)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    monkeypatch.setattr(worker, "_cached_recon", nothing)
    monkeypatch.setattr(worker, "_unaffected_branches", nothing)
    report = await worker._harvest_with_repair(task, tmp_path / "task", deadline=deadline)
    return report, timeouts


@pytest.mark.asyncio
async def test_repair_that_cannot_finish_is_not_started(tmp_path, monkeypatch):
    report, timeouts = await _run_repairs(tmp_path, monkeypatch, time.monotonic() + 700)
    assert timeouts == [agent_worker.MIN_RECON_SECONDS]
    assert [item["status"] for item in report["unresolved"]] == [
        "roster_card_mismatch", "repair_skipped_task_deadline"]


@pytest.mark.asyncio
async def test_repairs_run_normally_when_time_remains(tmp_path, monkeypatch):
    report, timeouts = await _run_repairs(tmp_path, monkeypatch, time.monotonic() + 10_000)
    assert timeouts == [900, 900, 900]
    assert "repair_skipped_task_deadline" not in {item["status"] for item in report["unresolved"]}


# ── 6. self-check result reaches the status interface ────────────────────────
def test_self_check_result_is_reported_with_worker_status(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    assert queue.get_worker_status()["self_check"] is None
    queue.record_self_check(["缺少 pypinyin"])
    status = queue.get_worker_status()["self_check"]
    assert status["ok"] is False and status["problems"] == ["缺少 pypinyin"]
    queue.record_self_check([])
    assert queue.get_worker_status()["self_check"]["ok"] is True


def test_failed_self_check_is_recorded_before_the_worker_exits(tmp_path, monkeypatch):
    import server.worker_selfcheck as selfcheck
    monkeypatch.setattr(selfcheck, "run_self_check", lambda: ["Playwright Chromium 未安装"])
    monkeypatch.setattr(sys, "argv", ["agent_worker", "--db", str(tmp_path / "tasks.db")])
    with pytest.raises(SystemExit) as exited:
        agent_worker.main()
    assert exited.value.code == 2
    status = AgentTaskQueue(tmp_path / "tasks.db").get_worker_status()["self_check"]
    assert status == {"ok": False, "problems": ["Playwright Chromium 未安装"],
                      "checked_at": status["checked_at"]}


# ── 10. forwarded requests are never "local"; lease length is bounded ────────
def test_forwarded_requests_are_not_local_and_lease_length_is_bounded():
    from pydantic import ValidationError
    from starlette.requests import Request
    from server.app import LeaseRenewRequest, _local_request

    def request(headers):
        return Request({"type": "http", "method": "GET", "path": "/", "headers": headers,
                        "client": ("127.0.0.1", 5000), "server": ("127.0.0.1", 8000), "scheme": "http"})

    assert _local_request(request([]))
    for header in (b"x-forwarded-for", b"x-real-ip", b"forwarded", b"x-forwarded-proto"):
        assert not _local_request(request([(header, b"203.0.113.9")]))
    assert LeaseRenewRequest(worker_id="w", lease_token="t").lease_seconds == 300
    for bad in (0, 29, 901, 86400):
        with pytest.raises(ValidationError):
            LeaseRenewRequest(worker_id="w", lease_token="t", lease_seconds=bad)
