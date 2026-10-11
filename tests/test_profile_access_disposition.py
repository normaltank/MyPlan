"""A failed fetch and its body consequence share one recovery decision."""
import json
from pathlib import Path

import pytest

from server.agent_queue import failure_disposition
from server.agent_worker import AgentWorker
from server.recovery_policy import classify_issues

DEAD = 'https://example.edu/p/broken%20reference'
OTHER = 'https://example.edu/p/live'


def failures():
    return [{'url': DEAD, 'status': 'profile_navigation_failed',
             'error': 'PRIVATE_TEACHER@example.edu forbidden raw error'},
            {'url': DEAD.replace('%20', ' '), 'status': 'profile_content_browser_unverified',
             'browser_evidence': {'reason': 'container_not_unique', 'text': 'PRIVATE_TEACHER'}}]


def test_shared_policy_keeps_original_access_body_issues_without_downgrading_other_scope():
    issues = failures()
    recovery = classify_issues(issues)
    assert recovery.access == issues
    assert not recovery.structure
    other = {'url': OTHER, 'status': 'profile_content_selector_missing'}
    unknown = {'url': DEAD, 'status': 'profile_content_new_unclassified_error'}
    recovery = classify_issues([*issues, other, unknown])
    assert recovery.structure == [other]
    assert recovery.stop == [unknown]


@pytest.mark.asyncio
@pytest.mark.parametrize('extra,expected', [
    ([], 'access'),
    ([{'url': OTHER, 'status': 'profile_content_selector_missing'}], 'structure'),
    ([{'url': DEAD, 'status': 'unknown_harvest_error'}], 'failed'),
])
async def test_worker_persists_context_then_queue_api_and_report_agree(
    tmp_path, monkeypatch, extra, expected
):
    from server import agent_worker, app as web
    from server.agent_recon import ReconResult

    worker = AgentWorker(db_path=tmp_path/'tasks.db', output_dir=tmp_path/'outputs', max_recon_repairs=0)
    queued = worker.queue.submit_task('University', 'College', 'https://example.edu/')
    task = worker.queue.claim_task('test-worker', task_id=queued.task_id)
    issues = [*failures(), *extra]
    reports = []

    async def scout(*args, **kwargs):
        from source_contract_fixture import upgrade
        plan = {'school': task.school, 'college': task.college,
                'branches': [{'url': task.url, 'card_selector': 'main li', 'single_page': True}]}
        return ReconResult(upgrade(plan), 0, 'completed', 'fixture')

    async def capture(_plan, directory):
        audit = directory/'audit.json'
        result = {'delivery_ready': False, 'unresolved': issues, 'audit_path': str(audit),
                  'total': 1, 'with_email': 0, 'records': [{'name': 'PRIVATE_TEACHER'}]}
        audit.write_text(json.dumps(result), encoding='utf-8')
        reports.append(result)
        return result

    async def no_cache(*args):
        return None

    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(worker, '_cached_recon', no_cache)
    monkeypatch.setattr(worker, '_run_browser_plan', capture)
    monkeypatch.setattr(web, 'agent_queue', worker.queue)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(worker.output_dir))
    await worker._process(task)
    saved = worker.queue.get_task(task.task_id)
    assert saved.status == 'failed'
    assert failure_disposition(saved.error, saved.stats) == expected
    presented = web.present_task(saved.to_dict(), worker.queue.get_delivery_scope(saved.batch_id, saved.school))
    assert presented['failure_disposition'] == expected
    audit = json.loads(Path(reports[-1]['audit_path']).read_text(encoding='utf-8'))
    assert audit['unresolved'] == issues and audit['records'] == [{'name': 'PRIVATE_TEACHER'}]
    assert not audit['delivery_ready']
    assert set(audit['recovery']) == ({'access'} if expected == 'access'
                                     else {'access', 'structure'} if expected == 'structure'
                                     else {'access', 'stop'})
    assert 'PRIVATE_TEACHER' not in json.dumps(saved.stats['failure_issues'])
    assert saved.stats['failure_issues'][0] == {'status': 'profile_navigation_failed', 'url': DEAD}
    assert failure_disposition('', {'failure_codes': saved.stats['failure_codes'],
                                    'failure_issues': saved.stats['failure_issues']}) == expected


