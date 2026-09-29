"""Safety valves that keep full parallel dispatch from overwhelming sites or the host.

All colleges still start at once. These guards bound only what is shared: the
pages opened against one university site, free memory for yet another
browser, the size of one submission, and visibility of unusual model spend.
"""

import asyncio
import contextlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server import agent_recon
from server import agent_worker as agent_worker_module
from server.agent_worker import AgentWorker
from server.harvest_workflow import capture_profiles
from server.site_limits import site_key


# ── Per-site politeness ──────────────────────────────────────────────────────
@pytest.mark.parametrize("url,expected", [
    ("https://cs.nju.edu.cn/teachers/1.htm", "nju.edu.cn"),
    ("https://law.nju.edu.cn/", "nju.edu.cn"),
    ("https://www.hku.hk/staff", "hku.hk"),
    ("https://faculty.example.ac.uk/a", "example.ac.uk"),
    ("https://www.tsinghua.edu.cn/", "tsinghua.edu.cn"),
    ("http://10.0.0.5:8080/p", "10.0.0.5"),
    ("not a url", None),
])
def test_site_key_groups_colleges_of_one_university(url, expected):
    assert site_key(url) == expected


class _Response:
    status = 200


class _CountingPage:
    def __init__(self, tracker):
        self.tracker = tracker
        self.url = ""

    async def goto(self, url, **_kwargs):
        self.url = url
        site = url.split("/")[2].split(".", 1)[1]
        self.tracker["now"][site] = self.tracker["now"].get(site, 0) + 1
        self.tracker["peak"][site] = max(self.tracker["peak"].get(site, 0), self.tracker["now"][site])
        await asyncio.sleep(0.02)
        self.tracker["now"][site] -= 1
        return _Response()

    async def content(self):
        return f"<html><body><p>{self.url}</p></body></html>"

    async def close(self):
        pass


class _Context:
    def __init__(self, tracker):
        self.tracker = tracker

    async def new_page(self):
        return _CountingPage(self.tracker)


async def _two_colleges(tracker, host_a, host_b):
    context = _Context(tracker)
    urls_a = [f"https://{host_a}/p/{i}" for i in range(12)]
    urls_b = [f"https://{host_b}/p/{i}" for i in range(12)]
    results = await asyncio.gather(capture_profiles(context, urls_a, 4),
                                   capture_profiles(context, urls_b, 4))
    return results


@pytest.mark.asyncio
async def test_colleges_of_one_university_share_the_site_limit(monkeypatch):
    monkeypatch.setenv("HARVEST_PER_SITE_CONCURRENCY", "3")
    tracker = {"now": {}, "peak": {}}
    results = await _two_colleges(tracker, "cs.uni.edu.cn", "law.uni.edu.cn")
    assert tracker["peak"]["uni.edu.cn"] == 3  # 2 colleges x 4 would otherwise reach 8
    assert all(not errors and len(pages) == 12 for pages, errors in results)


@pytest.mark.asyncio
async def test_different_universities_do_not_throttle_each_other(monkeypatch):
    monkeypatch.setenv("HARVEST_PER_SITE_CONCURRENCY", "3")
    tracker = {"now": {}, "peak": {}}
    await _two_colleges(tracker, "cs.one.edu.cn", "cs.two.edu.cn")
    assert tracker["peak"] == {"one.edu.cn": 3, "two.edu.cn": 3}


@pytest.mark.asyncio
async def test_site_limit_can_be_disabled(monkeypatch):
    monkeypatch.setenv("HARVEST_PER_SITE_CONCURRENCY", "0")
    tracker = {"now": {}, "peak": {}}
    await _two_colleges(tracker, "cs.uni.edu.cn", "law.uni.edu.cn")
    assert tracker["peak"]["uni.edu.cn"] == 8


# ── Usage alert (report, never stop) ─────────────────────────────────────────
def _message(tokens=100):
    return {"type": "assistant/message", "data": {"usage": {
        "inputTokens": tokens, "cacheReadTokens": 0, "outputTokens": 0}}}


