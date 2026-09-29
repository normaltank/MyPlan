"""The backend receipt must independently reject contradictory coverage claims."""
import pytest
from server.agent_queue import AgentTaskQueue


@pytest.mark.parametrize('coverage', [
    {'complete':False,'issues':[]},
    {'complete':True,'issues':[{'status':'branch_termination_unverified'}]},
    'malformed',
])
def test_incomplete_coverage_receipt_keeps_lease_and_records_unchanged(tmp_path, coverage):
    queue=AgentTaskQueue(tmp_path/'queue.db')
    task=queue.submit_task('University','College','https://example.edu/')
    claimed=queue.claim_task('worker',task_id=task.task_id)
    stats={'total':1,'with_email':0,'no_email':1,'unresolved':0,'coverage':coverage}
    row={'姓名':'Person','主页':'https://example.edu/p/1','邮箱':''}
    with pytest.raises(ValueError,match='coverage'):
        queue.complete_task(task.task_id,[row],stats,worker_id='worker',lease_token=claimed.lease_token)
    unchanged=queue.get_task(task.task_id)
    assert unchanged.status=='running' and unchanged.records==[]
    assert unchanged.lease_token==claimed.lease_token


def test_complete_coverage_receipt_is_preserved_in_task_stats(tmp_path):
    queue=AgentTaskQueue(tmp_path/'queue.db')
    task=queue.submit_task('University','College','https://example.edu/')
    claimed=queue.claim_task('worker',task_id=task.task_id)
    coverage={'complete':True,'issues':[],'scope':'verified_roster_branches'}
    stats={'total':1,'with_email':0,'no_email':1,'unresolved':0,'coverage':coverage}
    queue.complete_task(task.task_id,[{'姓名':'Person','主页':'https://example.edu/p/1','邮箱':''}],
                        stats,worker_id='worker',lease_token=claimed.lease_token)
    assert queue.get_task(task.task_id).stats['coverage']==coverage
