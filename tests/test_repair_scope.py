"""A structural repair stays inside the branch it concerns."""
from types import SimpleNamespace

import pytest

from server import agent_worker
from server.agent_recon import ReconResult, build_repair_feedback
from server.agent_worker import AgentWorker

A, B = "https://scope.example.edu/a", "https://scope.example.edu/b"
PLAN = {"school": "Scope University", "college": "Scope College", "branches": [
    {"url": A, "card_selector": "main li", "single_page": True, "profile_content_selector": "div.body-a"},
    {"url": B, "card_selector": "main li", "single_page": True, "profile_content_selector": "div.body-b"}]}
PROFILE_ISSUE = {"url": "https://scope.example.edu/p/7.htm", "status": "profile_content_selector_hidden",
                 "selector": "div.body-a"}


def test_profile_issue_names_the_one_branch_that_declared_its_selector():
    issue = build_repair_feedback(PLAN, {"unresolved": [PROFILE_ISSUE]})["issues"][0]
    assert issue["branch_url"] == A and issue["url"] == PROFILE_ISSUE["url"]
    shared = {**PLAN, "branches": [{**branch, "profile_content_selector": "div.body"} for branch in PLAN["branches"]]}
    issue = build_repair_feedback(shared, {"unresolved": [{**PROFILE_ISSUE, "selector": "div.body"}]})["issues"][0]
    assert "branch_url" not in issue, "a selector shared by several branches does not locate the failure"


@pytest.mark.asyncio
@pytest.mark.parametrize("attributed", [True, False])
async def test_branch_repair_reuses_accepted_discovery(tmp_path, monkeypatch, attributed):
    monkeypatch.setenv("HARVEST_TASK_DB", str(tmp_path / "tasks.db"))
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "outputs")
    task = SimpleNamespace(school=PLAN["school"], college=PLAN["college"], url="https://scope.example.edu/",
                           task_id="scope-task")
    checkpoint = {"plan": {"branches": [{"url": A}, {"url": B}]}, "events": [], "session_id": "scope-discover"}
    calls = []
    issue = PROFILE_ISSUE if attributed else {**PROFILE_ISSUE, "selector": "div.unknown"}

    repaired = {**PLAN, "branches": [{**PLAN["branches"][0], "profile_content_selector": "div.inner-a"},
                                     PLAN["branches"][1]]}

    async def scout(*_args, **kwargs):
        calls.append(kwargs)
        return ReconResult(PLAN if len(calls) == 1 else repaired, 0, "completed", f"scope-{len(calls)}",
                           discovery_checkpoint=checkpoint)

    async def browser(*_args):
        return ({"delivery_ready": False, "unresolved": [dict(issue)]} if len(calls) == 1
                else {"delivery_ready": True, "unresolved": []})

    async def cache(*_args):
        return None

    async def unaffected(plan, feedback, root):
        damaged = worker._damaged_branches(plan, feedback)
        return {B: {"branch": PLAN["branches"][1], "events": []}} if damaged == {A} else None

    monkeypatch.setattr(agent_worker, "reconnoiter", scout)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    monkeypatch.setattr(worker, "_cached_recon", cache)
    monkeypatch.setattr(worker, "_unaffected_branches", unaffected)
    report = await worker._harvest_with_repair(task, tmp_path / "task")
    assert report["delivery_ready"] is True and len(calls) == 2
    repair = calls[1]
    if attributed:
        assert repair["resume_discovery"] is checkpoint
        assert set(repair["verified_branches"]) == {B}
        assert repair["feedback"]["issues"][0]["branch_url"] == A
    else:
        # An issue no accepted branch explains may need a new entry: discover again.
        assert "resume_discovery" not in repair and "verified_branches" not in repair


def test_damaged_branch_is_scouted_with_its_issue_not_copied_from_a_template(monkeypatch):
    from test_recon_optimizations import TARGET, URLS, branch, discovery, result
    from server import agent_recon
    prompts, checked = {}, []

    def execute(prompt, sid, cfg, patches):
        if cfg["phase"] == "discovery":
            return discovery()
        url = next(u for u in URLS if u in prompt)
        prompts[url] = prompt
        value, proof = branch(url)
        return result(value, proof)

    def verify(value, target, root, timeout, expected):
        checked.append(value["url"])
        _, proof = branch(value["url"])
        return {"valid": True, "events": proof, "evidence_root": str(root)}

    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    monkeypatch.setattr("server.recon_templates.inspect_templates", lambda *_: dict.fromkeys(URLS, "same"))
    monkeypatch.setattr("server.recon_templates.verify_template", verify)
    issue = {"status": "roster_card_mismatch", "url": URLS[2], "visible_cards": 12, "extracted_candidates": 11}
    agent_recon._run_staged_harness("damaged", {"target": TARGET, "timeout_seconds": 10,
        "parallel_branches": True, "template_reuse": True, "feedback": {"issues": [issue]}}, ())
    assert set(prompts) == {URLS[0], URLS[2]} and checked == [URLS[1]]
    assert "roster_card_mismatch" in prompts[URLS[2]] and "roster_card_mismatch" not in prompts[URLS[0]]


@pytest.mark.asyncio
async def test_unchanged_repair_plan_is_not_captured_again(tmp_path, monkeypatch):
    monkeypatch.setenv("HARVEST_TASK_DB", str(tmp_path / "tasks.db"))
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "outputs")
    task = SimpleNamespace(school=PLAN["school"], college=PLAN["college"], url="https://scope.example.edu/",
                           task_id="scope-unchanged")
    scouts, captures = [], []
    mismatch = {"url": A, "status": "roster_card_mismatch", "visible_cards": 12, "extracted_candidates": 11}

    async def scout(*_args, **kwargs):
        scouts.append(kwargs)
        return ReconResult(PLAN, 0, "completed", f"scope-{len(scouts)}")

    async def browser(*_args):
        captures.append(1)
        return {"delivery_ready": False, "unresolved": [dict(mismatch)]}

    async def nothing(*_args):
        return None

    monkeypatch.setattr(agent_worker, "reconnoiter", scout)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    monkeypatch.setattr(worker, "_cached_recon", nothing)
    monkeypatch.setattr(worker, "_unaffected_branches", nothing)
    report = await worker._harvest_with_repair(task, tmp_path / "task")
    assert len(scouts) == 2 and len(captures) == 1
    assert report["unresolved"][0]["status"] == "roster_card_mismatch"
    assert report["recon_attempts"][-1]["status"] == "repair_plan_unchanged"


def test_issue_on_a_later_roster_page_names_its_branch(tmp_path):
    import json
    coverage = tmp_path / "coverage.json"
    coverage.write_text(json.dumps({"pages": [{"url": A + "/2.htm", "branch_url": A}]}), encoding="utf-8")
    issues = [{"status": "roster_card_mismatch", "url": A + "/2.htm"}, {"status": "x", "url": "https://z/"}]
    mapped = AgentWorker._with_branch_urls(issues, str(coverage))
    assert mapped[0]["branch_url"] == A and "branch_url" not in mapped[1]
    feedback = build_repair_feedback(PLAN, {"unresolved": mapped[:1]})
    assert AgentWorker._damaged_branches(PLAN, feedback) == {A}
    assert AgentWorker._with_branch_urls(issues, None) == issues
