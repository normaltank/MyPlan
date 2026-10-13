"""Controlled refusals must reach the actual next phase, without sibling leakage."""
import json
from types import SimpleNamespace

import pytest

from server import agent_recon, recon_contract

TARGET = {'school': '样本大学', 'college': '样本学院', 'url': 'https://example.edu/college'}
FIRST, SECOND = 'https://example.edu/roster-a', 'https://example.edu/roster-b'
SCHEMA = {'path': '$', 'status': 'schema_model_type', 'failed_condition': 'plan_json_not_object',
          'need': 'Supply the plan object.'}


def capture_prompts(monkeypatch, feedback, *, discovery=True):
    """Exercise the production staged consumer; admission is outside this check."""
    captures = {}
    plan = {'school': TARGET['school'], 'college': TARGET['college'],
            'branches': [{'url': FIRST}, {'url': SECOND}]}
    def execute(prompt, sid, cfg, patches):
        captures[cfg['phase'] if cfg['phase'] == 'discovery' else sid.rsplit('-', 1)[1]] = prompt
        if cfg['phase'] == 'discovery':
            return SimpleNamespace(final_response=json.dumps(plan), finish_reason='completed', events=[])
        raise agent_recon.ReconError('End this synthetic check after capturing its prompt.')
    monkeypatch.setattr(agent_recon, '_execute_harness', execute)
    monkeypatch.setattr(recon_contract, 'validate_submission', lambda *_: [])
    cfg = {'target': TARGET, 'timeout_seconds': 30, 'feedback': feedback}
    if not discovery:
        cfg['resume_discovery'] = {'plan': plan, 'events': [], 'session_id': 'old-discover'}
    with pytest.raises(agent_recon.ReconError):
        agent_recon._run_staged_harness('capture', cfg, ())
    return captures


def test_actual_discovery_prompt_receives_its_schema_refusal(monkeypatch):
    feedback = {'issues': [{'status': 'plan_contract_rejected', 'phase': 'discovery',
        'session_id': 'old-discover', 'target_url': TARGET['url'], 'gate_issues': [SCHEMA]}]}
    prompts = capture_prompts(monkeypatch, feedback)
    assert 'plan_json_not_object' in prompts['discovery']
    assert 'old-discover' in prompts['discovery']
    assert 'plan_json_not_object' not in prompts['1'] + prompts['2']


def test_nested_url_refusal_reaches_only_its_actual_branch_prompt(monkeypatch):
    issue = {'url': FIRST, 'status': 'source_role_evidence_unverified',
             'failed_condition': 'current_source_unit_binding_missing'}
    feedback = {'issues': [{'status': 'plan_contract_rejected', 'gate_issues': [issue]}]}
    prompts = capture_prompts(monkeypatch, feedback, discovery=False)
    assert 'current_source_unit_binding_missing' in prompts['1']
    assert 'current_source_unit_binding_missing' not in prompts['2']
    assert FIRST not in prompts['2']


def test_branch_schema_without_a_page_url_keeps_its_session_target(monkeypatch):
    feedback = {'issues': [{'status': 'plan_contract_rejected', 'phase': 'branch',
        'session_id': 'old-branch-1', 'target_url': FIRST, 'gate_issues': [SCHEMA]}]}
    prompts = capture_prompts(monkeypatch, feedback)
    assert 'plan_json_not_object' in prompts['1']
    assert 'old-branch-1' in prompts['1']
    assert 'plan_json_not_object' not in prompts['discovery'] + prompts['2']


def test_direct_url_feedback_keeps_its_existing_scope(monkeypatch):
    issue = {'url': FIRST, 'status': 'selector_not_verified', 'failed_condition': 'url_never_verified'}
    prompts = capture_prompts(monkeypatch, {'issues': [issue]}, discovery=False)
    assert 'url_never_verified' in prompts['1']
    assert 'url_never_verified' not in prompts['2']


@pytest.mark.parametrize('item', [
    {'phase': 'branch', 'target_url': SECOND, 'gate_issues': [SCHEMA]},
    {'phase': 'discovery', 'target_url': 'https://other.edu/college', 'gate_issues': [SCHEMA]},
    {'phase': 'branch', 'target_url': FIRST, 'gate_issues': [{'url': SECOND, **SCHEMA}]},
    {'gate_issues': [{'url': 'https://other.edu/roster', **SCHEMA}]},
])
def test_unrelated_or_contradictory_gate_provenance_is_not_sent_to_first_branch(monkeypatch, item):
    prompts = capture_prompts(monkeypatch, {'issues': [{'status': 'plan_contract_rejected', **item}]})
    assert 'plan_json_not_object' not in prompts['1']
    if item.get('target_url') != SECOND:
        assert 'plan_json_not_object' not in prompts['2']


