"""Failure counts describe current failed college jobs, not website shapes."""
from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient

from server import app as web
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue
from server.site_type_registry import SiteTypeRegistry


def _fail(queue: AgentTaskQueue, error: str, stats: dict | None = None):
    task = queue.submit_task("样本大学", "样本学院", "https://example.edu/roster")
    claimed = queue.claim_task("worker", task_id=task.task_id)
    queue.sync_task_result(task.task_id, [], stats or {}, "", error,
                           worker_id="worker", lease_token=claimed.lease_token)
    return task


def test_structured_failure_codes_are_counted_once_and_retry_removes_them(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = _fail(queue, "审计未通过交付门禁：unresolved=5 ['roster_card_mismatch']",
                 {"failure_codes": ["roster_card_mismatch", "image_ocr_uncertain",
                                    "image_ocr_uncertain"]})
    counts = queue.get_failure_type_stats()
    assert counts["failed_tasks"] == 1
    assert {(row["reason_code"], row["task_count"]) for row in counts["types"]} == {
        ("roster_card_mismatch", 1), ("image_ocr_uncertain", 1)}
    assert all(row["partial_count"] == 0 for row in counts["types"])
    assert queue.retry_failed_review_task(task.task_id)
    after_retry = queue.get_failure_type_stats()
    assert after_retry["failed_tasks"] == 0 and after_retry["types"] == []
    assert after_retry["historical_failure_events"] == 1
    assert {row["reason_code"] for row in after_retry["historical_types"]} == {
        "roster_card_mismatch", "image_ocr_uncertain"}


def test_restart_backfills_legacy_failures_and_does_not_duplicate(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = _fail(queue, "审计未通过交付门禁：unresolved=8 "
                       "['profile_content_selector_conflict', 'roster_card_mismatch']")
    type_db = tmp_path / "outputs" / "site-types.sqlite3"
    with sqlite3.connect(type_db) as con:
        con.execute("DELETE FROM failure_type_observations")
    restarted = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    counts = restarted.get_failure_type_stats()
    assert counts["failed_tasks"] == 1
    assert len(counts["types"]) == 2
    assert all(row["partial_count"] == 1 for row in counts["types"])
    assert restarted.get_failure_type_stats() == counts
    assert counts["historical_failure_events"] == 1
    with sqlite3.connect(type_db) as con:
        assert con.execute("SELECT COUNT(DISTINCT task_id) FROM failure_type_observations").fetchone()[0] == 1
        assert con.execute("SELECT DISTINCT task_id FROM failure_type_observations").fetchone()[0] == task.task_id


def test_repeated_failure_of_same_task_adds_historical_event(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = _fail(queue, "工单缺少入口 URL")
    assert queue.retry_failed_review_task(task.task_id)
    claimed = queue.claim_task("worker", task_id=task.task_id)
    queue.sync_task_result(task.task_id, [], {}, "", "侦察失败：侦察超时（900s）",
                           worker_id="worker", lease_token=claimed.lease_token)
    counts = queue.get_failure_type_stats()
    assert counts["failed_tasks"] == 1
    assert [row["reason_code"] for row in counts["types"]] == ["recon_timeout"]
    assert counts["historical_failure_events"] == 2
    assert {row["reason_code"] for row in counts["historical_types"]} == {
        "missing_entry_url", "recon_timeout"}


def test_unknown_error_is_recorded_without_raw_message_or_teacher_data(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    _fail(queue, "教师张三的原始异常详情")
    counts = queue.get_failure_type_stats()
    assert counts["types"][0]["reason_code"] == "unclassified_failure"
    type_db = tmp_path / "outputs" / "site-types.sqlite3"
    with sqlite3.connect(type_db) as con:
        assert "张三" not in "".join(str(row) for row in con.execute(
            "SELECT * FROM failure_type_observations"))


def test_reclassifying_same_failure_event_does_not_double_history(tmp_path):
    registry = SiteTypeRegistry(tmp_path / "types.sqlite3")
    row = {"task_id": "task-1", "school": "甲", "college": "乙",
           "url": "https://example.edu", "stats_json": "{}", "finished_at": 123.0,
           "error": "侦察失败：未知"}
    registry.reconcile_failures([row])
    registry.reconcile_failures([{**row, "error": "侦察失败：成本保护已停止异常高消耗运行"}])
    counts = registry.failure_counts()
    assert counts["historical_failure_events"] == 1
    assert [item["reason_code"] for item in counts["historical_types"]] == [
        "recon_cost_guard_manual_stop"]


def test_site_shape_rows_remain_separate_from_failure_rows(tmp_path):
    registry = SiteTypeRegistry(tmp_path / "types.sqlite3")
    evidence = tmp_path / "page.html"
    evidence.write_text("<img>", encoding="utf-8")
    registry.record(school="甲", college="乙", entry_url="https://example.edu",
                    branch_url="https://example.edu/roster", shape_code="image_bio",
                    features={}, evidence_path=evidence)
    assert registry.failure_counts() == {"failed_tasks": 0, "types": [],
                                         "historical_failure_events": 0,
                                         "historical_types": []}


def test_anyone_signed_in_can_read_failure_counts(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    store = AccountStore(queue.db_path)
    _fail(queue, "工单缺少入口 URL")
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "account_store", store)
    route = "/api/failure-types"
    assert TestClient(web.app).get(route).status_code == 401
    staff = TestClient(web.app)
    assert staff.post("/api/auth/name", json={"display_name": "staff"}).status_code == 200
    report = staff.get(route)
    assert report.status_code == 200
    assert report.json()["types"][0]["reason_code"] == "missing_entry_url"
