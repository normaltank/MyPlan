"""A person can close a college that publishes no faculty roster (badr 2026-10-09).

集美大学继续教育学院 and 国际教育学院 have no 师资 menu: one was collected from the
organisation page (office heads, not faculty), the other ran out of reconnaissance time.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from server import app as web
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue


def _record(college, name):
    return {"序号": 1, "姓名": name, "职称": "教师", "邮箱": "", "学校": "集美大学", "学院": college,
            "采集人": "fixture", "采集日期": "2026-10-09", "简介": "",
            "主页": "https://ae.x.edu.cn/xygk/xyjg.htm"}


def _finish(queue, task, rows):
    claimed = queue.claim_task("fixture", task_id=task.task_id)
    queue.complete_task(task.task_id, rows, {"total": len(rows), "with_email": 0, "no_email": len(rows)},
                        worker_id="fixture", lease_token=claimed.lease_token)


def _fail(queue, task):
    claimed = queue.claim_task("fixture", task_id=task.task_id)
    queue.sync_task_result(task.task_id, [], {}, "侦察失败：侦察超时（900s）", "failed", "fixture",
                           claimed.lease_token)


def test_failed_and_wrongly_collected_colleges_complete_with_zero_teachers(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    teaching = queue.submit_task("集美大学", "诚毅学院", mode="batch", batch_id="b")
    offices = queue.submit_task("集美大学", "继续教育学院", mode="batch", batch_id="b")
    timeout = queue.submit_task("集美大学", "国际教育学院", mode="batch", batch_id="b")
    _finish(queue, teaching, [_record("诚毅学院", "林静")])
    _finish(queue, offices, [_record("继续教育学院", "陈成涛")])
    _fail(queue, timeout)
    assert queue.get_delivery_scope("b", "集美大学")["state"] == "blocked"

    assert queue.mark_no_faculty(offices.task_id, 7) and queue.mark_no_faculty(timeout.task_id, 7)
    for task_id, previous in ((offices.task_id, "completed"), (timeout.task_id, "failed")):
        task = queue.get_task(task_id)
        assert task.status == "completed" and task.records == [] and task.error is None
        assert task.stats["total"] == 0 and task.stats["no_faculty"]["reviewed_by"] == 7
        assert task.stats["no_faculty"]["previous_status"] == previous
    assert queue.get_task(offices.task_id).stats["no_faculty"]["previous_total"] == 1
    assert queue.get_delivery_scope("b", "集美大学")["state"] == "ready"
    names = [row["姓名"] for row in queue.get_university_snapshot("集美大学")["records"]]
    assert names == ["林静"]


def test_only_a_finished_college_can_be_judged(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    waiting = queue.submit_task("集美大学", "师范学院")
    assert not queue.mark_no_faculty(waiting.task_id, 1)
    queue.claim_task("fixture", task_id=waiting.task_id)
    assert not queue.mark_no_faculty(waiting.task_id, 1)
    assert queue.get_task(waiting.task_id).status == "running"


def test_web_verdict_needs_a_signed_in_person(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / "tasks.db")
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "account_store", AccountStore(queue.db_path))
    task = queue.submit_task("集美大学", "国际教育学院")
    _fail(queue, task)
    assert TestClient(web.app).post(f"/api/task/{task.task_id}/no-faculty").status_code == 401
    browser = TestClient(web.app)
    assert browser.post("/api/auth/name", json={"display_name": "badr"}).status_code == 200
    csrf = browser.get("/api/auth/me").json()["csrf_token"]
    response = browser.post(f"/api/task/{task.task_id}/no-faculty", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert queue.get_task(task.task_id).stats["no_faculty"]["reviewed_by"] is not None
    again = browser.post(f"/api/task/{task.task_id}/no-faculty", headers={"X-CSRF-Token": csrf})
    # The first verdict keeps what it replaced; judging again would overwrite that record.
    assert again.status_code == 409
