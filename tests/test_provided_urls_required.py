"""Supplied sources stay obligations through staged and public recon validation."""
import json
from types import SimpleNamespace

import pytest

from server import agent_recon
from server.source_qualification import SOURCE_FIELDS
from test_page_type_recon import (CHILDREN, TARGET, all_events, branch, navigation, pair, plan,
                                 selector_events, source_events)

FIRST = 'https://example.edu/supplied'
SECOND = 'https://example.edu/additional'


def discovery(values, dispositions=()):
    result = {'school': TARGET['school'], 'college': TARGET['college'],
        'branches': [{key: value[key] for key in ('url', *SOURCE_FIELDS)} for value in values]}
    if dispositions:
        result['source_dispositions'] = list(dispositions)
    events = []
    for index, value in enumerate(values):
        events += pair('browser_navigate', {'url': value['url']}, {'url': value['url']}, f'nav-{index}')
        source, _ = source_events(value, f'discovery-source-{index}')
        events += source
    for index, value in enumerate(dispositions):
        events += pair('browser_navigate', {'url': value['url']},
                       {'requested_url': value['url'], 'url': value['url'], 'status': 503}, f'unreadable-{index}')
    return result, events


def response(value, events):
    return SimpleNamespace(final_response=json.dumps(value), finish_reason='completed', events=events)


def test_supplied_url_is_required_even_on_first_discovery_and_runtime_target(monkeypatch, tmp_path):
    seen = []
    targets = []
    value, events = discovery([branch(SECOND)])
    def execute(prompt, sid, cfg, patches):
        seen.append(cfg['phase'])
        assert FIRST in prompt
        return response(value, events)
    monkeypatch.setattr(agent_recon, '_execute_harness', execute)
    monkeypatch.setattr(agent_recon, 'write_runtime_patch', lambda path, **kwargs: targets.append(kwargs['target']))
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness('provided', {'target': TARGET, 'timeout_seconds': 60,
            'provided_urls': [FIRST], 'runtime_patch': str(tmp_path/'runtime.yml')}, ())
    assert seen == ['discovery']
    assert targets[0]['required_urls'] == [FIRST]
    assert any(issue.get('url') == FIRST and issue['status'] == 'previous_branch_omitted'
               for issue in caught.value.safe_feedback['issues'])
    assert FIRST in caught.value.safe_feedback['known_urls']


def test_resumed_discovery_cannot_erase_a_newly_supplied_source(monkeypatch):
    value, events = discovery([branch(SECOND)])
    def execute(*args):
        raise AssertionError('Missing required source must fail before another branch/model phase')
    monkeypatch.setattr(agent_recon, '_execute_harness', execute)
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness('resumed', {'target': TARGET, 'timeout_seconds': 60,
            'provided_urls': [FIRST], 'resume_discovery': {'plan': value, 'events': events, 'session_id': 'older'}}, ())
    assert FIRST in caught.value.safe_feedback['known_urls']


def test_given_source_does_not_limit_discovery_of_additional_branches(monkeypatch):
    values = [branch(FIRST), branch(SECOND)]
    found, discovery_events = discovery(values)
    def execute(prompt, sid, cfg, patches):
        if cfg['phase'] == 'discovery':
            return response(found, discovery_events)
        value = next(value for value in values if value['url'] in prompt)
        return response(value, selector_events(value, sid))
    monkeypatch.setattr(agent_recon, '_execute_harness', execute)
    result = agent_recon._run_staged_harness('additional', {'target': TARGET, 'timeout_seconds': 60,
        'provided_urls': [FIRST]}, ())
    assert [value['url'] for value in json.loads(result.final_response)['branches']] == [FIRST, SECOND]


def test_unreadable_supplied_source_has_explicit_disposition_not_silent_omission(monkeypatch):
    value = branch(SECOND)
    disposition = {'url': FIRST, 'reason': 'source_unreadable'}
    found, events = discovery([value], [disposition])
    def execute(prompt, sid, cfg, patches):
        return (response(found, events) if cfg['phase'] == 'discovery'
                else response(value, selector_events(value, sid)))
    monkeypatch.setattr(agent_recon, '_execute_harness', execute)
    result = agent_recon._run_staged_harness('disposition', {'target': TARGET, 'timeout_seconds': 60,
        'provided_urls': [FIRST]}, ())
    assert json.loads(result.final_response)['source_dispositions'] == [disposition]


@pytest.mark.asyncio
async def test_final_public_validator_still_rejects_omission_if_harness_returns_a_sibling(monkeypatch, tmp_path):
    value = branch(SECOND)
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', lambda *args: response(plan(value), selector_events(value)))
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        await agent_recon.reconnoiter(TARGET['school'], TARGET['college'], TARGET['url'],
            config={'workspace': str(tmp_path), 'patches': []}, provided_urls=[FIRST])
    assert caught.value.safe_feedback['known_urls'] == [FIRST]
    assert caught.value.safe_feedback['issues'] == [{'path': '$.branches', 'url': FIRST,
                                                   'status': 'previous_branch_omitted'}]


@pytest.mark.asyncio
async def test_final_coverage_allows_only_observed_redirect_aliases(monkeypatch, tmp_path):
    original, canonical = FIRST, FIRST+'/'
    value = branch(canonical)
    events = pair('browser_navigate', {'url': original}, {'url': canonical}, 'redirect') + selector_events(value)
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', lambda *args: response(plan(value), events))
    result = await agent_recon.reconnoiter(TARGET['school'], TARGET['college'], TARGET['url'],
        config={'workspace': str(tmp_path), 'patches': []}, provided_urls=[original])
    assert result.plan['branches'][0]['url'] == canonical
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', lambda *args: response(plan(value), selector_events(value)))
    with pytest.raises(agent_recon.ReconPlanError):
        await agent_recon.reconnoiter(TARGET['school'], TARGET['college'], TARGET['url'],
            config={'workspace': str(tmp_path), 'patches': []}, provided_urls=[original])


@pytest.mark.asyncio
async def test_final_public_coverage_includes_verified_explicit_child_without_weakening_its_gate(monkeypatch, tmp_path):
    import copy
    from pathlib import Path
    value = navigation()
    events = all_events(value)
    config = {'workspace': str(tmp_path),
              'patches': [str(Path(agent_recon.__file__).with_name('recon_runtime.cordis.yml'))]}
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', lambda *args: response(plan(value), events))
    accepted = await agent_recon.reconnoiter(TARGET['school'], TARGET['college'], TARGET['url'],
        config=config, provided_urls=[CHILDREN[0]])
    assert accepted.plan['branches'][0]['child_branches'][0]['url'] == CHILDREN[0]

    missing = copy.deepcopy(value)
    missing['child_branches'].pop(0)
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', lambda *args: response(plan(missing), events))
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        await agent_recon.reconnoiter(TARGET['school'], TARGET['college'], TARGET['url'],
            config=config, provided_urls=[CHILDREN[0]])
    assert any(issue.get('url') == CHILDREN[0] and issue['status'] == 'previous_branch_omitted'
        for issue in caught.value.safe_feedback['issues'])

    # A represented URL alone is insufficient: the same public final validator
    # still requires that child's independent source and selector evidence.
    parent_only = selector_events(value, 'parent')
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', lambda *args: response(plan(value), parent_only))
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        await agent_recon.reconnoiter(TARGET['school'], TARGET['college'], TARGET['url'],
            config=config, provided_urls=[CHILDREN[0]])
    assert any(issue.get('url') == CHILDREN[0] and issue['status'] == 'source_role_evidence_unverified'
        for issue in caught.value.safe_feedback['issues'])
