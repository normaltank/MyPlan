"""An accepted scout receipt completes its stage, before extra model spending."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from server import agent_recon
from server.recon_contract import plan_digest, submitted_plan


ARCHIVED = json.loads((Path(__file__).parent / 'fixtures/recon_receipts/qinghai_computer_discovery.json')
                     .read_text(encoding='utf-8'))


def usage():
    return {'type': 'assistant/message', 'data': {'usage': {
        'inputTokens': 10, 'cacheReadTokens': 20, 'outputTokens': 5}}}


def submission_events(plan, receipt=None, *, name='mcp__playwright__submit_plan', is_error=False):
    receipt = receipt if receipt is not None else {'accepted': True, 'digest': plan_digest(plan)}
    return [
        {'type': 'tool/call', 'data': {'name': name, 'callId': 'submit',
                                     'arguments': json.dumps({'plan': plan})}},
        {'type': 'tool/result', 'data': {'message': {'content': [
            {'toolCallId': 'submit', 'isError': is_error, 'content': [
                {'type': 'text', 'text': json.dumps(receipt)}]}]}}},
    ]


def install_harness(monkeypatch, events, *, before_event=None, foreign_session=False):
    import deepseek_harness
    state = {'emitted': [], 'closed': False, 'natural_return': False}

    class Harness:
        def __init__(self, **kwargs):
            self.phase = kwargs['env']['FACULTY_RECON_PHASE']

        def __enter__(self):
            return self

        def __exit__(self, *_):
            state['closed'] = True

        def run(self, prompt, *, session_id, on_notification):
            phase_events = events(self.phase) if callable(events) else events
            for event in phase_events:
                if before_event:
                    before_event(event)
                state['emitted'].append(event)
                on_notification(SimpleNamespace(method='session.event', payload={
                    'sessionId': 'other-session' if foreign_session else session_id, 'event': event}))
            state['natural_return'] = True
            return SimpleNamespace(final_response='natural completion', finish_reason='completed', events=phase_events)

    monkeypatch.setattr(deepseek_harness, 'DeepSeekHarness', Harness)
    monkeypatch.setattr(agent_recon, 'check_runtime_composition', lambda *_: None)
    return state


def config(tmp_path, **overrides):
    return {'dsh_home': str(tmp_path / 'sdk'), 'workspace': str(tmp_path), 'dsh_bin': None,
            'profile': 'sdk', 'provider': 'deepseek-official', 'model': 'deepseek-flash',
            'effort': 'high', 'api_key': 'non-secret-test-value', 'require_submission': True,
            'phase': 'discovery', **overrides}


def test_archived_accepted_discovery_does_not_run_into_next_budgeted_request(tmp_path, monkeypatch):
    plan, receipt = ARCHIVED['plan'], ARCHIVED['receipt']
    assert plan_digest(plan) == receipt['digest']
    accepted = submission_events(plan, receipt)
    events = [usage(), *accepted, usage()]
    state = install_harness(monkeypatch, events)
    budget = agent_recon.ReconBudget(max_calls=2, max_tokens=1000)

    result = agent_recon._execute_harness('structure only', 'accepted-discovery',
                                         config(tmp_path, budget=budget), ())

    assert result.finish_reason == 'completed'
    assert submitted_plan(result.events) == plan
    assert json.loads(result.final_response) == plan
    assert state['closed'] and not state['natural_return']
    assert state['emitted'] == events[:-1]
    assert budget.snapshot()['calls'] == 1
    assert budget.snapshot()['token_usage'] == {'uncached_input': 10, 'cached_input': 20, 'output': 5}
    assert result.completion_reason == 'accepted_submission' and result.usage_unknown
    assert budget.snapshot()['cost']['unknown_calls'] == 1
    assert budget.snapshot()['cost']['estimated_usd'] is None


@pytest.mark.parametrize('receipt,is_error,name', [
    ({'accepted': False, 'issues': [{'status': 'source_role_evidence_unverified'}]}, False,
     'mcp__playwright__submit_plan'),
    ({'accepted': True, 'digest': 'wrong-digest'}, False, 'mcp__playwright__submit_plan'),
    (ARCHIVED['receipt'], True, 'mcp__playwright__submit_plan'),
    (ARCHIVED['receipt'], False, 'mcp__playwright__browser_verify_source'),
])
def test_unaccepted_unbound_or_other_tool_reply_keeps_normal_session(tmp_path, monkeypatch,
                                                                    receipt, is_error, name):
    events = [usage(), *submission_events(ARCHIVED['plan'], receipt, is_error=is_error, name=name), usage()]
    state = install_harness(monkeypatch, events)
    result = agent_recon._execute_harness('structure only', 'invalid-receipt', config(tmp_path), ())
    assert state['natural_return'] and state['closed']
    assert result.final_response == 'natural completion'


def test_receipt_from_another_session_does_not_complete_this_session(tmp_path, monkeypatch):
    events = [usage(), *submission_events(ARCHIVED['plan'])]
    state = install_harness(monkeypatch, events, foreign_session=True)
    result = agent_recon._execute_harness('structure only', 'our-session', config(tmp_path), ())
    assert state['natural_return'] and result.final_response == 'natural completion'


def test_legacy_non_submission_caller_keeps_normal_completion(tmp_path, monkeypatch):
    events = [usage(), *submission_events(ARCHIVED['plan']), usage()]
    state = install_harness(monkeypatch, events)
    result = agent_recon._execute_harness('structure only', 'legacy', config(tmp_path, require_submission=False), ())
    assert state['natural_return'] and result.final_response == 'natural completion'


@pytest.mark.parametrize('flag_name', ['cancel_event', 'branch_stop_event'])
def test_caller_cancellation_wins_over_an_accepted_receipt(tmp_path, monkeypatch, flag_name):
    flag = threading.Event()
    events = [usage(), *submission_events(ARCHIVED['plan'])]
    def cancel_at_receipt(event):
        if event['type'] == 'tool/result':
            flag.set()
    state = install_harness(monkeypatch, events, before_event=cancel_at_receipt)
    budget = agent_recon.ReconBudget()
    with pytest.raises(agent_recon.ReconCancelled):
        agent_recon._execute_harness('structure only', 'cancelled', config(tmp_path, budget=budget,
                                                                          **{flag_name: flag}), ())
    assert state['closed'] and not state['natural_return']
    assert budget.snapshot()['calls'] == 1


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('tamper_branch', [False, True])
async def test_real_scout_receipts_complete_stages_and_preserve_source_revalidation(tmp_path, monkeypatch,
                                                                                 tamper_branch):
    from playwright.async_api import async_playwright
    from server.recon_browser_mcp import BrowserScout

    target = {'school': '样本大学', 'college': '样本学院', 'url': 'https://fixture.example.edu/faculty'}
    branch = {'url': target['url'], 'source_role': 'faculty_roster', 'source_container_selector': 'main',
              'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
    plan = {'school': target['school'], 'college': target['college'], 'branches': [branch]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(service_workers='block')
        await context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=(
            '<meta charset="utf-8"><title>教师名录</title><header>样本大学 样本学院</header>'
            '<h1>教师名录</h1><main><ul><li>张华</li></ul></main>')))
        try:
            page = await context.new_page()
            await page.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf-8'))
            scout = BrowserScout(page, tmp_path / 'scout', 'discovery', target)
            await scout.call('browser_navigate', {'url': target['url']})
            proof = await scout.call('browser_verify_source', {k:v for k,v in branch.items() if k != 'url'})
            assert json.loads(proof['content'][0]['text'])['confirmed'] is True
            discovery_proof = copy.deepcopy(scout.events)
            receipt = await scout.call('submit_plan', {'plan': plan})
            assert json.loads(receipt['content'][0]['text'])['accepted'] is True
            discovery_events = [usage(), *discovery_proof,
                                *submission_events(plan, json.loads(receipt['content'][0]['text'])), usage()]
            roster = {**branch, 'card_selector': 'main ul > li', 'single_page': True}
            scout = BrowserScout(page, tmp_path / 'branch-scout', 'branch', target)
            await scout.call('browser_navigate', {'url': target['url']})
            await scout.call('browser_verify_source', {k:v for k,v in branch.items() if k != 'url'}
                             | {'unit_selector': roster['card_selector']})
            await scout.call('browser_verify', {'card_selector': roster['card_selector']})
            branch_proof = copy.deepcopy(scout.events)
            receipt = await scout.call('submit_plan', {'plan': roster})
            assert json.loads(receipt['content'][0]['text'])['accepted'] is True
            returned_branch = {**roster, 'source_context_selector': 'footer'} if tamper_branch else roster
            # Even a digest-correct returned object cannot borrow another claim's
            # source proof when the staged aggregator revalidates the result.
            branch_events = [usage(), *branch_proof, *submission_events(returned_branch), usage()]
        finally:
            await context.close()
            await browser.close()

    state = install_harness(monkeypatch, discovery_events)
    result = agent_recon._execute_harness('structure only', 'real-scout', config(tmp_path), ())
    assert not state['natural_return'] and submitted_plan(result.events) == plan
    assert agent_recon.run_metrics(result.events)['model_calls'] == 1

    state = install_harness(monkeypatch, lambda phase: discovery_events if phase == 'discovery' else branch_events)
    cfg = config(tmp_path, target=target, timeout_seconds=60, parallel_branches=False, template_reuse=False)
    if tamper_branch:
        with pytest.raises(agent_recon.ReconPlanError) as caught:
            agent_recon._run_staged_harness('verified-stages', cfg, ())
        assert 'source_role_evidence_unverified' in [i['status'] for i in caught.value.safe_feedback['issues']]
    else:
        result = agent_recon._run_staged_harness('verified-stages', cfg, ())
        assert json.loads(result.final_response)['branches'] == [roster]
        assert [p['completion_reason'] for p in result.phase_metrics] == ['accepted_submission'] * 2
        assert result.usage_unknown is True
        assert agent_recon.run_metrics(result.events)['model_calls'] == 2
    assert state['closed'] and not state['natural_return']
