"""Every queued college starts its own worker run without an application cap."""

import asyncio
import contextlib

import pytest

from server.agent_worker import AgentWorker


@pytest.mark.asyncio
async def test_all_queued_colleges_start_before_any_finishes(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    task_ids = {
        worker.queue.submit_task("样本大学", f"学院{i}", f"https://example.edu/{i}").task_id
        for i in range(6)
    }
    started: set[str] = set()
    all_started = asyncio.Event()
    release = asyncio.Event()

    async def process(task):
        started.add(task.task_id)
        if started == task_ids:
            all_started.set()
        await release.wait()

    monkeypatch.setattr(worker, "_process", process)
    runner = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.wait_for(all_started.wait(), timeout=1.5)
        assert started == task_ids
    finally:
        release.set()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner


@pytest.mark.asyncio
async def test_once_still_processes_only_one_college(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    for i in range(2):
        worker.queue.submit_task("样本大学", f"学院{i}", f"https://example.edu/{i}")
    started = []

    async def process(task):
        started.append(task.task_id)

    monkeypatch.setattr(worker, "_process", process)
    await worker.run_forever(once=True)

    assert len(started) == 1
    assert worker.queue.get_queue_stats()["queued"] == 1
