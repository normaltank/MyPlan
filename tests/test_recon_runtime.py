from types import SimpleNamespace
from pathlib import Path

import pytest
import yaml

from server import agent_recon, agent_worker


def test_phase_and_evidence_namespace_are_explicit_in_mcp_child_config(tmp_path):
    patch = tmp_path / "runtime.yml"
    agent_recon.write_runtime_patch(patch, phase="discovery", session_id="recon-case-discover")
    entries = yaml.safe_load(patch.read_text(encoding="utf-8"))
    entries = [child for entry in entries for child in entry.get("insert", [entry])]
    mcp = next(item["config"] for item in entries if item.get("config", {}).get("serverName") == "playwright")
    assert mcp["env"]["FACULTY_RECON_PHASE"] == "discovery"
    assert mcp["env"]["FACULTY_RECON_SESSION"] == "recon-case-discover"


def test_scout_bounds_browser_results_and_requires_complete_evidence():
    """The deployed profile must bound results even when the model returns full DOM."""
    root = Path(agent_recon.__file__).parent
    patch = yaml.safe_load((root / "recon_runtime.cordis.yml").read_text(encoding="utf-8"))
    entries = {entry["id"]: entry for entry in patch}
    assert entries["spill-policy"]["config"]["maxInlineTokens"] == 12500
    assert entries["spill-local"]["config"]["cleanupPeriodDays"] == 0
    instructions = (root / "recon_instructions.md").read_text(encoding="utf-8")
    assert "省略" in instructions and "定向" in instructions


def test_repair_feedback_never_copies_records_or_names():
    plan = {"school": "样本大学", "college": "样本学院", "source_kind": "university_faculty_roster",
            "branches": [{"url": "https://example.edu/list", "card_selector": "main ul > li", "single_page": True}]}
    report = {"records": [{"name": "秘密姓名", "email": "secret@example.edu"}],
              "unresolved": [{"status": "card_selector_empty", "url": "https://example.edu/list",
                              "name": "秘密姓名", "detail": "教师简介"}]}
    result = agent_recon.build_repair_feedback(plan, report)
    text = str(result)
    assert "card_selector_empty" in text
    assert "秘密姓名" not in text and "secret@example.edu" not in text and "教师简介" not in text


def test_run_metrics_reports_real_usage_without_message_content():
    events = [{"type": "step/start", "data": {"step": 1}},
              {"type": "tool/call", "data": {"name": "mcp__playwright__browser_evaluate"}},
              {"type": "assistant/message", "data": {"usage": {"inputTokens": 100, "cacheReadTokens": 200,
                       "outputTokens": 30}, "message": {"content": "secret"}}}]
    result = agent_recon.run_metrics(events)
    assert result["token_usage"] == {"uncached_input": 100, "cached_input": 200, "output": 30}
    assert result["steps"] == 1 and result["tool_calls"] == 1
    assert "secret" not in str(result)


@pytest.mark.asyncio
async def test_worker_returns_to_recon_after_failed_audit(tmp_path, monkeypatch):
    worker = agent_worker.AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    task = SimpleNamespace(school="样本大学", college="样本学院", url="https://example.edu/list")
    feedbacks = []
    plan = {"school": task.school, "college": task.college, "source_kind": "university_faculty_roster",
            "branches": [{"url": task.url, "card_selector": "main ul > li", "single_page": True}]}

    async def recon(*args, **kwargs):
        feedbacks.append(kwargs.get("feedback"))
        # The repair proposes a different selector; an identical plan would stop early.
        branch = {**plan["branches"][0], "card_selector": f"main ul:nth-of-type({len(feedbacks)}) > li"}
        return agent_recon.ReconResult({**plan, "branches": [branch]}, 0.1, "completed", "sample")

    async def collect(_plan, _output):
        if len(feedbacks) == 1:
            return {"delivery_ready": False, "unresolved": [{"status": "card_selector_empty", "url": task.url}]}
        return {"delivery_ready": True, "unresolved": []}

    monkeypatch.setattr(agent_worker, "reconnoiter", recon)
    monkeypatch.setattr(worker, "_run_browser_plan", collect)
    report = await worker._harvest_with_repair(task, tmp_path / "out")
    assert report["delivery_ready"] is True
    assert len(feedbacks) == 2 and feedbacks[0] is None
    assert feedbacks[1]["issues"][0]["status"] == "card_selector_empty"
    assert len(report["recon_attempts"]) == 2


@pytest.mark.asyncio
async def test_failed_audit_stops_at_configured_repair_budget(tmp_path, monkeypatch):
    worker = agent_worker.AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out",
                                     max_recon_repairs=1)
    task = SimpleNamespace(school="样本大学", college="样本学院", url="https://example.edu/list")
    plan = {"school": task.school, "college": task.college, "source_kind": "university_faculty_roster",
            "branches": [{"url": task.url, "card_selector": "main ul > li", "single_page": True}]}
    calls = []

    async def recon(*args, **kwargs):
        calls.append(kwargs.get("feedback"))
        return agent_recon.ReconResult(plan, 0.1, "completed", "sample")

    async def collect(*args):
        return {"delivery_ready": False, "unresolved": [{"status": "card_selector_empty"}]}

    monkeypatch.setattr(agent_worker, "reconnoiter", recon)
    monkeypatch.setattr(worker, "_run_browser_plan", collect)
    result = await worker._harvest_with_repair(task, tmp_path / "out")
    assert not result["delivery_ready"] and len(calls) == 2


@pytest.mark.asyncio
async def test_rejected_model_plan_returns_safe_feedback_for_repair(tmp_path, monkeypatch):
    worker = agent_worker.AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    task = SimpleNamespace(school="样本大学", college="样本学院", url="https://example.edu/list")
    plan = {"school": task.school, "college": task.college, "source_kind": "university_faculty_roster",
            "branches": [{"url": task.url, "card_selector": "main ul > li", "single_page": True}]}
    calls = []

    async def recon(*args, **kwargs):
        calls.append(kwargs.get("feedback"))
        if len(calls) == 1:
            raise agent_recon.PlanHygieneError("教师秘密姓名 secret@example.edu")
        return agent_recon.ReconResult(plan, 0.1, "completed", "sample")

    async def collect(*args):
        return {"delivery_ready": True, "unresolved": []}

    monkeypatch.setattr(agent_worker, "reconnoiter", recon)
    monkeypatch.setattr(worker, "_run_browser_plan", collect)
    report = await worker._harvest_with_repair(task, tmp_path / "out")
    assert report["delivery_ready"] and len(calls) == 2
    assert calls[1] == {"issues": [{"status": "plan_contract_rejected"}]}
    assert "秘密" not in str(report) and "secret@example.edu" not in str(report)
