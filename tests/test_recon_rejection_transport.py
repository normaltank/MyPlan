"""Actual SDK error envelopes and large submit rejections keep their protocol."""
import copy
import hashlib
import json
from pathlib import Path
import threading

import pytest

from server.agent_recon import _watch_gate
from server.recon_browser_mcp import BrowserScout, MAX_INLINE_BYTES

FIXTURES = Path(__file__).parent / 'fixtures/recon_receipts'
ARCHIVED = json.loads((FIXTURES / 'repair3_submission_errors.json').read_text(encoding='utf-8'))


def body(reply):
    return json.loads(reply['content'][0]['text'])


def many_sources():
    target = {'school': '样本大学', 'college': '样本学院', 'url': 'https://sample.example.edu/'}
    plan = {'school': target['school'], 'college': target['college'], 'branches': [],
            'source_dispositions': [{'url': target['url'] + f'people/{index}',
                'reason': 'source_relationship_unverified', 'source_role': 'unverified_people_source',
                'source_container_selector': 'main', 'role_evidence_selector': 'h1',
                'source_context_selector': 'header'} for index in range(100)]}
    return target, plan


@pytest.mark.parametrize('case', ARCHIVED['cases'], ids=lambda case: case['session_id'])
def test_real_sdk_rejection_is_saved_by_bound_submit_listener(case):
    calls, rejections = set(), {}
    for event in case['events']:
        _watch_gate(event, calls, rejections, case['session_id'])
    assert rejections[case['session_id']]
    assert all(item.get('status') for item in rejections[case['session_id']])


@pytest.mark.asyncio
async def test_actual_multi_source_rejection_preserves_inline_control_and_complete_diagnostic(tmp_path):
    target, plan = many_sources()
    scout = BrowserScout(None, tmp_path, 'discovery', target)
    first = await scout.call('submit_plan', {'plan': plan})
    packet = body(first)
    assert packet['accepted'] is False and packet['issues'] and packet['issue_count'] == 100
    assert len(first['content'][0]['text'].encode('utf-8')) <= MAX_INLINE_BYTES
    from server.recon_contract import decode_submission_reply
    reference = packet['full_submission_result']
    raw = Path(reference['evidence_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == reference['sha256']
    complete = decode_submission_reply('Error: ' + first['content'][0]['text'], is_error=True)
    assert complete['accepted'] is False and len(complete['issues']) == 100
    assert {issue['failed_condition'] for issue in complete['issues']} == {'current_source_role_proof_missing'}
    await scout.call('submit_plan', {'plan': plan})
    stopped = await scout.call('submit_plan', {'plan': plan})
    packet = body(stopped)
    assert packet['stop'] == 'recon_gate_stalled' and packet['unchanged_resubmission'] == 1
    calls, rejections = set(), {}
    _watch_gate({'type': 'tool/call', 'data': {'callId': 's', 'name': 'mcp__playwright__submit_plan'}}, calls, rejections, 'sid')
    result = {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': 's', 'isError': True,
              'content': [{'type': 'text', 'text': 'Error: ' + stopped['content'][0]['text']}]}]}}}
    stop = _watch_gate(result, calls, rejections, 'sid')
    assert stop.code == 'recon_gate_stalled' and len(rejections['sid']) == 100


def test_archived_large_rejection_is_rendered_without_losing_conditions(tmp_path):
    from server.recon_contract import decode_submission_reply
    raw = (FIXTURES / 'repair3_economics_rejection.json').read_bytes()
    assert len(raw) == 27285
    assert hashlib.sha256(raw).hexdigest() == 'ac5a3bcc878fdb3f862f14ed8ab96f5eefc05363e2f9ccadca8d6e0530a5a177'
    original = json.loads(raw)
    scout = BrowserScout(None, tmp_path, 'discovery')
    result = scout.submission_result(original)
    packet = body(result)
    assert packet['accepted'] is False and packet['issue_count'] == 13 and packet['issues']
    assert decode_submission_reply(result['content'][0]['text'], is_error=True) == original
    assert all('closest_observation' not in issue for issue in packet['issues'])


