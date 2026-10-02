"""L1 (known limitation, quantified): two different teachers with the same common name
in two colleges of one batch block the whole university version; both colleges are
'completed', and the only per-college link is the broken one from A4."""
from server.agent_queue import AgentTaskQueue
from server.university_delivery import publish_delivery


def rec(college, name, url, email=""):
    return {"序号": 1, "姓名": name, "职称": "教授", "邮箱": email, "学校": "样本大学", "学院": college,
            "采集人": "w", "采集日期": "2026-10-01", "简介": "简介", "主页": url}


def finish(queue, task, rows):
    claimed = queue.claim_task("w", task_id=task.task_id)
    n = sum(bool(r["邮箱"]) for r in rows)
    queue.complete_task(task.task_id, rows, {"total": len(rows), "with_email": n, "no_email": len(rows) - n},
                        worker_id="w", lease_token=claimed.lease_token)


def test_cross_college_homonym_blocks_university_version(tmp_path):
    queue = AgentTaskQueue(tmp_path / "q.db")
    a = queue.submit_task("样本大学", "化学学院", "https://chem.example.edu/", mode="batch", batch_id="b1")
    b = queue.submit_task("样本大学", "物理学院", "https://phys.example.edu/", mode="batch", batch_id="b1")
    finish(queue, a, [rec("化学学院", "王伟", "https://chem.example.edu/p/1"),
                      rec("化学学院", "李娜", "https://chem.example.edu/p/2")])
    finish(queue, b, [rec("物理学院", "王伟", "https://phys.example.edu/p/9")])
    delivery = publish_delivery(queue, "b1", "样本大学", tmp_path / "outputs")
    print(delivery["delivery_scope"]["state"], delivery.get("university_excel"),
          [c["kind"] for c in delivery["delivery_scope"].get("identity_conflicts", [])])
    assert delivery["delivery_scope"]["state"] == "blocked"
    assert delivery.get("university_excel") is None
    assert all(queue.get_task(t.task_id).status == "completed" for t in (a, b))