def test_usage_alert_fires_once_without_stopping():
    seen = []
    budget = agent_recon.ReconBudget(alert_tokens=250, on_alert=seen.append)
    for _ in range(6):
        budget.observe(_message())
    assert len(seen) == 1 and seen[0]["total_tokens"] == 300
    assert budget.snapshot()["calls"] == 6


def test_alert_sink_failure_cannot_stop_reconnaissance_and_kill_limit_still_applies():
    def broken(_alert):
        raise RuntimeError("log disk full")
    budget = agent_recon.ReconBudget(max_calls=3, max_tokens=10_000, alert_calls=1, on_alert=broken)
    budget.observe(_message())
    budget.observe(_message())
    with pytest.raises(agent_recon.ReconBudgetExceeded):
        budget.observe(_message())
    with pytest.raises(ValueError):
        agent_recon.ReconBudget(max_calls=3, max_tokens=0)


@pytest.mark.asyncio
async def test_worker_records_usage_alert_and_keeps_going(tmp_path, monkeypatch, capsys):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "outputs",
                         recon_alert_tokens=250, max_recon_repairs=0)
    task = SimpleNamespace(task_id="alert", school="样本大学", college="样本学院",
                           url="https://example.edu/roster")

    async def no_cache(*_):
        return None

    async def expensive_then_error(*_args, budget, **_kwargs):
        for _ in range(5):  # would have been stopped if the alert were a kill limit
            budget.observe(_message())
        raise agent_recon.ReconError("fixture stops here")

    monkeypatch.setattr(worker, "_cached_recon", no_cache)
    monkeypatch.setattr("server.agent_worker.reconnoiter", expensive_then_error)
    root = tmp_path / "outputs" / "tasks" / task.task_id
    with pytest.raises(agent_recon.ReconError, match="fixture"):
        await worker._harvest_with_repair(task, root)
    alert = json.loads((root / "recon-usage-alert.json").read_text(encoding="utf-8"))
    assert alert["task_id"] == "alert" and alert["total_tokens"] == 300
    assert "侦察用量告警" in capsys.readouterr().out


# ── Memory admission ─────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_low_memory_defers_new_colleges_but_never_the_first(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out",
                         min_free_memory_mb=1024)
    ids = [worker.queue.submit_task("U", f"C{i}", f"https://u.edu/{i}").task_id for i in range(3)]
    free = {"mb": 100.0}
    monkeypatch.setattr(agent_worker_module, "available_memory_mb", lambda: free["mb"])
    started = []
    release = asyncio.Event()

    async def process(task):
        started.append(task.task_id)
        await release.wait()

    monkeypatch.setattr(worker, "_process", process)
    runner = asyncio.create_task(worker.run_forever())
    try:
        await asyncio.sleep(0.6)
        assert started == ids[:1]  # the first college always starts
        free["mb"] = 8000.0
        for _ in range(40):
            if len(started) == 3:
                break
            await asyncio.sleep(0.1)
        assert sorted(started) == sorted(ids)
    finally:
        release.set()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner


def test_memory_probe_reads_this_host():
    value = agent_worker_module.available_memory_mb()
    assert value is None or value > 0


# ── Submission size ──────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_oversized_or_duplicate_batches_are_rejected_before_queueing(tmp_path, monkeypatch):
    from server import app as web
    from server.agent_queue import AgentTaskQueue

    queue = AgentTaskQueue(tmp_path / "tasks.db")
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "_max_batch_colleges", lambda: 3)
    lines = "\n".join(f"一校 学院{i} https://example.edu/{i}" for i in range(4))
    with pytest.raises(HTTPException, match="最多提交 3"):
        await web.start_batch_collection(web.BatchCollectRequest(items_text=lines))
    duplicate = "一校 甲院 https://a.example.edu/\n二校 乙院 https://b.example.edu/\n一校 甲院 https://c.example.edu/"
    with pytest.raises(HTTPException, match="第 3 行与第 1 行"):
        await web.start_batch_collection(web.BatchCollectRequest(items_text=duplicate))
    assert queue.get_queue_stats()["total_tasks"] == 0


def test_batch_limit_comes_from_config(tmp_path, monkeypatch):
    from server import app as web
    assert web._max_batch_colleges() >= 1
