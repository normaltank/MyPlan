"""Tests run on the machine that hosts the production queue; they must not touch it.

Before this guard, one full run on Windows wrote a failed worker self-check
("[拼音] 缺少 pypinyin", from test_worker_refuses_to_start_when_self_check_fails)
into the live server/tasks.db, created server/wrappers.db through
FacultyDirectoryExtractor.extract(), and left MCP evidence in
server/outputs/recon-evidence/standalone.
"""
import os
import sys
from pathlib import Path

import pytest

SERVER = Path(__file__).resolve().parents[1] / "server"


def test_suite_queue_database_is_outside_the_project():
    from server.agent_queue import AgentTaskQueue, default_db_path

    isolated = Path(os.environ["HARVEST_TASK_DB"]).resolve()
    assert not isolated.is_relative_to(SERVER)
    assert default_db_path() == isolated
    assert AgentTaskQueue().db_path.resolve() == isolated


def test_production_default_is_unchanged_without_the_override(monkeypatch):
    from server.agent_queue import default_db_path

    monkeypatch.delenv("HARVEST_TASK_DB", raising=False)
    assert default_db_path() == SERVER / "tasks.db"


def test_wrapper_cache_follows_the_queue_database(monkeypatch, tmp_path):
    from server.runtime_l1 import SQLiteWrapperRegistry

    monkeypatch.setenv("HARVEST_TASK_DB", str(tmp_path / "queue" / "tasks.db"))
    registry = SQLiteWrapperRegistry()
    try:
        assert registry.db_path == tmp_path / "queue" / "wrappers.db"
    finally:
        registry.close()


def test_worker_self_check_is_recorded_in_the_configured_queue(monkeypatch, tmp_path):
    from server import agent_worker, worker_selfcheck
    from server.agent_queue import AgentTaskQueue

    database = tmp_path / "tasks.db"
    monkeypatch.setenv("HARVEST_TASK_DB", str(database))
    monkeypatch.setattr(worker_selfcheck, "run_self_check", lambda: ["[拼音] 缺少 pypinyin"])
    monkeypatch.setattr(sys, "argv", ["agent_worker"])
    with pytest.raises(SystemExit):
        agent_worker.main()
    status = AgentTaskQueue(database).get_worker_status()["self_check"]
    assert status["ok"] is False and status["problems"] == ["[拼音] 缺少 pypinyin"]


def test_recon_evidence_root_is_isolated_for_the_suite():
    from server.agent_recon import recon_evidence_root

    root = recon_evidence_root().resolve()
    assert not root.is_relative_to(SERVER)
    assert root == Path(os.environ["FACULTY_RECON_EVIDENCE_ROOT"]).resolve()


def test_recon_evidence_default_is_unchanged_without_the_override(monkeypatch):
    from server.agent_recon import recon_evidence_root

    monkeypatch.delenv("FACULTY_RECON_EVIDENCE_ROOT", raising=False)
    assert recon_evidence_root() == SERVER / "outputs" / "recon-evidence"