def test_inline_bound_includes_final_complete_preview_flag(tmp_path):
    scout = BrowserScout(None, tmp_path, 'discovery')
    original = {'accepted': False, 'issues': [{'status': 'blocked', 'need': '',
                 'closest_observation': {'large': 'x' * 10000}}]}
    first = scout.submission_result(original)
    initial_bytes = len(first['content'][0]['text'].encode('utf-8'))
    original['issues'][0]['need'] = 'x' * (MAX_INLINE_BYTES - initial_bytes + 1)
    second = scout.submission_result(original)
    assert len(second['content'][0]['text'].encode('utf-8')) <= MAX_INLINE_BYTES
    assert body(second)['issues_truncated'] is True


@pytest.mark.parametrize('is_error,prefix', [(True, ''), (True, 'Error: '), (False, 'Error: ')])
def test_error_or_error_wrapped_acceptance_never_clears_rejection(is_error, prefix):
    calls, rejections = {'s'}, {'sid': [{'status': 'keep_rejected'}]}
    result = {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': 's', 'isError': is_error,
             'content': [{'type': 'text', 'text': prefix + json.dumps({'accepted': True, 'digest': 'a' * 64})}]}]}}}
    assert _watch_gate(result, calls, rejections, 'sid') is None
    assert rejections == {'sid': [{'status': 'keep_rejected'}]}


def test_wrong_tool_and_bad_json_do_not_become_submission_replies():
    calls, rejections = set(), {}
    _watch_gate({'type': 'tool/call', 'data': {'callId': 'web', 'name': 'mcp__playwright__browser_evaluate'}}, calls, rejections, 'sid')
    for call_id, text in [('web', 'Error: {"accepted":false,"issues":[{"status":"webpage"}]}'),
                           ('unknown', '{bad json')]:
        result = {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': call_id,
                  'content': [{'type': 'text', 'text': text}]}]}}}
        assert _watch_gate(result, calls, rejections, 'sid') is None
    assert not rejections


def test_reused_call_id_is_bound_to_its_latest_actual_tool():
    calls, rejections = set(), {}
    for name in ('mcp__playwright__submit_plan', 'mcp__playwright__browser_evaluate'):
        _watch_gate({'type': 'tool/call', 'data': {'callId': 'same', 'name': name}}, calls, rejections, 'sid')
    result = {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': 'same', 'isError': True,
        'content': [{'type': 'text', 'text': 'Error: {"accepted":false,"issues":[{"status":"webpage"}],"stop":"recon_gate_stalled"}'}]}]}}}
    assert _watch_gate(result, calls, rejections, 'sid') is None and not rejections


@pytest.mark.parametrize('corruption', ['digest', 'missing', 'control', 'count', 'issue', 'nested', 'too_large', 'accepted_file'])
def test_invalid_full_rejection_packet_is_not_consumed(tmp_path, corruption):
    from server.recon_contract import decode_submission_reply
    original = json.loads((FIXTURES / 'repair3_economics_rejection.json').read_text(encoding='utf-8'))
    packet = body(BrowserScout(None, tmp_path, 'discovery').submission_result(original))
    reference = packet['full_submission_result']
    path = Path(reference['evidence_path'])
    if corruption == 'digest':
        reference['sha256'] = '0' * 64
    elif corruption == 'missing':
        path.unlink()
    elif corruption == 'control':
        packet['stop'] = 'recon_gate_stalled'
    elif corruption == 'count':
        packet['issue_count'] += 1
    elif corruption == 'issue':
        packet['issues'][0]['status'] = 'invented_condition'
    else:
        changed = copy.deepcopy(original)
        if corruption == 'nested':
            changed['full_submission_result'] = dict(reference)
        elif corruption == 'accepted_file':
            changed['accepted'] = True
        else:
            changed['padding'] = 'x' * (1024 * 1024)
        raw = json.dumps(changed).encode('utf-8')
        path.write_bytes(raw)
        reference['sha256'] = hashlib.sha256(raw).hexdigest()
    assert decode_submission_reply('Error: ' + json.dumps(packet), is_error=True) is None


