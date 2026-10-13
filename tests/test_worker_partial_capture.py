"""Incomplete college runs retain local observations without publishing a success."""
import json

import pytest

from server.agent_queue import AgentTaskQueue, LeaseConflictError
from server.agent_worker import AgentWorker


@pytest.mark.asyncio
async def test_failed_capture_keeps_rows_and_remains_outside_university_delivery(tmp_path):
    out = tmp_path / 'outputs'
    db = tmp_path / 'tasks.db'
    queue = AgentTaskQueue(db)
    task = queue.submit_task('样本大学', '样本学院', url='https://example.edu/', mode='batch', batch_id='partial')
    claimed = queue.claim_task('fixture', task_id=task.task_id)
    row = {'序号': 1, '姓名': '王芳', '职称': '', '邮箱': '', '学校': task.school, '学院': task.college,
           '采集人': 'fixture', '采集日期': '2026-10-12', '简介': '', '主页': ''}
    folder = out / 'tasks' / task.task_id / 'attempt-1'
    folder.mkdir(parents=True)
    path = folder / 'records.json'
    path.write_text(json.dumps([row], ensure_ascii=False), encoding='utf8')
    worker = AgentWorker(output_dir=out, db_path=db)
    await worker._mark_failed(claimed, 'one source remains unresolved',
        failure_codes=['source_structure_unresolved'],
        failure_issues=[{'url': 'https://example.edu/pending', 'status': 'source_structure_unresolved'}],
        partial_report={'total': 1, 'records_path': str(path), 'coverage_scope': 'supplied_roster_branches_only'})
    saved = queue.get_task(task.task_id)
    assert saved.status == 'failed' and saved.records == [row]
    assert saved.stats['partial_capture']['record_count'] == 1
    assert saved.stats['partial_capture']['delivery_verified'] is False
    assert queue.get_delivery_scope('partial', task.school)['state'] == 'blocked'
    assert queue.get_university_snapshot(task.school)['records'] == []


def test_lost_lease_cannot_replace_rows_with_a_partial_capture(tmp_path):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    task = queue.submit_task('样本大学', '样本学院')
    queue.claim_task('fixture', task_id=task.task_id)
    with pytest.raises(LeaseConflictError):
        queue.sync_task_result(task.task_id, [{'姓名': 'untrusted'}], {}, '', 'failure', 'other', 'wrong-token')
    assert queue.get_task(task.task_id).records == []


@pytest.mark.asyncio
async def test_malformed_partial_file_does_not_leave_task_running(tmp_path):
    worker = AgentWorker(output_dir=tmp_path / 'out', db_path=tmp_path / 'tasks.db')
    queued = worker.queue.submit_task('U', 'C', 'https://example.edu/')
    task = worker.queue.claim_task('fixture', task_id=queued.task_id)
    folder = worker.output_dir / 'tasks' / task.task_id / 'attempt-1'
    folder.mkdir(parents=True)
    path = folder / 'records.json'
    path.write_text('broken JSON', encoding='utf8')
    await worker._mark_failed(task, 'capture failed', partial_report={'records_path': str(path), 'total': 1})
    saved = worker.queue.get_task(task.task_id)
    assert saved.status == 'failed' and saved.records == []
    assert 'partial_capture' not in saved.stats


@pytest.mark.asyncio
async def test_repair_budget_failure_keeps_preceding_browser_capture(tmp_path, monkeypatch):
    from server import agent_worker
    from server.agent_recon import ReconBudgetExceeded, ReconResult
    from source_contract_fixture import upgrade
    worker = AgentWorker(output_dir=tmp_path / 'out', db_path=tmp_path / 'tasks.db', max_recon_repairs=1)
    queued = worker.queue.submit_task('U', 'C', 'https://example.edu/')
    task = worker.queue.claim_task('fixture', task_id=queued.task_id)
    root = worker.output_dir / 'tasks' / task.task_id
    plan = upgrade({'school': 'U', 'college': 'C', 'branches': [
        {'url': 'https://example.edu/list', 'card_selector': 'li', 'single_page': True}]})
    calls = []

    async def scout(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            return ReconResult(plan, 0, 'completed', 'fixture')
        raise ReconBudgetExceeded('controlled repair failure')

    async def nothing(*args, **kwargs):
        return None

    async def capture(_plan, directory):
        record_path = directory / 'records.json'
        record_path.write_text('[]', encoding='utf8')
        return {'delivery_ready': False, 'total': 1, 'records_path': str(record_path), 'unresolved': [
            {'url': 'https://example.edu/list', 'status': 'profile_content_selector_missing'}]}

    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(worker, '_cached_recon', nothing)
    monkeypatch.setattr(worker, '_unaffected_branches', nothing)
    monkeypatch.setattr(worker, '_run_browser_plan', capture)
    report = await worker._harvest_with_repair(task, root)
    assert len(calls) == 2
    assert report['total'] == 1 and report['records_path'].endswith('records.json')
    assert not report['delivery_ready']
    assert {item['status'] for item in report['unresolved']} == {
        'profile_content_selector_missing', 'recon_budget_exceeded'}
    runtime = json.loads((root / 'runtime-report.json').read_text('utf8'))
    assert len(runtime['browser_runs']) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['exception', 'timeout'])