def test_nested_profile_feedback_keeps_its_bound_branch_and_filters_siblings(monkeypatch):
    child = {'url': 'https://example.edu/profile-a', 'status': 'profile_content_not_verified',
             'failed_condition': 'profile_container_unverified'}
    feedback = {'phase_rejections': [{'phase': 'branch', 'session_id': 'old-branch-1',
        'target_url': FIRST, 'issues': [{'status': 'recon_page_unverifiable', 'gate_issues': [child,
            {'url': SECOND, **SCHEMA}]}]}]}
    prompts = capture_prompts(monkeypatch, feedback, discovery=False)
    assert 'profile_container_unverified' in prompts['1']
    assert 'profile-a' in prompts['1']
    assert 'plan_json_not_object' not in prompts['1'] + prompts['2']
    assert 'profile_container_unverified' not in prompts['2']


def test_actual_snapshot_does_not_expand_historical_gate_envelopes(monkeypatch):
    prior = {'status': 'plan_contract_rejected', 'gate_issues': [{'url': FIRST, **SCHEMA}]}
    feedback = {'issues': [prior], 'phase_rejections': []}
    prompts = capture_prompts(monkeypatch, feedback)
    assert 'plan_json_not_object' not in ''.join(prompts.values())


def test_prompt_contains_only_controlled_fields_not_exception_or_model_narrative(monkeypatch):
    feedback = {'issues': [{'url': FIRST, 'status': 'selector_not_verified',
        'failed_condition': 'url_never_verified', 'exception': 'PRIVATE_EXCEPTION',
        'model_response': 'PRIVATE_MODEL_BODY', 'closest_observation': {'text': 'PRIVATE_TEACHER_BODY'},
        'need': 'teacher@example.edu', 'selector': 'main li'}]}
    prompts = capture_prompts(monkeypatch, feedback, discovery=False)
    assert 'url_never_verified' in prompts['1']
    assert all(value not in ''.join(prompts.values()) for value in
               ('PRIVATE_EXCEPTION', 'PRIVATE_MODEL_BODY', 'PRIVATE_TEACHER_BODY', 'teacher@example.edu'))


@pytest.mark.parametrize('feedback', [{}, {'issues': []}, {'issues': None, 'phase_rejections': []}])
def test_no_controlled_rejection_leaves_no_feedback_reason(monkeypatch, feedback):
    prompts = capture_prompts(monkeypatch, feedback)
    assert 'plan_contract_rejected' not in ''.join(prompts.values())


def test_cross_round_discovery_refusal_does_not_become_legacy_branch_feedback(monkeypatch):
    issue = {'status': 'source_role_evidence_unverified', 'url': FIRST,
             'failed_condition': 'current_source_unit_binding_missing'}
    old = {'issues': [issue], 'phase_rejections': [agent_recon._phase_rejection(
        'discovery', 'old-discover', TARGET['url'], [issue])]}
    incoming = {'issues': [{'status': 'plan_contract_rejected'}], 'phase_rejections': []}
    merged = agent_recon.merge_repair_feedback(old, incoming)
    prompts = capture_prompts(monkeypatch, merged)
    assert 'current_source_unit_binding_missing' not in ''.join(prompts.values())
    # Historical diagnostics/classification remain recorded, with their origin.
    assert issue in merged['issues']
    assert merged['phase_rejection_history'][0]['session_id'] == 'old-discover'


def test_retired_phase_provenance_survives_multiple_empty_retry_snapshots(monkeypatch):
    issue = {'status': 'source_role_evidence_unverified', 'url': FIRST,
             'failed_condition': 'current_source_unit_binding_missing'}
    feedback = {'issues': [issue], 'phase_rejections': [agent_recon._phase_rejection(
        'discovery', 'old-discover', TARGET['url'], [issue])]}
    incoming = {'issues': [{'status': 'plan_contract_rejected'}], 'phase_rejections': []}
    for _ in range(3):
        feedback = agent_recon.merge_repair_feedback(feedback, incoming)
    prompts = capture_prompts(monkeypatch, feedback, discovery=False)
    assert 'current_source_unit_binding_missing' not in ''.join(prompts.values())


def test_captured_counterexample_identical_to_a_retired_phase_issue_remains_applicable(monkeypatch):
    issue = {'status': 'source_role_evidence_unverified', 'url': FIRST}
    old = {'issues': [issue], 'phase_rejections': [agent_recon._phase_rejection(
        'discovery', 'old-discover', TARGET['url'], [issue])]}
    claim = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
             'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
    plan = {'school': TARGET['school'], 'college': TARGET['college'],
            'branches': [{'url': FIRST, **claim, 'card_selector': 'main li', 'single_page': True}]}
    captured = agent_recon.build_repair_feedback(plan, {'unresolved': [issue]})
    feedback = agent_recon.merge_repair_feedback(old, captured)
    feedback = agent_recon.merge_repair_feedback(feedback, {
        'issues': [{'status': 'plan_contract_rejected'}], 'phase_rejections': []})
    prompts = capture_prompts(monkeypatch, feedback, discovery=False)
    payload = json.loads(prompts['1'].rsplit('\n本阶段结构反馈：', 1)[1])
    assert issue in payload['issues']
    assert 'old-discover' not in prompts['1']
    assert 'source_role_evidence_unverified' not in prompts['2']


