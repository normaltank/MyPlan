"""The submission gate says what failed and ends sessions no resubmission can pass."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from server.agent_recon import (ReconGateStopped, ReconPlanError, _watch_gate, last_gate_rejections,
                                selector_evidence_issues)
from server.agent_worker import AgentWorker, gate_rejection_summary
from server.recon_browser_mcp import BrowserScout
from server.recovery_policy import classify_issues, failure_disposition

URL = "https://gate.example.edu/list"
PROFILES = ["https://gate.example.edu/p/1", "https://gate.example.edu/p/2", "https://gate.example.edu/p/3"]
TARGET = {"school": "样本大学", "college": "样本学院", "url": URL}
BRANCH = {"url": URL, "single_page": True, "card_selector": ".cards>li", "profile_content_selector": "article"}
PLAN = {"school": TARGET["school"], "college": TARGET["college"], "branches": [BRANCH]}
BLOCKING = [{"type": "fetch", "status": 500, "path": "/api/visits"}]


def event(name, args, result, cid):
    return [{"type": "tool/call", "data": {"callId": cid, "name": "mcp__playwright__" + name,
                                             "arguments": json.dumps(args)}},
            {"type": "tool/result", "data": {"message": {"content": [
                {"toolCallId": cid, "content": [{"type": "text", "text": json.dumps(result)}]}]}}}]


def roster(samples):
    return event("browser_verify", {"card_selector": ".cards>li"},
                 {"url": URL, "cardCount": len(samples), "pagination": {"total": 0}, "profileSamples": samples}, "r")


def blocked_profile(url, cid):
    return event("browser_verify_profile", {"profile_content_selector": "article"},
                 {"url": url, "profile_content_selector": "article", "profile_content_start_selector": None,
                  "confirmed": False, "reason": "network_not_idle", "container_count": 1, "dom_stable": True,
                  "network_idle": False, "page_blocking_requests": BLOCKING}, cid)


def body(reply):
    return json.loads(reply["content"][0]["text"])


@pytest.mark.asyncio
async def test_rejection_names_the_branch_the_failed_condition_and_the_missing_proof(tmp_path):
    scout = BrowserScout(None, tmp_path, "branch", TARGET)
    reply = body(await scout.call("submit_plan", {"plan": {key: value for key, value in BRANCH.items() if key != "profile_content_selector"}}))
    [issue] = reply["issues"]
    assert reply["accepted"] is False and "stop" not in reply
    assert issue["url"] == URL and issue["selector"] == ".cards>li"
    assert issue["failed_condition"] == "url_never_verified" and issue["need"]


@pytest.mark.asyncio
async def test_identical_resubmission_is_not_judged_again_and_the_second_ends_the_session(tmp_path):
    scout = BrowserScout(None, tmp_path, "branch", TARGET)
    first = body(await scout.call("submit_plan", {"plan": BRANCH}))
    again = body(await scout.call("submit_plan", {"plan": BRANCH}))
    assert again["unchanged_resubmission"] == first["submission"] == 1
    assert again["issues"] == first["issues"] and "stop" not in again
    # New browser evidence makes the same plan a new question for the gate.
    scout.events.extend(roster(PROFILES[:1]))
    fresh = body(await scout.call("submit_plan", {"plan": BRANCH}))
    assert fresh["submission"] == 2 and "unchanged_resubmission" not in fresh
    assert body(await scout.call("submit_plan", {"plan": BRANCH}))["unchanged_resubmission"] == 2
    assert body(await scout.call("submit_plan", {"plan": BRANCH}))["stop"] == "recon_gate_stalled"


def test_profile_failure_is_page_only_when_too_few_linked_pages_can_still_pass():
    two = roster(PROFILES[:2])
    [issue] = selector_evidence_issues(PLAN, two)
    assert issue["failed_condition"] == "profile_proof_missing" and "page_only" not in issue
    assert [sample["condition"] for sample in issue["closest"]["samples"]] == ["not_opened", "not_opened"]
    # Two proofs are needed from two linked pages: one page blocking every container settles it.
    [issue] = selector_evidence_issues(PLAN, two + blocked_profile(PROFILES[0], "1"))
    assert issue["page_only"] is True
    assert issue["closest"]["samples"][0]["condition"] == "network_not_idle"
    assert issue["closest"]["samples"][0]["page_blocking_requests"] == BLOCKING
    # A third linked profile might still pass: the scout can try it.
    three = roster(PROFILES) + blocked_profile(PROFILES[0], "1")
    assert "page_only" not in selector_evidence_issues(PLAN, three)[0]
    assert selector_evidence_issues(PLAN, three + blocked_profile(PROFILES[1], "2"))[0]["page_only"]
    # A page the harvest names must pass itself; no other page can stand in.
    def proved(url, cid):
        return event("browser_verify_profile", {"profile_content_selector": "article"},
                     {"url": url, "profile_content_selector": "article", "profile_content_start_selector": None,
                      "confirmed": True, "container_count": 1, "container_visible": True, "dom_stable": True,
                      "network_idle": True, "html_sha256": "a" * 64, "selected_html_sha256": "b" * 64}, cid)
    events = two + proved(PROFILES[0], "1") + proved(PROFILES[1], "2")
    repair = {URL: ["https://gate.example.edu/p/9"]}
    assert selector_evidence_issues(PLAN, events) == []
    assert "page_only" not in selector_evidence_issues(PLAN, events, repair)[0]
    assert selector_evidence_issues(PLAN, events + blocked_profile(repair[URL][0], "3"), repair)[0]["page_only"]


@pytest.mark.asyncio
async def test_gate_ends_the_session_when_every_failure_is_a_page_fact(tmp_path):
    scout = BrowserScout(None, tmp_path, "branch", TARGET)
    scout.events.extend(roster(PROFILES[:2]) + blocked_profile(PROFILES[0], "1") + blocked_profile(PROFILES[1], "2"))
    reply = body(await scout.call("submit_plan", {"plan": BRANCH}))
    assert reply["stop"] == "recon_page_unverifiable"
    # A plan the scout can still fix never ends the session on a page fact alone.
    reply = body(await scout.call("submit_plan", {"plan": {**BRANCH, "card_selector": ".other>li"}}))
    assert "stop" not in reply


def test_harness_watch_keeps_the_last_rejection_and_raises_the_stop():
    rejections, calls = {}, set()
    issue = {"path": "$", "status": "profile_content_not_verified", "url": URL, "page_only": True}
    def reply(value, cid="s1"):
        return [{"type": "tool/call", "data": {"callId": cid, "name": "mcp__playwright__submit_plan"}},
                {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": cid, "isError": True,
                 "content": [{"type": "text", "text": json.dumps(value)}]}]}}}]
    for item in reply({"accepted": False, "issues": [issue]}):
        assert _watch_gate(item, calls, rejections, "sid") is None
    assert last_gate_rejections({"gate_rejections": rejections}) == [{"session_id": "sid", "issues": [issue]}]
    stop = [_watch_gate(item, calls, rejections, "sid")
            for item in reply({"accepted": False, "issues": [issue], "stop": "recon_page_unverifiable"}, "s2")][-1]
    assert isinstance(stop, ReconGateStopped) and isinstance(stop, ReconPlanError)
    assert stop.safe_feedback == {"issues": [{"status": "recon_page_unverifiable", "url": URL, "gate_issues": [issue]}]}
    for item in reply({"accepted": True, "digest": "x"}, "s3"):
        _watch_gate(item, calls, rejections, "sid")
    assert rejections == {}
    error = SimpleNamespace(diagnostics={"last_gate_rejections": [{"session_id": "sid", "issues": [
        {"status": "selector_not_verified", "failed_condition": "card_count_zero"}]}]})
    assert gate_rejection_summary(error) == "；最后一次门禁打回：selector_not_verified/card_count_zero"


def test_page_facts_wait_for_access_while_a_stalled_scout_gets_a_fresh_repair():
    assert classify_issues([{"status": "recon_page_unverifiable"}]).access
    assert classify_issues([{"status": "recon_gate_stalled"}]).structure
    assert failure_disposition(["recon_page_unverifiable"]) == "access"


@pytest.mark.asyncio
async def test_worker_sends_no_repair_scout_after_a_page_fact_stop(tmp_path, monkeypatch):
    from server import agent_worker
    task = SimpleNamespace(task_id="gate-task", school="University", college="College", url=URL)
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "outputs")
    calls = []
    async def scout(*args, **kwargs):
        calls.append(kwargs)
        raise ReconGateStopped("recon_page_unverifiable", [{"status": "profile_content_not_verified", "url": URL}])
    async def no_reuse(*args):
        return None
    monkeypatch.setattr(agent_worker, "reconnoiter", scout)
    monkeypatch.setattr(worker, "_cached_recon", no_reuse)
    report = await worker._harvest_with_repair(task, tmp_path / "outputs/tasks/gate-task")
    assert len(calls) == 1
    assert [item["status"] for item in report["unresolved"]] == ["recon_page_unverifiable"]


@pytest.mark.asyncio
@pytest.mark.browser
async def test_profile_proof_names_failures_that_block_every_container_on_both_opens(tmp_path):
    from server.browser_context import new_browser_context
    page_html = ('<article><p>完整正文。</p></article>'
                 '<script>fetch("/api/visits")</script>')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser)
            await context.add_init_script(Path("server/recon_dom_probe.js").read_text(encoding="utf-8"))
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path)
            served = {"api": 0}
            async def route(request):
                if request.request.url.endswith("/api/visits"):
                    served["api"] += 1
                    await request.fulfill(status=500, body="error")
                else:
                    await request.fulfill(body=page_html, content_type="text/html")
            await page.route("**/*", route)
            await page.goto(PROFILES[0])
            result = body(await scout.call("browser_verify_profile", {"profile_content_selector": "article"}))
            assert result["confirmed"] is False and served["api"] == 2
            assert result["page_blocking_requests"] == [{"type": "fetch", "status": 500, "path": "/api/visits"}]
        finally:
            await browser.close()
