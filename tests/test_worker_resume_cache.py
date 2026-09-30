"""Offline worker orchestration: cache, task recovery and local access retries."""
import asyncio
import copy
import hashlib
import json
import shutil
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import server.agent_worker as worker_module
import server.browser_batch_tool as browser_module
from server.agent_recon import ReconPlanError, ReconResult
from server.agent_worker import AgentWorker
from server.harvest_state import HarvestState
from server.browser_context import IsolatedBrowserContext


PLAN = {"school": "University", "college": "College",
        "source_kind": "university_faculty_roster", "branches": [
            {"url": "https://example.edu/list", "card_selector": "main li", "single_page": True}]}


def worker_and_task(tmp_path, *, repairs=2):
    worker = AgentWorker(db_path=tmp_path / "queue.db", output_dir=tmp_path / "outputs",
                         max_recon_repairs=repairs)
    queued = worker.queue.submit_task("University", "College", "https://example.edu/")
    task = worker.queue.claim_task("fixture", task_id=queued.task_id)
    root = worker.output_dir / "tasks" / task.task_id
    root.mkdir(parents=True)
    return worker, task, root


def recon_result(*, cached=False):
    return ReconResult(copy.deepcopy(PLAN), 0.0, "cache_hit" if cached else "stop", "fixture-session",
        diagnostics={"model_calls": 0 if cached else 1,
                     "token_usage": {"uncached_input": 0 if cached else 17,
                                     "cached_input": 0, "output": 0 if cached else 3}})


async def no_cache(*_args, **_kwargs):
    return None


async def validated(*_args, **_kwargs):
    return {"valid": True, "model_calls": 0, "browser_tool_calls": 1}


def completed_report(directory):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "audit.json"
    report = {"delivery_ready": True, "unresolved": [], "audit_path": str(path)}
    path.write_text(json.dumps(report), encoding="utf-8")
    return report


@pytest.mark.asyncio
async def test_validated_cache_avoids_model_and_saves_zero_metrics(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path)
    cache_calls = []
    async def cached(target, directory):
        cache_calls.append((target.task_id, directory))
        return recon_result(cached=True)
    async def model_forbidden(*_args, **_kwargs):
        pytest.fail("validated cache must not invoke model reconnaissance")
    async def browser(plan, directory):
        assert plan == PLAN
        return completed_report(directory)
    monkeypatch.setattr(worker, "_cached_recon", cached, raising=False)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    monkeypatch.setattr(worker_module, "reconnoiter", model_forbidden)
    report = await worker._harvest_with_repair(task, root)
    assert cache_calls == [(task.task_id, root)]
    assert report["recon"]["model_calls"] == 0
    assert report["recon"]["token_usage"] == {"uncached_input": 0, "cached_input": 0, "output": 0}
    saved = json.loads(open(report["audit_path"], encoding="utf-8").read())
    assert saved["recon"]["model_calls"] == 0
    assert (root / "active-plan.json").is_file()


@pytest.mark.asyncio
async def test_plan_repair_reuses_same_task_discovery_only(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path)
    calls = []
    checkpoint = {"plan": {"school": "University", "college": "College",
                           "source_kind": "university_faculty_roster",
                           "branches": [{"url": PLAN["branches"][0]["url"]}]},
                  "events": [], "session_id": "first-discover"}
    verified = {"https://example.edu/other": {"branch": {"url": "https://example.edu/other",
                "card_selector": "main li", "single_page": True}, "events": []}}

    async def model(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            error = ReconPlanError("branch plan missing selector")
            error.safe_feedback = {"issues": [{"status": "profile_content_selector_missing",
                                                "url": PLAN["branches"][0]["url"]}]}
            error.discovery_checkpoint = checkpoint
            error.verified_branches = verified
            error.diagnostics = {"model_calls": 3, "token_usage": {
                "uncached_input": 100, "cached_input": 200, "output": 50}}
            raise error
        return recon_result()

    async def browser(_plan, directory):
        return completed_report(directory)

    monkeypatch.setattr(worker, "_cached_recon", no_cache)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    monkeypatch.setattr(worker_module, "reconnoiter", model)
    report = await worker._harvest_with_repair(task, root)
    assert report["delivery_ready"] is True
    assert calls[1]["resume_discovery"] is checkpoint
    assert calls[1]["verified_branches"] is verified
    assert calls[1]["feedback"]["issues"][0]["status"] == "profile_content_selector_missing"
    saved = json.loads((root / "recon-attempts.json").read_text(encoding="utf-8"))
    assert saved["attempts"][0]["issues"][0]["status"] == "profile_content_selector_missing"


@pytest.mark.asyncio
async def test_identical_contract_failure_stops_after_no_progress(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path)
    worker.max_recon_repairs = 4
    calls = []

    async def model(*_args, **kwargs):
        calls.append(kwargs)
        error = ReconPlanError("same unresolved structure")
        error.safe_feedback = {"issues": [{"status": "profile_content_selector_missing",
                                            "url": PLAN["branches"][0]["url"]}]}
        error.diagnostics = {"model_calls": 1, "token_usage": {
            "uncached_input": 100, "cached_input": 0, "output": 10}}
        raise error

    monkeypatch.setattr(worker, "_cached_recon", no_cache)
    monkeypatch.setattr(worker_module, "reconnoiter", model)
    report = await worker._harvest_with_repair(task, root)
    assert not report["delivery_ready"]
    assert len(calls) == 2
    assert len(report["recon_attempts"]) == 2


def test_default_worker_does_not_enforce_fixed_college_call_or_token_cap(tmp_path):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "output")
    assert worker.max_recon_calls == 0
    assert worker.max_recon_tokens == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["profile_navigation_failed", "browser_navigation_failed"])
