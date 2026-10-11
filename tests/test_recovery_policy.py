"""Recovery spends model calls only on known, actionable structure problems."""
from types import SimpleNamespace

import pytest

from server.agent_recon import ReconResult
from server.agent_worker import AgentWorker
from server.recovery_policy import classify_issues

# These repair-control components start from an independently verified source.
pytestmark = pytest.mark.verified_source_boundary


def test_access_dependency_and_review_are_separate_from_structure_and_unknown():
    issues = [{"status": "browser_navigation_failed", "url": "https://example.edu/list"},
              {"status": "planned_branch_not_captured", "url": "https://example.edu/list"},
              {"status": "planned_branch_not_captured", "url": "https://example.edu/other"},
              {"status": "distinct_profile_identity_unverified"}, {"status": "new_unclassified_error"}]
    recovery = classify_issues(issues)
    assert recovery.access == issues[:2]
    assert recovery.structure == issues[2:3]
    assert recovery.review == issues[3:4]
    assert recovery.stop == issues[4:]


def test_unstable_body_is_access_repair_while_wrong_container_is_structure_repair():
    issues = [{"status": "profile_content_browser_unverified", "browser_evidence": {"reason": reason}}
              for reason in ("dom_unstable", "network_not_idle", "container_not_unique")]
    recovery = classify_issues(issues)
    assert recovery.access == issues[:2]
    assert recovery.structure == issues[2:]


@pytest.mark.asyncio
@pytest.mark.parametrize("unknown", [False, True])
async def test_worker_never_replays_human_or_unknown_issues_to_structure_model(tmp_path, monkeypatch, unknown):
    from server import agent_worker
    task = SimpleNamespace(task_id="review-task", school="University", college="College", url="https://example.edu/")
    plan = {"school": task.school, "college": task.college,
            "branches": [{"url": task.url, "card_selector": "main li", "single_page": True}]}
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "outputs")
    calls, collections = [], []
    async def scout(*args, **kwargs):
        calls.append(kwargs)
        return ReconResult(plan, 0, "completed", "fixture")
    async def collect(*args):
        collections.append(1)
        issues = [{"status": "image_bio_low_confidence", "url": task.url}]
        if len(collections) == 1:
            issues.append({"status": "new_unclassified_error" if unknown else "card_selector_missing", "url": task.url})
        return {"delivery_ready": False, "unresolved": issues}
    async def no_reuse(*args):
        return None
    monkeypatch.setattr(agent_worker, "reconnoiter", scout)
    monkeypatch.setattr(worker, "_cached_recon", no_reuse)
    monkeypatch.setattr(worker, "_unaffected_branches", no_reuse)
    monkeypatch.setattr(worker, "_run_browser_plan", collect)
    report = await worker._harvest_with_repair(task, tmp_path / "outputs/tasks/review-task")
    assert not report["delivery_ready"]
    assert len(calls) == (1 if unknown else 2)
    if not unknown:
        assert {i["status"] for i in calls[1]["feedback"]["issues"]} == {"card_selector_missing"}


@pytest.mark.asyncio
async def test_access_retry_can_recover_while_independent_identity_review_stays_blocking(tmp_path, monkeypatch):
    from server import agent_worker
    task = SimpleNamespace(task_id="review-task", school="University", college="College", url="https://example.edu/")
    plan = {"school": task.school, "college": task.college,
            "branches": [{"url": task.url, "card_selector": "main li", "single_page": True}]}
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "outputs")
    calls, collections = [], []
    async def scout(*args, **kwargs):
        calls.append(1)
        return ReconResult(plan, 0, "completed", "fixture")
    async def collect(*args):
        collections.append(1)
        issues = [{"status": "distinct_profile_identity_unverified"}]
        if len(collections) == 1:
            issues.extend([{"status": "browser_navigation_failed", "url": task.url},
                           {"status": "planned_branch_not_captured", "url": task.url}])
        return {"delivery_ready": False, "unresolved": issues}
    async def no_cache(*args):
        return None
    monkeypatch.setattr(agent_worker, "reconnoiter", scout)
    monkeypatch.setattr(worker, "_cached_recon", no_cache)
    monkeypatch.setattr(worker, "_run_browser_plan", collect)
    report = await worker._harvest_with_repair(task, tmp_path / "outputs/tasks/review-task")
    assert len(calls) == 1 and len(collections) == 2
    assert report["recovery"]["review"]["codes"] == ["distinct_profile_identity_unverified"]
    assert len(report["local_access_repairs"]) == 1
