"""Disposition-only repair preserves fresh branch proofs, not old permissions."""
import copy
import json
from types import SimpleNamespace

import pytest

from server import agent_recon, agent_worker
from server.agent_worker import AgentWorker
from test_repair_scope import PLAN, A, B

D = 'https://scope.example.edu/organization'


def plan():
    active = copy.deepcopy(PLAN)
    for branch in active['branches']:
        branch.update(source_role='faculty_roster', source_container_selector='main',
                      role_evidence_selector='h1', source_context_selector='header')
    return {**active, 'source_dispositions': [{'url': D,
        'reason': 'source_relationship_unverified', 'source_role': 'unverified_people_source',
        'source_container_selector': 'article', 'role_evidence_selector': 'h1',
        'source_context_selector': 'footer.expired'}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['known', 'unknown_url', 'profile', 'mixed', 'empty', 'fresh_failure'])
async def test_disposition_only_feedback_reuses_only_freshly_verified_branches(tmp_path, monkeypatch, case):
    worker = AgentWorker(db_path=tmp_path/'tasks.db', output_dir=tmp_path/'outputs')
    issues = [{'url': D, 'status': 'source_disposition_unverified'}]
    if case == 'unknown_url':
        issues[0]['url'] = D + '/new'
    elif case == 'profile':
        issues[0]['status'] = 'profile_content_selector_missing'
    elif case == 'mixed':
        issues.append({'url': 'https://scope.example.edu/profile/unknown', 'status': 'profile_content_selector_missing'})
    elif case == 'empty':
        issues = []
    calls = []
    async def validate(entry, directory, *, verify_discovery):
        assert verify_discovery is False
        [branch] = entry['plan']['branches']
        calls.append(branch)
        return {'valid': not (case == 'fresh_failure' and branch['url'] == A),
                'events': [{'fresh_branch': branch['url']}], 'evidence_root': str(directory)}
    monkeypatch.setattr(worker, '_validate_structure', validate)
    result = await worker._unaffected_branches(plan(), {'issues': issues}, tmp_path/'attempt-1')
    if case in {'known', 'fresh_failure'}:
        assert {branch['url'] for branch in calls} == {A, B}
        assert set(result) == ({B} if case == 'fresh_failure' else {A, B})
        assert all(result[url]['events'] == [{'fresh_branch': url}] for url in result)
    else:
        assert result is None and not calls
    # Source-only issues still require fresh discovery, not branch-only resume.
    assert worker._damaged_branches(plan(), {'issues': [{'url': D, 'status': 'source_disposition_unverified'}]}) is None


@pytest.mark.asyncio
async def test_worker_keeps_active_scopes_but_runs_new_discovery(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path/'tasks.db', output_dir=tmp_path/'outputs')
    first = plan()
    second = copy.deepcopy(first)
    second['source_dispositions'][0]['source_context_selector'] = 'footer'
    calls, validated = [], []
    async def scout(*args, **kwargs):
        calls.append(kwargs)
        return agent_recon.ReconResult(first if len(calls) == 1 else second, 0, 'completed', f'fixture-{len(calls)}')
    async def capture(current, directory):
        assert [b['profile_content_selector'] for b in current['branches']] == ['div.body-a', 'div.body-b']
        return {'delivery_ready': False, 'unresolved': [{'url': D, 'status':
            'source_disposition_unverified' if len(calls) == 1 else 'source_relationship_unverified'}]}
    async def validate(entry, directory, *, verify_discovery):
        assert verify_discovery is False
        [branch] = entry['plan']['branches']
        validated.append(branch['url'])
        return {'valid': True, 'events': [{'fresh_branch': branch['url']}]}
    async def no_cache(*args, **kwargs):
        return None
    monkeypatch.setattr(agent_worker, 'reconnoiter', scout)
    monkeypatch.setattr(worker, '_run_browser_plan', capture)
    monkeypatch.setattr(worker, '_validate_structure', validate)
    monkeypatch.setattr(worker, '_cached_recon', no_cache)
    task = SimpleNamespace(school=first['school'], college=first['college'], url='https://scope.example.edu/', task_id='fixture-disposition')
    report = await worker._harvest_with_repair(task, tmp_path/'tasks'/task.task_id)
    assert len(calls) == 2 and set(validated) == {A, B}
    assert set(calls[1]['verified_branches']) == {A, B}
    assert 'resume_discovery' not in calls[1]
    assert not report['delivery_ready']


@pytest.mark.parametrize('key,value', [('source_role', 'unverified_people_source'),
    ('source_container_selector', 'main'), ('role_evidence_selector', 'h1:first-of-type'),
    ('source_context_selector', 'header:first-of-type')])
def test_changed_discovery_role_does_not_borrow_reused_branch_permission(monkeypatch, key, value):
    # Stipulated model/source producer boundary, with real schema and staged gates.
    from test_source_role_staged import TARGET, URL, CLAIM, pair, source_events
    previous = {**CLAIM, 'card_selector': 'main ul > li', 'single_page': True}
    current = {**previous, key: value}
    cards = pair('browser_verify', {'card_selector': previous['card_selector']},
        {'url': URL, 'cardCount': 1, 'pagination': {'total': 0}}, 'cards')
    old_proof = cards + source_events(previous, 'old-source')
    phases = []
    def model(prompt, session, cfg, patches):
        phases.append(cfg['phase'])
        if cfg['phase'] == 'discovery':
            claim = {key: value for key, value in current.items() if key not in {'card_selector', 'single_page'}}
            value = {'school': TARGET['school'], 'college': TARGET['college'], 'branches': [claim]}
            events = pair('browser_navigation', {}, {'items': [{'url': URL}]}, 'navigation') + source_events(claim, 'new-discovery-source')
        else:
            value, events = current, cards + source_events(current, 'new-branch-source')
        return SimpleNamespace(final_response=json.dumps(value), finish_reason='completed', events=events)
    monkeypatch.setattr(agent_recon, '_execute_harness', model)
    result = agent_recon._run_staged_harness('changed-role', {'target': TARGET, 'timeout_seconds': 30,
        'verified_branches': {URL: {'branch': previous, 'events': old_proof}}}, ())
    assert phases == ['discovery', 'branch']
    assert json.loads(result.final_response)['branches'][0][key] == value
