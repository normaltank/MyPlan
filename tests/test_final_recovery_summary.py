"""Final failure audits reflect every issue added after capture."""
import json

import pytest

from server import agent_worker
from server.agent_worker import AgentWorker
from server.agent_recon import ReconResult, ReconGateStopped
from server.agent_queue import failure_disposition
from server.recovery_policy import classify_issues


@pytest.mark.asyncio
@pytest.mark.parametrize('exit_path', ['gate_reject', 'deadline'])
async def test_final_report_recovery_matches_complete_final_issues(tmp_path, monkeypatch, exit_path):
    worker = AgentWorker(db_path=tmp_path/'tasks.db', output_dir=tmp_path/'output', max_recon_repairs=1)
    queued = worker.queue.submit_task('U', 'C', 'https://example.edu/roster')
    task = worker.queue.claim_task('independent-spec', task_id=queued.task_id)
    plan = {'school': 'U', 'college': 'C', 'branches': [{'url': task.url, 'card_selector': 'li', 'single_page': True}]}
    calls = 0
    audit_paths = []

    async def scout(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise ReconGateStopped('recon_page_unverifiable', [{'url': 'https://example.edu/p/1', 'status': 'browser_navigation_failed'}])
        return ReconResult(plan, 0, 'completed', 'fixture')

    async def capture(_plan, directory):
        audit = directory/'audit.json'
        result = {'delivery_ready': False, 'total': 1, 'records': [{'name': 'retained'}], 'audit_path': str(audit),
                  'unresolved': [{'url': 'https://example.edu/p/1', 'status': 'profile_content_selector_missing'}]}
        audit.write_text(json.dumps(result), encoding='utf-8')
        audit_paths.append(audit)
        return result

    async def none(*args):
        return None

    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(worker, '_run_browser_plan', capture)
    monkeypatch.setattr(worker, '_cached_recon', none)
    monkeypatch.setattr(worker, '_unaffected_branches', none)
    if exit_path == 'deadline':
        monkeypatch.setattr(worker, '_recon_window', lambda deadline, attempt: 100 if attempt == 0 else None)
    await worker._process(task)
    report = json.loads(audit_paths[0].read_text(encoding='utf-8'))
    saved = worker.queue.get_task(task.task_id)
    facts = {'case': exit_path, 'final_unresolved': report['unresolved'], 'reported_recovery': report['recovery'],
             'expected_recovery': classify_issues(report['unresolved']).summary(),
             'queue_disposition': failure_disposition(saved.error, saved.stats), 'retained_records': report['records']}
    assert report['records'] == [{'name': 'retained'}]
    assert report['recovery'] == facts['expected_recovery']

