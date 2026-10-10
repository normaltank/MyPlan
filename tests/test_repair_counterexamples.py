"""The worker cannot forget captured body failures after an unrelated rejected plan."""
import json
from types import SimpleNamespace

import pytest

from server import agent_recon, agent_worker
from server.agent_recon import ReconPlanError, ReconResult
from server.agent_worker import AgentWorker
from test_recon_optimizations import pair, result

ROSTER = 'https://repair.example.edu/roster'
NORMAL = ['https://repair.example.edu/profile/normal-1', 'https://repair.example.edu/profile/normal-2']
FAILURES = [f'https://repair.example.edu/profile/layout-{i}' for i in range(15)]
PLAN = {'school': 'Repair University', 'college': 'Repair College', 'source_kind': 'university_faculty_roster',
        'branches': [{'url': ROSTER, 'card_selector': '.cards>li', 'single_page': True,
                      'profile_content_selector': '#body'}]}


def profile_proof(url, selector, cid):
    args = {'profile_content_selector': selector}
    return pair('browser_verify_profile', args, {'url': url, **args, 'profile_content_start_selector': None,
        'confirmed': True, 'container_visible': True, 'container_count': 1, 'dom_stable': True,
        'network_idle': True, 'html_sha256': 'a' * 64, 'selected_html_sha256': 'b' * 64}, cid)


def branch_proof(selector='#body', extra_profiles=(), card_selector='.cards>li'):
    events = pair('browser_verify', {'card_selector': card_selector},
                  {'url': ROSTER, 'cardCount': 17, 'pagination': {'total': 0},
                   'linkedSamples': NORMAL}, 'roster')
    for index, url in enumerate([*NORMAL, *extra_profiles]):
        events.extend(profile_proof(url, selector, f'profile-{index}'))
    return events


