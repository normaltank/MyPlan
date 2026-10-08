"""A failed college shows what it waits on; the queue row stays 'failed'."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from server.agent_queue import AgentTaskQueue, failure_disposition
from server.recovery_policy import failure_disposition as from_codes


def test_most_demanding_action_wins():
    assert from_codes(["image_bio_low_confidence"]) == "review"
    assert from_codes(["image_bio_low_confidence", "browser_navigation_failed"]) == "access"
    assert from_codes(["image_bio_low_confidence", "pagination_not_verified"]) == "structure"
    assert from_codes(["pagination_not_verified", "delivery_sync_failure"]) == "failed"
    assert from_codes(["recon_timeout"]) == "recon"
    assert from_codes(["recon_budget_exceeded"]) == "failed"
    assert from_codes(["unclassified_failure"]) == "failed"
    assert from_codes([]) == "failed"


def test_legacy_error_text_without_codes_is_still_sorted():
    assert failure_disposition("侦察失败：侦察超时（900s）", {}) == "recon"
    assert failure_disposition("同步回后台失败：OSError: x", {}) == "failed"
    assert failure_disposition("审计未通过交付门禁：unresolved=1 ['segment_name_needs_review']", {}) == "review"


def _fail(queue, task, error, stats):
    claimed = queue.claim_task("worker", task_id=task.task_id)
    queue.sync_task_result(task.task_id, [], stats, "", error,
                           worker_id="worker", lease_token=claimed.lease_token)


def test_batch_and_daily_counts_use_one_definition(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    tasks = queue.submit_batch([("甲大学", f"学院{i}", f"https://a.edu/{i}") for i in range(5)], "batch-a")
    claimed = queue.claim_task("worker", task_id=tasks[0].task_id)
    queue.complete_task(claimed.task_id, [], {"total": 0, "with_email": 0, "no_email": 0},
                        worker_id="worker", lease_token=claimed.lease_token)
    _fail(queue, tasks[1], "审计未通过交付门禁", {"failure_codes": ["image_bio_low_confidence"]})
    _fail(queue, tasks[2], "审计未通过交付门禁",
          {"failure_codes": ["image_bio_low_confidence", "pagination_not_verified"]})
    _fail(queue, tasks[3], "侦察失败：侦察超时（900s）", {})
    _fail(queue, tasks[4], "同步回后台失败：OSError: disk", {})

    status = queue.get_batch_status("batch-a")
    assert status["status"] == "partially_failed" and status["failed_colleges"] == 4
    assert status["failure_dispositions"] == {"review": 1, "structure": 1, "recon": 1, "failed": 1}
    assert status["pending_colleges"] == 3 and status["terminal_failed_colleges"] == 1

    today = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    daily = queue.get_daily_counts(today)
    assert daily["failed_colleges"] == 4
    assert daily["pending_colleges"] == 3 and daily["terminal_failed_colleges"] == 1