async def test_finished_capture_survives_later_browser_failure(tmp_path, monkeypatch, failure):
    import asyncio
    from server import agent_worker
    from server.agent_recon import ReconResult
    from source_contract_fixture import upgrade
    worker = AgentWorker(output_dir=tmp_path / 'out', db_path=tmp_path / 'tasks.db', max_recon_repairs=1)
    queued = worker.queue.submit_task('U', 'C', 'https://example.edu/')
    task = worker.queue.claim_task('fixture', task_id=queued.task_id)
    row = {'序号': 1, '姓名': '王芳', '职称': '', '邮箱': '', '学校': 'U', '学院': 'C',
           '采集人': 'fixture', '采集日期': '2026-10-12', '简介': '', '主页': ''}
    captures = []

    async def scout(*args, **kwargs):
        budget = kwargs['budget']
        before = budget.snapshot()
        budget.observe({'type': 'assistant/message', 'data': {'usage': {
            'inputTokens': 10, 'cacheReadTokens': 20, 'outputTokens': 5}}})
        plan = upgrade({'school': 'U', 'college': 'C', 'branches': [
            {'url': 'https://example.edu/list', 'card_selector': 'li' if not captures else 'main li',
             'single_page': True}]})
        return ReconResult(plan, 0, 'completed', 'fixture', diagnostics=budget.delta(before))

    async def nothing(*args, **kwargs):
        return None

    async def capture(_plan, directory):
        if captures:
            # A retry that damaged its own mutable output cannot damage the receipt.
            captures[0].write_text('broken JSON', encoding='utf8')
            raise RuntimeError('second capture failed') if failure == 'exception' else asyncio.TimeoutError()
        path = directory / 'records.json'
        path.write_text(json.dumps([row], ensure_ascii=False), encoding='utf8')
        captures.append(path)
        return {'delivery_ready': False, 'total': 1, 'records_path': str(path), 'unresolved': [
            {'url': 'https://example.edu/list', 'status': 'profile_content_selector_missing'}]}

    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(worker, '_cached_recon', nothing)
    monkeypatch.setattr(worker, '_unaffected_branches', nothing)
    monkeypatch.setattr(worker, '_run_browser_plan', capture)
    await worker._execute_task(task)
    saved = worker.queue.get_task(task.task_id)
    assert saved.status == 'failed' and saved.records == [row]
    assert saved.stats['partial_capture']['delivery_verified'] is False
    terminal = 'worker_runtime_failed' if failure == 'exception' else 'task_timeout'
    assert set(saved.stats['failure_codes']) == {terminal, 'profile_content_selector_missing'}
    assert saved.stats['runtime_report']['recon']['model_calls'] == 2
    audit = json.loads((worker.output_dir / 'tasks' / task.task_id / 'recon-attempts.json').read_text('utf8'))
    assert saved.stats['runtime_report']['cost']['known_usd'] == sum(
        attempt['cost']['known_usd'] for attempt in audit['attempts'])


@pytest.mark.asyncio
async def test_partial_budget_stop_captures_without_another_scout(tmp_path, monkeypatch):
    from server import agent_worker
    from server.agent_recon import ReconResult
    from source_contract_fixture import upgrade
    worker = AgentWorker(output_dir=tmp_path / 'out', db_path=tmp_path / 'tasks.db', max_recon_repairs=2)
    queued = worker.queue.submit_task('U', 'C', 'https://example.edu/')
    task = worker.queue.claim_task('fixture', task_id=queued.task_id)
    calls = []
    plan = upgrade({'school': 'U', 'college': 'C', 'branches': [
        {'url': 'https://example.edu/list', 'card_selector': 'li', 'single_page': True}]})

    async def scout(*args, **kwargs):
        calls.append(True)
        return ReconResult(plan, 0, 'completed', 'fixture', diagnostics={'stop_reason': 'recon_budget_exceeded'})

    async def nothing(*args, **kwargs):
        return None

    async def capture(_plan, directory):
        return {'delivery_ready': False, 'unresolved': [
            {'url': 'https://example.edu/list', 'status': 'profile_content_selector_missing'}]}

    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(worker, '_cached_recon', nothing)
    monkeypatch.setattr(worker, '_run_browser_plan', capture)
    monkeypatch.setattr(worker, '_attach_cross_check', nothing)
    report = await worker._harvest_with_repair(task, worker.output_dir / 'tasks' / task.task_id)
    assert len(calls) == 1 and not report['delivery_ready']
    assert 'recon_budget_exceeded' in {item['status'] for item in report['unresolved']}