@pytest.mark.asyncio
@pytest.mark.parametrize('failure_count, prove_repairs', [(15, False), (25, False), (15, True)])
async def test_worker_keeps_body_counterexamples_through_rejected_plan(tmp_path, monkeypatch,
                                                                     failure_count, prove_repairs):
    worker = AgentWorker(db_path=tmp_path/'queue.db', output_dir=tmp_path/'outputs', max_recon_repairs=2)
    task = SimpleNamespace(task_id='body-counterexamples', school=PLAN['school'], college=PLAN['college'],
                           url='https://repair.example.edu/')
    discovery = {'plan': {**PLAN, 'branches': [{'url': ROSTER}]},
                 'events': pair('browser_navigation', {}, {'items': [{'url': ROSTER}]}, 'discovery'),
                 'session_id': 'discovery'}
    captures = []
    calls = []
    failures = [f'https://repair.example.edu/profile/layout-{i}' for i in range(failure_count)]

    async def no_cache(*_args):
        return None

    async def browser(plan, directory):
        captures.append(plan)
        if len(captures) > 1:
            return {'delivery_ready': True, 'unresolved': []}
        return {'delivery_ready': False, 'unresolved': [
            {'status': 'profile_content_selector_missing', 'url': url,
             'branch_url': ROSTER, 'selector': '#body'} for url in failures]}

    def execute(prompt, sid, cfg, patches):
        # The later scout again proves only the two ordinary pages. The real
        # staged submission gate must reject that plan for known body failures.
        selector = '.body' if prove_repairs else '#body'
        branch = {**PLAN['branches'][0], 'card_selector': '.cards > li', 'profile_content_selector': selector}
        return result(branch, branch_proof(selector, failures if prove_repairs else (),
                                          card_selector=branch['card_selector']))

    async def scout(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return ReconResult(PLAN, 0, 'completed', 'first', discovery_checkpoint=discovery)
        if len(calls) == 2:
            # A rejected final response is not permission to clear the prior
            # capture's facts. Its text is deliberately unsafe to replay.
            error = ReconPlanError('rejected final response with teacher@example.edu')
            # The failed scout did observe the correct bodies, as in the real
            # case. Those events remain evidence, not an accepted replacement
            # plan or authority to erase the URLs the next gate must cover.
            error.phase_events = branch_proof('.body', failures)
            error.safe_feedback = {'previous_plan': {**PLAN, 'branches': []},
                                   'issues': [{'status': 'plan_contract_rejected'}]}
            raise error
        staged = agent_recon._run_staged_harness('third', {'target': {'school': task.school,
            'college': task.college, 'url': task.url}, 'timeout_seconds': 10,
            'resume_discovery': discovery, 'feedback': kwargs['feedback']}, ())
        return ReconResult(json.loads(staged.final_response), 0, 'completed', 'third')

    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(agent_recon, '_execute_harness', execute)
    monkeypatch.setattr(worker, '_run_browser_plan', browser)
    monkeypatch.setattr(worker, '_cached_recon', no_cache)
    monkeypatch.setattr(worker, '_unaffected_branches', no_cache)
    report = await worker._harvest_with_repair(task, tmp_path/'task')
    required = {item['url'] for item in calls[2]['feedback']['issues']
                if item['status'] == 'profile_content_selector_missing'}
    assert required == set(failures)
    assert calls[2]['feedback']['previous_plan']['branches'] == PLAN['branches']
    assert report['delivery_ready'] is prove_repairs and len(captures) == (2 if prove_repairs else 1)
    assert 'teacher@example.edu' not in json.dumps(calls[2]['feedback'])


@pytest.mark.asyncio
async def test_worker_repairs_live_bodies_without_requiring_a_dead_reference_body(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path/'queue.db', output_dir=tmp_path/'outputs', max_recon_repairs=2)
    task = SimpleNamespace(task_id='dead-reference', school=PLAN['school'], college=PLAN['college'],
                           url='https://repair.example.edu/')
    dead = 'https://repair.example.edu/profile/broken%20reference'
    checkpoint = {'plan': {**PLAN, 'branches': [{'url': ROSTER}]}, 'session_id': 'discovery',
                  'events': pair('browser_navigation', {}, {'items': [{'url': ROSTER}]}, 'discovery')}
    access = {'url': dead, 'status': 'profile_navigation_failed'}
    no_body = {'url': dead, 'branch_url': ROSTER, 'status': 'profile_content_browser_unverified',
               'selector': '#body'}
    captured_failures = [{'url': url, 'branch_url': ROSTER, 'status': 'profile_content_selector_missing',
                          'selector': '#body'} for url in FAILURES]
    calls, captures = [], []

    async def nothing(*_args):
        return None

    def execute(prompt, sid, cfg, patches):
        return result({**PLAN['branches'][0], 'profile_content_selector': '.body'},
                      branch_proof('.body', FAILURES))

    async def scout(*_args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return ReconResult(PLAN, 0, 'completed', 'first', discovery_checkpoint=checkpoint)
        if len(calls) > 2:
            pytest.fail('a still-dead reference must not start another structural scout')
        staged = agent_recon._run_staged_harness('repair', {'target': {'school': task.school,
            'college': task.college, 'url': task.url}, 'timeout_seconds': 10,
            'resume_discovery': checkpoint, 'feedback': kwargs['feedback']}, ())
        return ReconResult(json.loads(staged.final_response), 0, 'completed', 'repaired')

    async def browser(plan, directory):
        captures.append(plan)
        # The dead candidate is retained even after its accessible siblings have
        # valid bodies. No fake successful capture or record exclusion is used.
        return {'delivery_ready': False, 'candidate_count': 18, 'unresolved': [access, no_body,
            *(captured_failures if plan['branches'][0]['profile_content_selector'] == '#body' else [])]}

    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(agent_recon, '_execute_harness', execute)
    monkeypatch.setattr(worker, '_cached_recon', nothing)
    monkeypatch.setattr(worker, '_unaffected_branches', nothing)
    monkeypatch.setattr(worker, '_run_browser_plan', browser)
    report = await worker._harvest_with_repair(task, tmp_path/'task')
    assert not report['delivery_ready'] and report['candidate_count'] == 18
    assert access in report['unresolved'] and no_body in report['unresolved']
    assert len(calls) == 2 and any(p['branches'][0]['profile_content_selector'] == '.body' for p in captures)


def test_body_only_failure_cannot_be_deferred_without_a_failed_fetch():
    issue = {'url': 'https://repair.example.edu/p/1', 'status': 'profile_content_selector_empty'}
    failed = agent_recon.profile_access_failures([issue])
    assert not agent_recon.profile_issue_is_access_only(issue, failed)
    # A plain space in a source URL and the browser's percent encoding identify
    # the same failed reference, without matching a different page or fragment.
    failed = agent_recon.profile_access_failures([
        {'url': 'https://repair.example.edu/p/broken%20reference', 'status': 'profile_navigation_failed'}])
    assert agent_recon.profile_issue_is_access_only(
        {**issue, 'url': 'https://repair.example.edu/p/broken reference'}, failed)
    assert not agent_recon.profile_issue_is_access_only(issue, failed)


def test_rejected_plan_with_same_urls_cannot_replace_the_valid_capture_plan():
    feedback = {'previous_plan': PLAN, 'issues': [{'url': FAILURES[0], 'branch_url': ROSTER,
                                                  'status': 'profile_content_selector_missing'}]}
    unsafe = {**PLAN, 'branches': [{**PLAN['branches'][0], 'profile_content_selector': '.教师姓名'}]}
    merged = agent_recon.merge_repair_feedback(feedback,
        {'previous_plan': unsafe, 'issues': [{'status': 'plan_contract_rejected'}]})
    assert merged['previous_plan'] == PLAN
    assert '教师姓名' not in json.dumps(merged, ensure_ascii=False)
