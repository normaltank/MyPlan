"""The actual queue/HTTP payload consumers retain native source disposition evidence."""
import json

import pytest

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
