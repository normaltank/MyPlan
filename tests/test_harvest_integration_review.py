"""Independent integration review regressions; no network or production DB."""
import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from server import agent_recon, agent_worker
from server.agent_recon import ReconResult
from server.agent_worker import AgentWorker
from server.browser_batch_tool import run_plan
from server.harvest_state import HarvestState
from test_harvest_acceptance import CASES, ReplayContext


@pytest.fixture(autouse=True)
def offline_review(monkeypatch, tmp_path):
    monkeypatch.setenv("HARVEST_TASK_DB", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("HARVEST_DISABLE_NATIVE_CHROME_FALLBACK", "1")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["directory", "profile", "navigation_child"])
async def test_worker_access_failure_retries_same_plan_without_recon(tmp_path, monkeypatch, failure):
    case = next(case for case in CASES if case["id"] == "ordinary_full_biography")
    plan = copy.deepcopy(case["plan"])
    pages = copy.deepcopy(case["pages"])
    pages.pop("https://acceptance.example.edu/roster" if failure in {"directory", "navigation_child"}
              else "https://acceptance.example.edu/p/3.htm")
    browser_context = ReplayContext(pages)
    if failure == "navigation_child":
        root = "https://acceptance.example.edu/departments"
        plan["branches"] = [{"url": root, "kind": "navigation", "child_link_selector": "main a",
                              "child_branch": {"single_page": True, "card_selector": ".cards > li"}}]
        browser_context.pages[root] = [{"url": root,
                                       "html": '<main><a href="/roster">Faculty branch</a></main>'}]
    failed_report = await run_plan(plan, browser_context, tmp_path / "failed")
    assert failed_report["delivery_ready"] is False
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "outputs")
    task = SimpleNamespace(school=plan["school"], college=plan["college"],
                           url=plan["branches"][0]["url"], task_id="review-access-task")
    calls = {"recon": 0, "harvest": 0}

    async def scout(*_args, **_kwargs):
        calls["recon"] += 1
        return ReconResult(plan, 0, "completed", "review-scout")

    async def browser(*_args):
        calls["harvest"] += 1
        return copy.deepcopy(failed_report) if calls["harvest"] == 1 else {
            "delivery_ready": True, "unresolved": []}

    async def cache(*_args):
        return None

    async def unaffected(*_args):
        return None

    monkeypatch.setattr(agent_worker, "reconnoiter", scout)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    monkeypatch.setattr(worker, "_cached_recon", cache)
    monkeypatch.setattr(worker, "_unaffected_branches", unaffected)
    report = await worker._harvest_with_repair(task, tmp_path / "task")
    assert report["delivery_ready"] is True
    assert calls["harvest"] == 2
    assert calls["recon"] == 1, "access-only failures must not spend another model reconnaissance"


def event_pair(name, arguments, result, call_id):
    return [{"type": "tool/call", "data": {"callId": call_id,
             "name": "mcp__playwright__" + name, "arguments": json.dumps(arguments)}},
            {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": call_id,
             "content": [{"type": "text", "text": json.dumps(result)}]}]}}}]


def test_fresh_branch_cannot_borrow_another_branch_phase_verification(monkeypatch):
    urls = ["https://review.example.edu/a", "https://review.example.edu/b"]
    target = {"school": "Review University", "college": "Review College", "url": "https://review.example.edu/"}

    def execute(_prompt, sid, config, _patches):
        if config["phase"] == "discovery":
            value = {"school": target["school"], "college": target["college"],
                     "source_kind": "university_faculty_roster", "branches": [{"url": url} for url in urls]}
            events = event_pair("browser_navigation", {}, {"items": [{"url": url} for url in urls]}, sid)
        else:
            url = urls[0] if sid.endswith("branch-1") else urls[1]
            value = {"url": url, "card_selector": "main li", "single_page": True}
            # The first branch happened to inspect /b. The /b branch session
            # itself does no browser verification and must not pass its gate.
            events = []
            if url == urls[0]:
                for index, observed in enumerate(urls):
                    events += event_pair("browser_verify", {"card_selector": "main li"},
                                         {"url": observed, "cardCount": 3, "pagination": {"total": 0}},
                                         sid + str(index))
        return SimpleNamespace(final_response=json.dumps(value), finish_reason="completed", events=events)

    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    with pytest.raises(agent_recon.ReconPlanError):
        agent_recon._run_staged_harness("review", {"target": target, "timeout_seconds": 60}, ())


@pytest.mark.asyncio
async def test_run_plan_rejects_state_bound_to_a_different_complete_plan(tmp_path):
    case = next(case for case in CASES if case["id"] == "ajax_same_url")
    original = copy.deepcopy(case["plan"])
    state = HarvestState(tmp_path / "task", original)
    await run_plan(original, ReplayContext(case["pages"]), tmp_path / "first", state=state)
    changed = copy.deepcopy(original)
    changed["profile_ready_selector"] = "article.ready"
    with pytest.raises(ValueError):
        await run_plan(changed, ReplayContext({}), tmp_path / "changed", state=state)


