"""Durable queue, leases, fencing, and legacy web adapter contracts."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from server.agent_queue import AgentTaskQueue, LeaseConflictError


def test_submit_claim_complete_survives_restart(tmp_path):
    db = tmp_path / "tasks.db"
    q = AgentTaskQueue(db)
    assert q.get_queue_stats()["total_tasks"] == 0
    task = q.submit_task("甲大学", "乙学院", "https://example.edu", batch_id="b1")
    claimed = q.claim_task("worker-a")
    records = [{"姓名": "张三", "职称": "教授", "邮箱": "z@x.edu.cn", "简介": "全文", "主页": "https://x.edu.cn/z", "学院": "乙学院"}]
    q.complete_task(task.task_id, records, {"total": 1, "with_email": 1, "no_email": 0}, worker_id="worker-a", lease_token=claimed.lease_token)
    restarted = AgentTaskQueue(db, legacy_json_path=None)
    saved = restarted.get_task(task.task_id)
    assert saved.status == "completed" and saved.records == records and saved.stats["total"] == 1
    assert restarted.get_batch_status("b1")["completed_colleges"] == 1


def test_two_workers_cannot_claim_same_task(tmp_path):
    db = tmp_path / "tasks.db"
    q = AgentTaskQueue(db, legacy_json_path=None)
    q.submit_task("甲", "乙")
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda w: AgentTaskQueue(db, legacy_json_path=None).claim_task(w), ["a", "b"]))
    assert sum(x is not None for x in claimed) == 1


def test_expired_lease_reclaimed_after_restart_and_old_owner_fenced(tmp_path):
    db = tmp_path / "tasks.db"
    q = AgentTaskQueue(db, legacy_json_path=None)
    task = q.submit_task("甲", "乙")
    first = q.claim_task("old", lease_seconds=0.01)
    assert first and first.task_id == task.task_id
    import time
    time.sleep(0.025)
    restarted = AgentTaskQueue(db, legacy_json_path=None)
    assert task.task_id in [pending.task_id for pending in restarted.get_pending_tasks()]
    second = restarted.claim_task("new")
    assert second and second.task_id == task.task_id
    assert not restarted.renew_lease(task.task_id, "old")
    assert not restarted.renew_lease(task.task_id, "new", lease_token=None)
    assert restarted.renew_lease(task.task_id, "new", lease_token=second.lease_token)
    with pytest.raises(LeaseConflictError):
        restarted.complete_task(task.task_id, [{"name": "张三"}], {}, worker_id="old")
    with pytest.raises(LeaseConflictError):
        restarted.sync_task_result(task.task_id, [], {}, "", error="old worker failed", worker_id="old")
    assert restarted.get_task(task.task_id).status == "running"


def test_compile_ticket_deduplicates_and_claims_as_compiling(tmp_path):
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    one = q.create_compile_ticket("cluster-1", "compile this")
    two = q.create_compile_ticket("cluster-1", "compile this again")
    assert one.task_id == two.task_id
    assert one.task_type == "compile_ticket"
    claimed = q.claim_task("compiler")
    assert claimed and claimed.status == "compiling" and claimed.task_id == one.task_id


def test_legacy_claim_and_sync_adapter_persists_records(tmp_path):
    db = tmp_path / "tasks.db"
    q = AgentTaskQueue(db, legacy_json_path=None)
    task = q.submit_task("甲", "乙")
    claimed = q.claim_task(task.task_id, claimant="IDE")
    assert claimed.status == "running"
    assert q.sync_task_result(task.task_id, [{"姓名": "张三"}], {"total": 1, "with_email": 0, "no_email": 1}, "/download/x.xlsx", worker_id="IDE", lease_token=claimed.lease_token).status == "completed"
    assert AgentTaskQueue(db, legacy_json_path=None).get_task(task.task_id).download_url == "/download/x.xlsx"


def test_inconsistent_sheet_stats_cannot_complete(tmp_path):
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = q.submit_task("甲", "乙")
    claimed = q.claim_task("worker")
    with pytest.raises(ValueError, match="conservation"):
        q.complete_task(task.task_id, [{"name": "张三"}],
                        {"total": 1, "with_email": 1, "no_email": 1, "unresolved": 0},
                        worker_id="worker", lease_token=claimed.lease_token)
    assert q.get_task(task.task_id).status == "running"


def test_one_time_legacy_migration_keeps_preview_and_reclaims_old_running_task(tmp_path):
    legacy = tmp_path / "agent_queue.json"
    legacy.write_text(json.dumps({"tasks": {"old": {
        "task_id": "old", "school": "甲", "college": "乙", "status": "running",
        "teachers_preview": [{"姓名": "张三"}], "created_at": 1.0}},
        "batch_tasks": {}}, ensure_ascii=False), encoding="utf-8")
    db = tmp_path / "tasks.db"
    q = AgentTaskQueue(db, legacy_json_path=legacy)
    assert q.get_task("old").teachers_preview == [{"姓名": "张三"}]
    assert q.claim_task("new").task_id == "old"
    assert AgentTaskQueue(db, legacy_json_path=legacy).get_queue_stats()["total_tasks"] == 1