def test_failure_context_is_not_permission_to_ignore_uncovered_or_unknown_codes():
    context = [{'status': item['status'], 'url': item['url']} for item in failures()]
    codes = ['profile_navigation_failed', 'profile_content_browser_unverified']
    assert failure_disposition('', {'failure_codes': codes, 'failure_issues': context}) == 'access'
    assert failure_disposition('', {'failure_codes': [*codes, 'unknown_harvest_error'],
                                    'failure_issues': context}) == 'failed'
    assert failure_disposition('', {'failure_codes': [*codes, 'pagination_not_verified'],
                                    'failure_issues': context}) == 'structure'
    assert failure_disposition('', {'failure_codes': codes}) == 'structure'
    assert failure_disposition('', {'failure_codes': ['recon_failed', 'unknown_harvest_error'],
                                    'failure_issues': [{'status': 'recon_failed'}]}) == 'failed'
    assert failure_disposition('', {'failure_codes': ['recon_failed'],
                                    'failure_issues': [{'status': 'recon_failed'}]}) == 'recon'


def test_transient_body_proof_reason_and_branch_dependencies_survive_safe_metadata():
    contexts = [
        [{'status': 'profile_content_browser_unverified', 'url': OTHER,
          'browser_evidence': {'reason': 'dom_unstable'}}],
        [{'status': 'browser_navigation_failed', 'url': OTHER},
         {'status': 'planned_branch_not_captured', 'branch_url': OTHER}],
        [{'status': 'fetch_failed:TimeoutError', 'url': DEAD},
         {'status': 'profile_content_selector_missing', 'url': DEAD}],
    ]
    for context in contexts:
        assert failure_disposition('', {'failure_codes': [i['status'] for i in context],
                                        'failure_issues': context}) == 'access'


def test_unsafe_failure_metadata_keeps_audit_private_and_cannot_downgrade_scope():
    from server.recovery_policy import safe_failure_issues
    raw = [{'status': 'profile_navigation_failed', 'url': 'https://user:password@example.edu/p/1'},
           {'status': 'profile_content_selector_missing', 'url': 'https://user:password@example.edu/p/1'},
           {'status': 'fetch_failed:PRIVATE_TEACHER@example.edu',
            'url': 'https://example.edu/?email=PRIVATE_TEACHER@example.edu', 'name': 'PRIVATE_TEACHER'},
           {'status': 'profile_content_selector_missing',
            'url': 'https://example.edu/?email=PRIVATE_TEACHER@example.edu'},
           {'status': 'PRIVATE_TEACHER raw exception', 'error': 'PRIVATE_TEACHER'}]
    context = safe_failure_issues(raw)
    assert context == [{'status': 'profile_navigation_failed'},
                       {'status': 'profile_content_selector_missing'},
                       {'status': 'fetch_failed'}, {'status': 'profile_content_selector_missing'},
                       {'status': 'unknown_failure'}]
    assert 'PRIVATE_TEACHER' not in json.dumps(context) and 'password' not in json.dumps(context)
    assert failure_disposition('', {'failure_codes': [item['status'] for item in context],
                                    'failure_issues': context}) == 'failed'
    assert failure_disposition('', {'failure_codes': [item['status'] for item in context[:-1]],
                                    'failure_issues': context[:-1]}) == 'structure'


@pytest.mark.parametrize('different', [DEAD + '#different', DEAD + '?version=2', OTHER])
def test_body_on_another_exact_url_or_fragment_is_not_a_failed_fetch_consequence(different):
    recovery = classify_issues([{'status': 'profile_navigation_failed', 'url': DEAD},
                               {'status': 'profile_content_selector_empty', 'url': different}])
    assert len(recovery.access) == 1 and len(recovery.structure) == 1