@pytest.mark.asyncio
async def test_recon_transport_failure_persists_known_phase_tokens(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "output")
    task = SimpleNamespace(school="Review University", college="Review College",
                           url="https://review.example.edu/", task_id="review-error")

    async def cache(*_args):
        return None

    async def fail(*_args, **_kwargs):
        error = agent_recon.ReconError("transport stopped after a completed phase")
        error.diagnostics = {"session_id": "review-transport", "model_calls": 2,
                             "token_usage": {"uncached_input": 120, "cached_input": 20, "output": 10},
                             "phases": [{"phase": "discovery", "model_calls": 2}]}
        raise error

    monkeypatch.setattr(worker, "_cached_recon", cache)
    monkeypatch.setattr(agent_worker, "reconnoiter", fail)
    root = tmp_path / "failed-task"
    with pytest.raises(agent_recon.ReconError):
        await worker._harvest_with_repair(task, root)
    audit = json.loads((root / "recon-attempts.json").read_text(encoding="utf-8"))
    assert audit["status"] == "recon_failed"
    assert audit["usage_unknown"] is True
    assert audit["attempts"][0]["model_calls"] == 2
    assert audit["attempts"][0]["token_usage"] == {"uncached_input": 120, "cached_input": 20, "output": 10}


@pytest.mark.asyncio
async def test_exhausted_rejected_plans_persist_each_usage_without_raw_response(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "output", max_recon_repairs=2)
    task = SimpleNamespace(school="Review University", college="Review College",
                           url="https://review.example.edu/", task_id="review-rejected")
    calls = 0
    rejected_text = "Rejected teacher field: 王拒绝原文 forbidden.person@example.edu"

    async def cache(*_args):
        return None

    async def fail(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        error = agent_recon.ReconPlanError(rejected_text)
        error.diagnostics = {"session_id": f"review-rejection-{calls}", "model_calls": 1,
                             "token_usage": {"uncached_input": calls * 100, "cached_input": 5, "output": 10}}
        raise error

    monkeypatch.setattr(worker, "_cached_recon", cache)
    monkeypatch.setattr(agent_worker, "reconnoiter", fail)
    root = tmp_path / "rejected-task"
    report = await worker._harvest_with_repair(task, root)
    assert report["delivery_ready"] is False
    audit_path = root / "recon-attempts.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit["status"] == "plan_contract_rejected" and audit["usage_unknown"] is False
    # The same rejection twice with no newly verified branch stops early (2026-09-29 rule).
    assert len(audit["attempts"]) == calls == 2
    assert sum(attempt["model_calls"] for attempt in audit["attempts"]) == 2
    assert sum(attempt["token_usage"]["uncached_input"] for attempt in audit["attempts"]) == 300
    for path in root.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "王拒绝原文" not in text and "forbidden.person@example.edu" not in text


def test_timeout_marks_usage_unknown_and_preserves_known_prior_attempts(tmp_path):
    attempts = [{"model_calls": 2, "token_usage": {"uncached_input": 230, "cached_input": 25, "output": 15}}]
    AgentWorker._save_recon_audit(tmp_path, attempts, "recon_completed")
    AgentWorker._save_recon_audit(tmp_path, None, "task_timeout", usage_unknown=True)
    audit = json.loads((tmp_path / "recon-attempts.json").read_text(encoding="utf-8"))
    assert audit == {"status": "task_timeout", "usage_unknown": True, "attempts": attempts}


@pytest.mark.asyncio
async def test_failed_task_stats_link_the_token_audit_digest(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "output")
    task = SimpleNamespace(school="Review University", college="Review College",
                           task_id="review-stats", worker_id="review-worker", lease_token="review-local-lease")
    root = worker.output_dir / "tasks" / task.task_id
    worker._save_recon_audit(root, [{"model_calls": 1}], "plan_contract_rejected")
    saved = []
    monkeypatch.setattr(worker.queue, "sync_task_result", lambda *_args, **_kwargs: saved.append(_args))
    await worker._mark_failed(task, "plan_contract_rejected")
    stats = saved[0][2]
    path = root / "recon-attempts.json"
    assert stats["recon_audit_path"] == str(path)
    assert stats["recon_audit_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_model_request_count_includes_failed_run_completed_usage_messages():
    events = [
        {"type": "assistant/message", "data": {"usage": {"inputTokens": 10, "cacheReadTokens": 2, "outputTokens": 3}}},
        {"type": "assistant/message", "data": {"usage": {"inputTokens": 20, "cacheReadTokens": 4, "outputTokens": 6}}},
        {"type": "assistant/message", "data": {"text": "no additional model request usage"}},
    ]
    metrics = agent_recon.run_metrics(events)
    assert metrics["model_calls"] == 2
    assert metrics["token_usage"] == {"uncached_input": 30, "cached_input": 6, "output": 9}
