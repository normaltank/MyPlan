"""A failed sync must not be reported to operators as a delivery."""

import pytest

from server.agent_worker import AgentWorker
import server.browser_batch_tool as browser_batch_tool


@pytest.mark.asyncio
async def test_missing_audit_file_marks_failed_without_success_log(tmp_path, monkeypatch, capsys):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    queued = worker.queue.submit_task("样本大学", "样本学院", "https://example.edu/roster")
    task = worker.queue.claim_task("worker-1", task_id=queued.task_id)
    assert task is not None

    async def completed_harvest(*_args, **_kwargs):
        return {"delivery_ready": True, "unresolved": [],
                "audit_path": str(tmp_path / "missing_audit.json")}

    monkeypatch.setattr(browser_batch_tool, "run_with_lease", completed_harvest)
    await worker._process(task)

    assert worker.queue.get_task(task.task_id).status == "failed"
    assert "已交付" not in capsys.readouterr().out