@pytest.mark.parametrize('phase,suffix,target,key', [
    ('discovery', '-discover', TARGET['url'], 'discovery'),
    ('branch', '-branch-1', FIRST, '1'),
])
def test_derived_phase_session_keeps_a_legal_160_character_root(monkeypatch, phase, suffix, target, key):
    sid = 'r' * 160 + suffix
    feedback = {'phase_rejections': [agent_recon._phase_rejection(phase, sid, target,
        [{'status': 'plan_contract_rejected', 'gate_issues': [SCHEMA]}])]}
    prompts = capture_prompts(monkeypatch, feedback)
    assert sid in prompts[key]


@pytest.mark.asyncio
async def test_accepted_discovery_then_unsubmitted_branch_does_not_revive_prior_phase_error(tmp_path, monkeypatch):
    from source_contract_fixture import CLAIM, source_events
    from test_recon_submission_completion import config, install_harness, submission_events
    plan = {'school': TARGET['school'], 'college': TARGET['college'],
            'branches': [{'url': url, **CLAIM} for url in (FIRST, SECOND)]}
    attempt = {'number': 1}
    def events(phase):
        if phase != 'discovery':
            return []
        # Source truth is stipulated here; post-submission validation and the
        # retry/phase consumers themselves run unchanged.
        sources = [event for index, branch in enumerate(plan['branches'])
                   for event in source_events(branch, TARGET, 'source-' + str(index))]
        navigation = []
        for index, branch in enumerate(plan['branches']):
            cid = 'navigate-' + str(index)
            navigation.extend([
                {'type': 'tool/call', 'data': {'callId': cid, 'name': 'mcp__playwright__browser_navigate',
                    'arguments': json.dumps({'url': branch['url']})}},
                {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': cid,
                    'content': [{'type': 'text', 'text': json.dumps({'url': branch['url'], 'status': 200})}]}]}}}])
        return navigation + ([] if attempt['number'] == 1 else sources) + submission_events(plan)
    install_harness(monkeypatch, events)
    with pytest.raises(agent_recon.ReconPlanError) as first:
        await agent_recon.reconnoiter(**TARGET, session_id='first', config=config(tmp_path), timeout_seconds=30)
    old = first.value.safe_feedback
    assert old['phase_rejections'][0]['phase'] == 'discovery'
    assert any(issue['status'] == 'source_role_evidence_unverified' for issue in old['issues'])
    attempt['number'] = 2
    with pytest.raises(agent_recon.ReconPlanError) as second:
        await agent_recon.reconnoiter(**TARGET, session_id='second', config=config(tmp_path),
                                    feedback=old, timeout_seconds=30)
    assert hasattr(second.value, 'discovery_checkpoint'), json.dumps(second.value.safe_feedback)
    assert second.value.discovery_checkpoint['plan'] == plan
    assert second.value.safe_feedback['phase_rejections'] == []
    merged = agent_recon.merge_repair_feedback(old, second.value.safe_feedback)
    prompts = capture_prompts(monkeypatch, merged, discovery=False)
    assert all('本阶段结构反馈：' not in prompt for prompt in prompts.values())


