"""A completed expensive capture survives when another access pass cannot fit."""
import copy
import json

import pytest

from server import agent_worker
from server.agent_recon import ReconResult
from server.agent_worker import AgentWorker
from source_contract_fixture import upgrade


@pytest.mark.asyncio
@pytest.mark.parametrize('remaining,expected_captures', [(900, 1), (2200, 3)])
async def test_access_retry_uses_measured_capture_time(tmp_path, monkeypatch, remaining, expected_captures):
    worker = AgentWorker(db_path=tmp_path/'tasks.db', output_dir=tmp_path/'outputs', max_recon_repairs=0)
    queued = worker.queue.submit_task('U', 'C', 'https://example.edu/')
    task = worker.queue.claim_task('fixture', task_id=queued.task_id)
    root = worker.output_dir/'tasks'/task.task_id
    plan = upgrade({'school': 'U', 'college': 'C', 'branches': [
        {'url': 'https://example.edu/list', 'card_selector': 'li', 'single_page': True}]})
    clock = [0.0]
    captures = []

    async def scout(*args, **kwargs):
        return ReconResult(copy.deepcopy(plan), 0, 'completed', 'fixture')

    async def no_cache(*args, **kwargs):
        return None

    async def capture(_plan, directory):
        clock[0] += 1000
        audit = directory/'audit.json'
        result = {'delivery_ready': False, 'total': 1, 'records': [{'name': 'retained'}],
                  'audit_path': str(audit), 'unresolved': [
                      {'url': 'https://example.edu/p', 'status': 'profile_navigation_failed'}]}
        audit.write_text(json.dumps(result), encoding='utf8')
        captures.append(audit)
        return result

    monkeypatch.setattr(agent_worker.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(worker, '_cached_recon', no_cache)
    monkeypatch.setattr(worker, '_run_browser_plan', capture)
    report = await worker._harvest_with_repair(task, root, deadline=1000 + remaining)
    assert len(captures) == expected_captures
    assert report['records'] == [{'name': 'retained'}]
    assert not report['delivery_ready']
    assert any(i['status'] == 'profile_navigation_failed' for i in report['unresolved'])
    if expected_captures == 1:
        assert any(i['status'] == 'repair_skipped_task_deadline' for i in report['unresolved'])


@pytest.mark.asyncio
async def test_finished_capture_runtime_saved_before_next_pass_is_cancelled(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path/'tasks.db', output_dir=tmp_path/'outputs', max_recon_repairs=0)
    queued = worker.queue.submit_task('U', 'C', 'https://example.edu/')
    task = worker.queue.claim_task('fixture', task_id=queued.task_id)
    root = worker.output_dir/'tasks'/task.task_id
    plan = upgrade({'school': 'U', 'college': 'C', 'branches': [
        {'url': 'https://example.edu/list', 'card_selector': 'li', 'single_page': True}]})
    calls = []

    async def scout(*args, **kwargs):
        return ReconResult(plan, 0, 'completed', 'fixture')

    async def no_cache(*args, **kwargs):
        return None

    async def capture(_plan, directory):
        calls.append(True)
        if len(calls) > 1:
            import asyncio
            raise asyncio.CancelledError
        return {'delivery_ready': False, 'unresolved': [
            {'url': 'https://example.edu/p', 'status': 'profile_navigation_failed'}]}

    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(worker, '_cached_recon', no_cache)
    monkeypatch.setattr(worker, '_run_browser_plan', capture)
    import asyncio
    with pytest.raises(asyncio.CancelledError):
        await worker._harvest_with_repair(task, root)
    runtime = json.loads((root/'runtime-report.json').read_text('utf8'))
    assert len(runtime['browser_runs']) == 1
    assert runtime['unresolved'] == {'profile_navigation_failed': 1}