async def test_access_failure_retries_same_plan_without_model_repair(tmp_path, monkeypatch, status):
    worker, task, root = worker_and_task(tmp_path)
    model_calls, plans = [], []
    async def model(*_args, **kwargs):
        model_calls.append(kwargs.get("feedback"))
        return recon_result()
    async def browser(plan, directory):
        plans.append(copy.deepcopy(plan))
        if len(plans) == 1:
            return {"delivery_ready": False, "unresolved": [
                {"status": status, "url": "https://example.edu/p/failed", "branch_url": PLAN["branches"][0]["url"]}]}
        return completed_report(directory)
    monkeypatch.setattr(worker, "_cached_recon", no_cache, raising=False)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    monkeypatch.setattr(worker_module, "reconnoiter", model)
    report = await worker._harvest_with_repair(task, root)
    assert report["delivery_ready"]
    assert len(model_calls) == 1
    assert plans == [PLAN, PLAN]
    assert len(report["local_access_repairs"]) == 1
    assert report["local_access_repairs"][0]["model_calls"] == 0


@pytest.mark.asyncio
async def test_access_retries_stop_after_two_local_repairs(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path, repairs=5)
    model_calls, browser_calls = [], []
    async def model(*_args, **_kwargs):
        model_calls.append(True)
        return recon_result()
    async def browser(plan, directory):
        browser_calls.append(plan)
        return {"delivery_ready": False, "unresolved": [
            {"status": "profile_navigation_failed", "url": "https://example.edu/p/failed"}]}
    monkeypatch.setattr(worker, "_cached_recon", no_cache, raising=False)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    monkeypatch.setattr(worker_module, "reconnoiter", model)
    report = await worker._harvest_with_repair(task, root)
    assert not report["delivery_ready"]
    assert len(model_calls) == 1
    assert len(browser_calls) == 3
    assert len(report["local_access_repairs"]) == 2
    assert all(repair["model_calls"] == 0 for repair in report["local_access_repairs"])


@pytest.mark.asyncio
async def test_interrupted_task_recovers_plan_without_model_and_marks_resume(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path)
    model_calls = []
    async def model(*_args, **_kwargs):
        model_calls.append(True)
        return recon_result()
    async def interrupted(*_args):
        raise asyncio.CancelledError
    monkeypatch.setattr(worker, "_cached_recon", no_cache, raising=False)
    monkeypatch.setattr(worker, "_run_browser_plan", interrupted)
    monkeypatch.setattr(worker_module, "reconnoiter", model)
    with pytest.raises(asyncio.CancelledError):
        await worker._harvest_with_repair(task, root)
    assert (root / "active-plan.json").is_file()
    resumed = AgentWorker(db_path=worker.queue.db_path, output_dir=worker.output_dir)
    async def browser(plan, directory):
        assert plan == PLAN
        return completed_report(directory)
    monkeypatch.setattr(resumed, "_cached_recon", no_cache, raising=False)
    monkeypatch.setattr(resumed, "_validate_structure", validated, raising=False)
    monkeypatch.setattr(resumed, "_run_browser_plan", browser)
    report = await resumed._harvest_with_repair(task, root)
    assert len(model_calls) == 1
    assert report["recon"]["model_calls"] == 0
    saved = json.loads(open(report["audit_path"], encoding="utf-8").read())
    assert "resum" in json.dumps(saved).lower()