@pytest.mark.asyncio
async def test_error_wrapped_stop_unwinds_harness_without_extra_usage(tmp_path, monkeypatch):
    from server import agent_recon
    from test_recon_submission_completion import install_harness, config, usage
    target, plan = many_sources()
    scout = BrowserScout(None, tmp_path / 'scout', 'discovery', target)
    await scout.call('submit_plan', {'plan': plan})
    await scout.call('submit_plan', {'plan': plan})
    reply = await scout.call('submit_plan', {'plan': plan})
    events = [usage(), {'type': 'tool/call', 'data': {'callId': 'stop', 'name': 'mcp__playwright__submit_plan',
              'arguments': json.dumps({'plan': plan})}}, {'type': 'tool/result', 'data': {'message': {'content': [
              {'toolCallId': 'stop', 'isError': True, 'content': [{'type': 'text',
               'text': 'Error: ' + reply['content'][0]['text']}]}]}}}, usage()]
    state = install_harness(monkeypatch, events)
    budget = agent_recon.ReconBudget(max_calls=3, max_tokens=1000)
    budget.mark_usage_unknown()  # An earlier interrupted phase's uncertainty survives this gate stop.
    with pytest.raises(agent_recon.ReconGateStopped) as caught:
        agent_recon._execute_harness('structure only', 'gate-session', config(tmp_path, budget=budget), ())
    assert caught.value.code == 'recon_gate_stalled'
    assert state['closed'] and not state['natural_return'] and state['emitted'] == events[:-1]
    assert budget.snapshot()['calls'] == 1
    assert budget.snapshot()['cost']['unknown_calls'] == 1


@pytest.mark.parametrize('flag_name', ['cancel_event', 'branch_stop_event'])
def test_cancellation_wins_when_wrapped_gate_stop_arrives(tmp_path, monkeypatch, flag_name):
    from server import agent_recon
    from test_recon_submission_completion import install_harness, config, usage
    flag = threading.Event()
    call, result = copy.deepcopy(ARCHIVED['cases'][0]['events'])
    reply = {'accepted': False, 'issues': [{'status': 'source_role_evidence_unverified'}], 'stop': 'recon_gate_stalled'}
    result['data']['message']['content'][0]['content'][0]['text'] = 'Error: ' + json.dumps(reply)
    def cancel_at_receipt(event):
        if event['type'] == 'tool/result':
            flag.set()
    events = [usage(), call, result, usage()]
    state = install_harness(monkeypatch, events, before_event=cancel_at_receipt)
    budget = agent_recon.ReconBudget(max_calls=3, max_tokens=1000)
    with pytest.raises(agent_recon.ReconCancelled):
        agent_recon._execute_harness('structure only', 'cancelled-gate', config(tmp_path, budget=budget,
                                    **{flag_name: flag}), ())
    assert state['closed'] and not state['natural_return'] and state['emitted'] == events[:-1]
    assert budget.snapshot()['calls'] == 1 and budget.snapshot()['cost']['unknown_calls'] == 1


def test_non_submission_large_results_keep_existing_incomplete_protocol(tmp_path):
    result = body(BrowserScout(None, tmp_path, 'discovery').text_result({'webpage': 'x' * 10000}))
    assert result['status'] == 'result_too_large' and result['complete'] is False
    assert 'full_submission_result' not in result


@pytest.mark.browser
@pytest.mark.asyncio
async def test_real_browser_large_rejection_preserves_false_source_affiliation(tmp_path):
    from playwright.async_api import async_playwright
    from server.recon_contract import decode_submission_reply
    target = {'school': '样本大学', 'college': '样本学院', 'url': 'https://sample.example.edu/'}
    claim = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
             'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
    branches = [{'url': target['url'] + f'faculty/{index}', **claim} for index in range(13)]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context(service_workers='block')
            await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf-8'))
            await context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=(
                '<meta charset="utf-8"><title>教师名录</title><header>样本大学其他学院</header>'
                '<h1>教师名录</h1><main><ul><li>正式名录条目。</li></ul><p>' + '原始公开结构内容。' * 40 + '</p></main>')))
            scout = BrowserScout(await context.new_page(), tmp_path / 'browser', 'discovery', target)
            for branch in branches:
                await scout.call('browser_navigate', {'url': branch['url']})
                proof = body(await scout.call('browser_verify_source', claim))
                assert proof['college_bound'] is False and proof['confirmed'] is False
            reply = await scout.call('submit_plan', {'plan': {**{k:target[k] for k in ('school', 'college')},
                                                           'branches': branches}})
            packet = body(reply)
            assert packet['accepted'] is False and packet['issue_count'] == 13
            complete = decode_submission_reply('Error: ' + reply['content'][0]['text'], is_error=True)
            assert len(complete['issues']) == 13
            assert all(issue['closest_observation']['college_bound'] is False for issue in complete['issues'])
            assert all('target_institution_unverified' in issue['failed_conditions'] for issue in complete['issues'])
        finally:
            await browser.close()
