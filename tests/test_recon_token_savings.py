"""Bound model spend per reconnaissance session and keep what it costs visible.

Regression for the 2026-09-29 NXIST run: a flat multi-person article cannot
be expressed as one card selector, so each branch session kept probing the
same page (~50 requests), a 900 s timeout left the Harness thread running and
recorded no usage, and full-page screenshots were sent as multi-MB images.
"""
from __future__ import annotations

import base64
import json
import threading
import time
from types import SimpleNamespace

import pytest

from server import agent_recon
from server.agent_worker import AgentWorker


def _message(tokens=1000):
    return {"type": "assistant/message", "data": {"usage": {
        "inputTokens": tokens, "cacheReadTokens": 0, "outputTokens": 10}}}


def _fake_harness(monkeypatch, events: int, seen: list):
    monkeypatch.setattr(agent_recon, "check_runtime_composition", lambda *_: None)

    class FakeHarness:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            seen.append("closed")

        def run(self, _prompt, *, session_id, on_notification=None):
            for _ in range(events):
                seen.append("request")
                if on_notification:
                    on_notification(SimpleNamespace(method="session.event", payload={
                        "sessionId": session_id, "event": _message()}))
            return SimpleNamespace(events=[], finish_reason="completed", final_response="{}")

    import deepseek_harness
    monkeypatch.setattr(deepseek_harness, "DeepSeekHarness", FakeHarness)


def _cfg(**extra):
    return {"dsh_home": "home", "workspace": ".", "dsh_bin": None, "profile": "sdk",
            "provider": "deepseek", "model": "deepseek-flash", "effort": "high", "api_key": "test", **extra}


def test_phase_session_stops_after_its_request_limit(monkeypatch):
    seen = []
    _fake_harness(monkeypatch, events=50, seen=seen)
    with pytest.raises(agent_recon.ReconPhaseLimitExceeded, match="上限 25") as caught:
        agent_recon._execute_harness("probe", "s", _cfg(phase="branch", phase_call_limit=25), ())
    assert seen.count("request") == 26 and seen[-1] == "closed"
    assert caught.value.diagnostics["model_calls"] == 26
    assert caught.value.usage_unknown is False


def test_no_limit_keeps_previous_behaviour(monkeypatch):
    seen = []
    _fake_harness(monkeypatch, events=50, seen=seen)
    agent_recon._execute_harness("probe", "s", _cfg(phase="branch"), ())
    assert seen.count("request") == 50


def test_cancel_flag_ends_the_session_at_the_next_event(monkeypatch):
    seen = []
    _fake_harness(monkeypatch, events=50, seen=seen)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(agent_recon.ReconCancelled):
        agent_recon._execute_harness("probe", "s", _cfg(cancel_event=cancel), ())
    assert seen.count("request") == 1


@pytest.mark.asyncio
async def test_timeout_stops_the_harness_thread_and_keeps_usage(monkeypatch):
    stopped = threading.Event()

    def slow_scout(_prompt, _sid, cfg):
        while True:  # a session that would otherwise run until its own deadline
            if cfg["cancel_event"].is_set():
                stopped.set()
                raise agent_recon.ReconCancelled("stopped")
            cfg["budget"].observe(_message())
            time.sleep(0.02)

    monkeypatch.setattr(agent_recon, "_run_harness_blocking", slow_scout)
    budget = agent_recon.ReconBudget()
    with pytest.raises(agent_recon.ReconError, match="侦察超时") as caught:
        await agent_recon.reconnoiter("U", "C", "https://u.edu/", timeout_seconds=0.3,
                                      config=_cfg(), budget=budget)
    assert stopped.wait(2)
    assert caught.value.usage_unknown is True  # an interrupted in-flight request may still be billed
    assert caught.value.diagnostics["cost"]["estimated_usd"] is None
    assert caught.value.diagnostics["model_calls"] > 0


@pytest.mark.asyncio
async def test_worker_fails_fast_on_phase_limit_without_repair_rounds(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "outputs",
                         max_recon_repairs=2, max_branch_calls=25)
    task = SimpleNamespace(task_id="flat-article", school="样本大学", college="样本学院",
                           url="https://example.edu/")
    calls = []

    async def no_cache(*_):
        return None

    async def flailing(*_args, phase_call_limits, **_kwargs):
        calls.append(phase_call_limits)
        error = agent_recon.ReconPhaseLimitExceeded("分支验证阶段已用 26 次模型请求仍未完成（上限 25）")
        error.diagnostics = {"model_calls": 26, "token_usage": {"uncached_input": 1, "cached_input": 2, "output": 3}}
        error.usage_unknown = False
        raise error

    monkeypatch.setattr(worker, "_cached_recon", no_cache)
    monkeypatch.setattr("server.agent_worker.reconnoiter", flailing)
    root = tmp_path / "outputs" / "tasks" / task.task_id
    with pytest.raises(agent_recon.ReconPhaseLimitExceeded):
        await worker._harvest_with_repair(task, root)
    assert calls == [{"discovery": 0, "branch": 25}]
    audit = json.loads((root / "recon-attempts.json").read_text(encoding="utf-8"))
    assert audit["status"] == "recon_phase_limit" and audit["usage_unknown"] is False
    assert audit["attempts"][0]["model_calls"] == 26


@pytest.mark.asyncio
@pytest.mark.browser
async def test_screenshot_is_a_bounded_jpeg_and_identical_snapshots_are_not_duplicated(tmp_path):
    from playwright.async_api import async_playwright
    from server.recon_browser_mcp import BrowserScout, SCREENSHOT_MAX_HEIGHT

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 720})
        await page.set_content('<div id="tall" style="height:9000px;background:#ddd">tall roster</div>')
        scout = BrowserScout(page, tmp_path, phase="branch")
        for _ in range(3):
            await scout.call("browser_evaluate", {"function": "() => document.title"})
        viewport = await scout.call("browser_screenshot", {})
        element = await scout.call("browser_screenshot", {"selector": "#tall"})
        await browser.close()

    assert len(list(tmp_path.glob("*.html"))) == 1
    for reply in (viewport, element):
        image = reply["content"][1]
        assert image["mimeType"] == "image/jpeg"
        data = base64.b64decode(image["data"])
        assert data[:2] == b"\xff\xd8"
    from PIL import Image
    import io
    width, height = Image.open(io.BytesIO(base64.b64decode(element["content"][1]["data"]))).size
    assert height <= SCREENSHOT_MAX_HEIGHT and width <= 1280
