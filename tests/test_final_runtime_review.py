"""Independent checks of phase isolation and original resume timestamps."""
import json
from pagination_contract_fixture import completed_result
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import yaml

from server import agent_recon, agent_worker
from source_contract_fixture import upgrade, source_events


def test_concurrent_colleges_have_independent_runtime_files_and_phase_child_env(monkeypatch):
    barrier = threading.Barrier(2)
    observations = []
    lock = threading.Lock()
    runtime = str(Path(agent_recon.__file__).with_name("recon_runtime.cordis.yml"))
    def execute(prompt, session_id, config, patches):
        path = Path(patches[0])
        assert path != Path(runtime) and path.is_file()
        entries = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = [child for entry in entries for child in entry.get("insert", [entry])]
        mcp = next(e["config"] for e in entries if e.get("config", {}).get("serverName") == "playwright")
        assert mcp["env"]["FACULTY_RECON_PHASE"] == config["phase"]
        assert mcp["env"]["FACULTY_RECON_SESSION"] == session_id
        with lock:
            observations.append((session_id, str(path), config["phase"]))
        target = config["target"]
        roster_url = target["url"] + "list"
        if config["phase"] == "discovery":
            barrier.wait(timeout=5)
            value = {"school": target["school"], "college": target["college"],
                     "source_kind": "university_faculty_roster", "branches": [{"url": roster_url}]}
            name, arguments, result = "browser_navigation", {}, {"links": [roster_url]}
        else:
            value = {"url": roster_url, "card_selector": "main li", "single_page": True}
            name, arguments, result = "browser_verify", {"card_selector": "main li"}, {
                "url": roster_url, "cardCount": 3, "pagination": {"total": 0}}
        result = completed_result(result, arguments)
        events = [{"type": "tool/call", "data": {"callId": session_id,
            "name": "mcp__playwright__" + name, "arguments": json.dumps(arguments)}},
            {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": session_id,
                "content": [{"type": "text", "text": json.dumps(result)}]}]}}}]
        # Runtime isolation is tested with an explicit trusted purpose producer;
        # separate routed Chromium tests verify the actual source witness.
        upgrade(value)
        for index, branch in enumerate(value.get("branches", [value])):
            events.extend(source_events(branch, target, session_id + "-source-" + str(index)))
        return SimpleNamespace(final_response=json.dumps(value), finish_reason="completed", events=events)
    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    def run(index):
        return agent_recon._run_harness_blocking("unused", f"college-{index}", {
            "patches": (runtime,), "timeout_seconds": 60,
            "target": {"school": "Fixture University", "college": f"College {index}",
                       "url": f"https://example.edu/college-{index}/"}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, [1, 2]))
    assert len(results) == 2 and len(observations) == 4
    paths = {path for _, path, _ in observations}
    assert len(paths) == 2
    assert all(not Path(path).exists() for path in paths)
    assert len({sid for sid, _, _ in observations}) == 4


def test_resaving_resumed_active_plan_keeps_original_age(tmp_path, monkeypatch):
    root = tmp_path / "task-a"
    root.mkdir()
    task = SimpleNamespace(task_id="task-a", school="Fixture University", college="Fixture College",
                           url="https://example.edu/")
    plan = {"school": task.school, "college": task.college, "source_kind": "university_faculty_roster",
            "branches": [{"url": task.url + "list", "card_selector": "main li", "single_page": True}]}
    upgrade(plan)
    worker = agent_worker.AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path)
    monkeypatch.setattr(agent_worker.time, "time", lambda: 1000)
    recon = agent_recon.ReconResult(plan, 0, "completed", "fixture")
    worker._save_active_plan(task, root, recon, [recon.summary()])
    monkeypatch.setattr(agent_worker.time, "time", lambda: 2000)
    resumed = worker._resume_plan(task, root)
    assert resumed is not None and resumed.diagnostics["original_saved_at"] == 1000
    worker._save_active_plan(task, root, resumed, [resumed.summary()])
    assert json.loads((root / "active-plan.json").read_text(encoding="utf-8"))["saved_at"] == 1000
    monkeypatch.setattr(agent_worker.time, "time", lambda: 87401)
    assert worker._resume_plan(task, root) is None