@pytest.mark.asyncio
async def test_expired_active_plan_requires_new_recon(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path)
    model_calls = []
    async def model(*_args, **_kwargs):
        model_calls.append(True)
        return recon_result()
    async def interrupted(*_args):
        raise asyncio.CancelledError
    monkeypatch.setattr(worker, "_cached_recon", no_cache, raising=False)
    monkeypatch.setattr(worker, "_run_browser_plan", interrupted)
    monkeypatch.setattr(worker_module, "reconnoiter", model)
    with pytest.raises(asyncio.CancelledError):
        await worker._harvest_with_repair(task, root)
    active_path = root / "active-plan.json"
    saved = json.loads(active_path.read_text(encoding="utf-8"))
    saved["saved_at"] = time.time() - 90000
    saved.pop("digest", None)
    saved["digest"] = hashlib.sha256(json.dumps(saved, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    active_path.write_text(json.dumps(saved), encoding="utf-8")
    async def browser(plan, directory):
        return completed_report(directory)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    report = await worker._harvest_with_repair(task, root)
    assert report["delivery_ready"]
    assert len(model_calls) == 2


@pytest.mark.asyncio
async def test_browser_runner_injects_task_plan_checkpoint_store(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path)
    closed, stores = [], []
    context = object()
    class Browser:
        async def new_context(self):
            return context
        async def close(self):
            closed.append(True)
    class Chromium:
        async def launch(self, **_kwargs):
            return Browser()
    @asynccontextmanager
    async def playwright():
        yield SimpleNamespace(chromium=Chromium())
    async def run_plan(plan, actual_context, directory, *, state=None):
        assert isinstance(actual_context, IsolatedBrowserContext)
        assert actual_context.raw_context is context
        assert isinstance(state, HarvestState)
        assert state.directory.parent.parent == root.resolve()
        if stores:
            assert state.load_page("profile", "https://example.edu/p/success") == "<main>saved full page</main>"
        else:
            state.save_page("profile", "https://example.edu/p/success", "<main>saved full page</main>")
        stores.append(state)
        return {"delivery_ready": True, "unresolved": []}
    monkeypatch.setattr(worker, "_playwright", playwright)
    monkeypatch.setattr(browser_module, "run_plan", run_plan)
    await worker._run_browser_plan(copy.deepcopy(PLAN), root / "attempt-1")
    await worker._run_browser_plan(copy.deepcopy(PLAN), root / "attempt-2")
    assert stores[0].plan_digest == stores[1].plan_digest
    assert stores[1].load_page("profile", "https://example.edu/p/success") == "<main>saved full page</main>"
    assert closed == [True, True]


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_cached_recon_requires_fresh_structure_validation(tmp_path, monkeypatch, valid):
    from server.recon_plan_cache import ReconPlanCache
    worker, task, root = worker_and_task(tmp_path)
    discovery = [{"url": task.url, "links": [PLAN["branches"][0]["url"]], "fingerprint": "a" * 64}]
    ReconPlanCache(worker.output_dir / "plan-cache").put(copy.deepcopy(PLAN), task.url, discovery)
    validations = []
    async def validate(entry, directory, *, verify_discovery=True):
        validations.append((entry["plan"], verify_discovery))
        return {"valid": valid, "model_calls": 0, "reason": "fixture_structure_changed",
                "discovery_pages": discovery, "browser_tool_calls": 3, "evidence_root": str(directory)}
    monkeypatch.setattr(worker, "_validate_structure", validate)
    result = await worker._cached_recon(task, root)
    assert validations == [(PLAN, True)]
    if valid:
        assert isinstance(result, ReconResult)
        assert result.summary()["model_calls"] == 0
        assert result.summary()["cache_hit"]
        assert result.summary()["token_usage"] == {"uncached_input": 0, "cached_input": 0, "output": 0}
    else:
        assert result is None
        assert json.loads((root / "cache-miss.json").read_text(encoding="utf-8"))["reason"] == "fixture_structure_changed"


@pytest.mark.asyncio
@pytest.mark.parametrize("valid", [True, False])
async def test_only_browser_verified_unaffected_branch_is_reused(tmp_path, monkeypatch, valid):
    worker, task, root = worker_and_task(tmp_path)
    broken = PLAN["branches"][0]
    good = {"url": "https://example.edu/other", "card_selector": "main article", "single_page": True}
    plan = {**PLAN, "branches": [broken, good]}
    validations = []
    async def validate(entry, directory, *, verify_discovery=True):
        validations.append((entry["plan"]["branches"], verify_discovery))
        return {"valid": valid, "events": [{"type": "fixture_browser_evidence"}], "model_calls": 0,
                "evidence_root": str(directory)}
    monkeypatch.setattr(worker, "_validate_structure", validate)
    verified = await worker._unaffected_branches(plan, {"issues": [
        {"status": "roster_selector_empty", "branch_url": broken["url"]}]}, root)
    assert validations == [([good], False)]
    if valid:
        assert set(verified) == {good["url"]}
        assert verified[good["url"]]["branch"] == good
        assert verified[good["url"]]["events"]
    else:
        assert verified is None


@pytest.mark.asyncio
async def test_structure_error_reconnoiters_and_passes_only_verified_branch(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path)
    broken = PLAN["branches"][0]
    good = {"url": "https://example.edu/other", "card_selector": "main article", "single_page": True}
    plan = {**PLAN, "branches": [broken, good]}
    model_kwargs, runs = [], []
    async def model(*_args, **kwargs):
        model_kwargs.append(kwargs)
        recon = recon_result()
        recon.plan = copy.deepcopy(plan)
        if len(model_kwargs) > 1:  # the repair changes the broken branch
            recon.plan["branches"][0]["card_selector"] = "main ol > li"
        return recon
    async def browser(active, directory):
        runs.append(active)
        if len(runs) == 1:
            return {"delivery_ready": False, "unresolved": [
                {"status": "roster_selector_empty", "branch_url": broken["url"]}]}
        return completed_report(directory)
    async def validate(entry, directory, *, verify_discovery=True):
        assert entry["plan"]["branches"] == [good]
        assert not verify_discovery
        return {"valid": True, "events": [{"type": "fixture_browser_evidence"}], "model_calls": 0}
    monkeypatch.setattr(worker, "_cached_recon", no_cache)
    monkeypatch.setattr(worker, "_validate_structure", validate)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    monkeypatch.setattr(worker_module, "reconnoiter", model)
    report = await worker._harvest_with_repair(task, root)
    assert report["delivery_ready"]
    assert len(model_kwargs) == 2
    assert model_kwargs[1]["feedback"]["issues"][0]["branch_url"] == broken["url"]
    assert set(model_kwargs[1]["verified_branches"]) == {good["url"]}


@pytest.mark.asyncio
async def test_active_plan_from_other_task_does_not_bypass_new_recon(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path)
    model_calls = []
    async def model(*_args, **_kwargs):
        model_calls.append(True)
        return recon_result()
    async def interrupted(*_args):
        raise asyncio.CancelledError
    monkeypatch.setattr(worker, "_cached_recon", no_cache)
    monkeypatch.setattr(worker, "_run_browser_plan", interrupted)
    monkeypatch.setattr(worker_module, "reconnoiter", model)
    with pytest.raises(asyncio.CancelledError):
        await worker._harvest_with_repair(task, root)
    other = worker.queue.submit_task(task.school, task.college, task.url)
    other_task = worker.queue.claim_task("fixture", task_id=other.task_id)
    other_root = root.parent / other.task_id
    other_root.mkdir()
    shutil.copyfile(root / "active-plan.json", other_root / "active-plan.json")
    async def browser(plan, directory):
        return completed_report(directory)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    report = await worker._harvest_with_repair(other_task, other_root)
    assert report["delivery_ready"]
    assert len(model_calls) == 2
    assert not report["recon"].get("resumed", False)


@pytest.mark.asyncio
async def test_structure_validator_passes_discovery_flag_and_closes_browser(tmp_path, monkeypatch):
    import server.recon_plan_cache as cache_module
    worker, task, root = worker_and_task(tmp_path)
    seen, closed = [], []
    context = object()
    class Browser:
        async def new_context(self):
            return context
        async def close(self):
            closed.append(True)
    class Chromium:
        async def launch(self, **_kwargs):
            return Browser()
    @asynccontextmanager
    async def playwright():
        yield SimpleNamespace(chromium=Chromium())
    async def validate(entry, actual_context, evidence_dir, *, verify_discovery=True):
        assert isinstance(actual_context, IsolatedBrowserContext)
        assert actual_context.raw_context is context
        seen.append(verify_discovery)
        return {"valid": True, "model_calls": 0, "events": []}
    monkeypatch.setattr(worker, "_playwright", playwright)
    monkeypatch.setattr(cache_module, "validate_cached_plan", validate)
    first = await worker._validate_structure({"plan": PLAN}, root / "fresh")
    second = await worker._validate_structure({"plan": PLAN}, root / "branch", verify_discovery=False)
    assert first["valid"] and second["valid"]
    assert seen == [True, False]
    assert closed == [True, True]


def test_modified_active_plan_is_rejected_before_browser_or_model(tmp_path):
    worker, task, root = worker_and_task(tmp_path)
    recon = recon_result()
    worker._save_active_plan(task, root, recon, [recon.summary()])
    path = root / "active-plan.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["plan"]["branches"][0]["card_selector"] = ".changed-without-new-evidence"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert worker._resume_plan(task, root) is None