def test_nonretryable_page_envelope_is_not_reclassified_by_prompt_routing(monkeypatch):
    from server.recovery_policy import classify_issues
    feedback = {'issues': [{'status': 'recon_page_unverifiable', 'phase': 'branch',
        'target_url': FIRST, 'gate_issues': [{'url': FIRST, 'status': 'source_role_evidence_unverified',
                                          'page_only': True, 'failed_condition': 'source_network_not_idle'}]}]}
    prompts = capture_prompts(monkeypatch, feedback, discovery=False)
    assert 'recon_page_unverifiable' in prompts['1']
    assert 'source_network_not_idle' in prompts['1']
    assert 'source_network_not_idle' not in prompts['2']
    assert classify_issues(feedback['issues']).access
    assert not classify_issues(feedback['issues']).structure


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['discovery', 'repeat', 'branch'])
async def test_worker_actual_error_reply_reaches_next_phase(tmp_path, monkeypatch, mode):
    import deepseek_harness
    from server.agent_worker import AgentWorker
    captures = []
    class Harness:
        def __init__(self, **kwargs):
            self.phase = kwargs['env']['FACULTY_RECON_PHASE']
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def run(self, prompt, *, session_id, on_notification):
            captures.append({'phase': self.phase, 'session_id': session_id, 'prompt': prompt})
            if (mode == 'discovery' and len(captures) > 1
                    or mode == 'branch' and len(captures) > 3):
                raise agent_recon.ReconBudgetExceeded('End after capturing the actual retry consumer.')
            issue = SCHEMA
            if mode == 'branch' and self.phase == 'discovery':
                from test_recon_submission_completion import submission_events
                plan = {'school': TARGET['school'], 'college': TARGET['college'],
                        'branches': [{'url': FIRST}, {'url': SECOND}]}
                events = submission_events(plan)
            else:
                if mode == 'branch':
                    issue = {**SCHEMA, 'failed_condition': 'branch_' + ('a' if session_id.endswith('-1') else 'b') + '_plan_json_not_object'}
                events = [
                    {'type': 'tool/call', 'data': {'callId': 'rejected', 'name': 'mcp__playwright__submit_plan',
                                                 'arguments': json.dumps({'plan': 'invalid-string'})}},
                    {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': 'rejected',
                        'isError': True, 'content': [{'type': 'text', 'text': 'Error: ' + json.dumps(
                            {'accepted': False, 'issues': [issue]})}]}]}}}]
            for event in events:
                on_notification(SimpleNamespace(method='session.event', payload={
                    'sessionId': session_id, 'event': event}))
            return SimpleNamespace(final_response='unsafe model narrative teacher@example.edu',
                                   finish_reason='completed', events=events)
    monkeypatch.setattr(deepseek_harness, 'DeepSeekHarness', Harness)
    monkeypatch.setattr(agent_recon, 'check_runtime_composition', lambda *_: None)
    if mode == 'branch':
        # Stipulate admission solely to reach the worker's resumed branch prompt.
        monkeypatch.setattr(recon_contract, 'validate_submission', lambda *_: [])
    monkeypatch.setattr(agent_recon, '_config_from_env', lambda: {
        'workspace': str(tmp_path), 'dsh_home': str(tmp_path / 'sdk'), 'profile': 'sdk',
        'dsh_bin': None, 'patches': (),
        'api_key': 'non-secret-test-value', 'require_submission': True,
        'provider': 'deepseek-official', 'model': 'deepseek-flash', 'effort': 'high'})
    async def no_cache(*_):
        return None
    monkeypatch.setattr(AgentWorker, '_cached_recon', no_cache)
    worker = AgentWorker(db_path=tmp_path / 'tasks.db', output_dir=tmp_path / 'out',
                         max_recon_repairs=3 if mode == 'repeat' else 1)
    task = SimpleNamespace(**TARGET)
    if mode == 'repeat':
        report = await worker._harvest_with_repair(task, tmp_path / 'out')
        assert not report['delivery_ready']
    else:
        with pytest.raises(agent_recon.ReconBudgetExceeded):
            await worker._harvest_with_repair(task, tmp_path / 'out')
    assert len(captures) == (4 if mode == 'branch' else 2)
    refused = captures[1] if mode == 'branch' else captures[0]
    retried = captures[-1]
    assert refused['phase'] == retried['phase'] == ('branch' if mode == 'branch' else 'discovery')
    assert 'plan_json_not_object' in retried['prompt']
    assert refused['session_id'] in retried['prompt']
    assert 'teacher@example.edu' not in retried['prompt']
    if mode == 'branch':
        assert 'branch_a_plan_json_not_object' in retried['prompt']
        assert 'branch_b_plan_json_not_object' not in retried['prompt']
    audit = json.loads((tmp_path / 'out/recon-attempts.json').read_text(encoding='utf-8'))
    assert audit['attempts'][0]['issues'][0]['gate_issues'][0]['status'] == SCHEMA['status']


def test_accepted_session_does_not_replay_its_retired_schema_refusal(tmp_path, monkeypatch):
    from test_recon_submission_completion import config, install_harness, submission_events
    claim = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
             'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
    plan = {'school': TARGET['school'], 'college': TARGET['college'], 'branches': [{'url': FIRST, **claim}]}
    events = submission_events('bad string', {'accepted': False, 'issues': [SCHEMA]}, is_error=True)
    events += submission_events(plan)
    state = install_harness(monkeypatch, events)
    cfg = config(tmp_path, target=TARGET, timeout_seconds=30, gate_rejections={}, gate_rejection_contexts={})
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness('accepted', cfg, ())
    # The accepted receipt still faces real post-submission source validation.
    assert state['closed'] and not cfg['gate_rejections']
    issues = agent_recon._phase_repair_issues(caught.value.safe_feedback, 'discovery', TARGET['url'])
    assert issues and 'source_role_evidence_unverified' in json.dumps(issues)
    assert 'plan_json_not_object' not in json.dumps(issues)
