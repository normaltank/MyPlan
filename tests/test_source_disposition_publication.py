"""The actual queue/HTTP payload consumers retain native source disposition evidence."""
import json
import hashlib
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from server import agent_worker
from server.agent_recon import ReconResult
from server.browser_batch_tool import run_plan
from server.browser_batch_sync import build_payload
from test_browser_batch_sync import _report
from test_worker_resume_cache import worker_and_task


def fields(url):
    return {'source_dispositions': [{'url': url, 'reason': 'source_unreadable'}],
            'source_disposition_evidence': [{'url': url, 'reason': 'source_unreadable',
                'status': 'unavailable', 'method': 'browser_navigate',
                'observed_at': '2026-10-10T20:00:00+00:00', 'http_status': 503,
                'error_kind': None, 'original_path': 'actual-original.html',
                'source_html_sha256': 'a' * 64, 'browser_proof': None,
                'navigation_evidence_path': 'events.json', 'navigation_evidence_sha256': 'b' * 64}]}


@pytest.mark.asyncio
async def test_failed_worker_publishes_native_source_evidence_without_reclassifying_or_requeue(tmp_path):
    worker, task, root = worker_and_task(tmp_path)
    original = fields(task.url)
    (root / 'runtime-report.json').write_bytes(json.dumps(original).encode('utf8'))
    await worker._mark_failed(task, 'source is unavailable', failure_codes=['source_unreadable'],
                              failure_issues=[{'url': task.url, 'status': 'source_unreadable'}])
    saved = worker.queue.get_task(task.task_id)
    assert saved.status == 'failed'
    assert saved.stats['failure_codes'] == ['source_unreadable']
    for key, value in original.items():
        assert saved.stats[key] == value == saved.stats['runtime_report'][key]
    assert task.status == 'running'  # No second sync through task_done.
    assert (root / 'runtime-report.json').read_bytes() == json.dumps(original).encode('utf8')


def test_success_sync_payload_preserves_source_scope_and_native_evidence(tmp_path):
    path, root = _report(tmp_path)
    report = json.loads(path.read_text(encoding='utf8'))
    original = fields('https://example.edu/committee')
    original['source_dispositions'][0]['reason'] = 'not_faculty_roster'
    original['source_disposition_evidence'][0].update(reason='not_faculty_roster', status='inactive', http_status=200)
    report.update(original, previous_source_audit_path='prior-native-audit.json')
    path.write_bytes(json.dumps(report, ensure_ascii=False).encode('utf8'))
    audit_bytes = path.read_bytes()
    stats = build_payload(path, 'worker-1', 'lease-1', root)['stats']
    for key, value in original.items():
        assert stats[key] == value
    assert stats['previous_source_audit_path'] == 'prior-native-audit.json'
    assert path.read_bytes() == audit_bytes


@pytest.mark.browser
@pytest.mark.asyncio
async def test_failed_process_preserves_actual_capture_evidence_through_runtime_and_queue(tmp_path, monkeypatch):
    worker, task, root = worker_and_task(tmp_path, repairs=0)
    url = 'https://example.edu/people'
    plan = {'school': task.school, 'college': task.college, 'branches': [],
            'source_dispositions': [{'url': url, 'reason': 'not_faculty_roster',
                'source_role': 'organizational_membership', 'source_container_selector': 'main',
                'role_evidence_selector': 'h1', 'source_context_selector': 'header'}]}
    html = ('<meta charset="utf-8"><title>学院领导</title><header>University College</header>'
            '<h1>学院领导</h1><main><p>' + '本学院当前组织成员与实际任职原文。' * 30 + '</p></main>')
    async def recon(*_args, **_kwargs):
        return ReconResult(plan, 0, 'completed', 'isolated-source-publication')
    async def no_cache(*_args, **_kwargs):
        return None
    monkeypatch.setattr(agent_worker, 'reconnoiter', recon)
    monkeypatch.setattr(worker, '_cached_recon', no_cache)
    captured = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route('**/*', lambda route: route.fulfill(
            body=html, content_type='text/html; charset=utf-8'))
        async def capture(value, directory):
            report = await run_plan(value, context, directory, task_id=task.task_id)
            captured.append(report)
            return report
        monkeypatch.setattr(worker, '_run_browser_plan', capture)
        await worker._process(task)
        await browser.close()
    assert len(captured) == 1
    saved = worker.queue.get_task(task.task_id)
    runtime = json.loads((root / 'runtime-report.json').read_text(encoding='utf8'))
    audit = json.loads(Path(captured[0]['audit_path']).read_text(encoding='utf8'))
    assert saved.status == 'failed'
    assert 'no_active_roster_sources' in saved.stats['failure_codes']
    for key in ('source_dispositions', 'source_disposition_evidence'):
        assert audit[key] == runtime[key] == saved.stats[key] == saved.stats['runtime_report'][key]
    evidence = audit['source_disposition_evidence'][0]
    assert evidence['status'] == 'inactive'
    assert hashlib.sha256(Path(evidence['original_path']).read_bytes()).hexdigest() == evidence['source_html_sha256']
    assert hashlib.sha256(Path(evidence['navigation_evidence_path']).read_bytes()).hexdigest() == evidence['navigation_evidence_sha256']
