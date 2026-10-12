"""A source rejection carries current observations, not one generic diagnosis."""
import json
import hashlib
from pathlib import Path
import pytest

from server.source_qualification import SOURCE_FIELDS, read_source_packet, source_event_issues, source_event_proof, witness

URL = 'https://example.edu/roster'
BRANCH = {'url': URL, 'source_role': 'faculty_roster', 'source_container_selector': 'main',
          'role_evidence_selector': 'h1', 'source_context_selector': 'header', 'card_selector': 'main li'}
TARGET = {'school': '样本大学', 'college': '样本学院'}


def events(proof, *, call_id='proof', unit='main li'):
    args = {key: BRANCH[key] for key in SOURCE_FIELDS}
    args['unit_selector'] = unit
    return [
        {'type': 'tool/call', 'data': {'callId': call_id, 'source_url': URL,
            'name': 'mcp__playwright__browser_verify_source', 'arguments': json.dumps(args)}},
        {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': call_id,
            'content': [{'type': 'text', 'text': json.dumps(proof)}]}]}}},
    ]


def proof(label='教师名单'):
    html = (f'<title>{label}</title><header>样本大学样本学院</header><h1>{label}</h1>'
            '<main><li>独立人员观察</li></main>')
    return {**witness(html, URL, BRANCH, TARGET), 'confirmed': label == '教师名单',
        'witness_visible': True, 'network_idle': True, 'dom_stable': True,
        'original_path': 'source.html', 'evidence_path': 'witness.json'}


def test_unknown_purpose_is_distinct_from_unreadable_or_wrong_institution():
    value = proof('未知的名录用途')
    issue, = source_event_issues(BRANCH, events(value))
    assert issue['failed_conditions'] == ['faculty_relationship_unverified']
    observation = issue['closest_observation']
    assert observation['college_bound'] and observation['witness_visible']
    assert observation['dom_stable'] and observation['network_idle']
    assert not observation['role_supported']
    assert observation['source_html_sha256'] == value['source_html_sha256']
    assert observation['original_path'] == value['original_path']
    assert 'not_faculty_roster' not in issue['failed_conditions']


def test_disposition_rejection_preserves_the_actual_missing_source_conditions():
    from server.recon_contract import disposition_event_issues
    item = {**{key: BRANCH[key] for key in SOURCE_FIELDS}, 'url': URL,
            'reason': 'not_faculty_roster', 'source_role': 'organizational_membership'}
    args = {key: item[key] for key in SOURCE_FIELDS}
    value = {**proof(), **args, 'confirmed': False, 'witness_visible': False}
    history = events(value, unit=None)
    history[0]['data']['arguments'] = json.dumps(args)
    issue, = disposition_event_issues({'source_dispositions': [item]}, history)
    assert issue['status'] == 'source_disposition_unverified'
    assert issue['failed_condition'] == 'source_witness_not_visible'
    assert issue['closest_observation']['witness_visible'] is False
    assert issue['reason'] == 'not_faculty_roster' and issue['need']


def test_unknown_purpose_cannot_be_excluded_and_the_feedback_names_that_condition():
    from server.recon_contract import disposition_event_issues
    item = {**{key: BRANCH[key] for key in SOURCE_FIELDS}, 'url': URL,
            'reason': 'not_faculty_roster', 'source_role': 'organizational_membership'}
    args = {key: item[key] for key in SOURCE_FIELDS}
    value = {**proof('人员展示'), **args, 'role_supported': True, 'confirmed': True}
    history = events(value, unit=None)
    history[0]['data']['arguments'] = json.dumps(args)
    issue, = disposition_event_issues({'source_dispositions': [item]}, history)
    assert issue['failed_condition'] == 'non_faculty_purpose_unverified'
    assert issue['closest_observation']['non_faculty_purpose'] is False
    assert 'unknown' in issue['need']


def test_unreadable_disposition_needs_an_actual_access_failure():
    from server.recon_contract import disposition_event_issues
    history = events(proof())
    history[0]['data'].update(name='mcp__playwright__browser_navigate', arguments=json.dumps({'url': URL}))
    block = history[1]['data']['message']['content'][0]
    block['content'][0]['text'] = json.dumps({'url': URL, 'status': 200, 'source_html_sha256': 'a' * 64})
    item = {'url': URL, 'reason': 'source_unreadable'}
    issue, = disposition_event_issues({'source_dispositions': [item]}, history)
    assert issue['failed_condition'] == 'access_failure_not_observed'
    assert issue['closest_observation']['status'] == 200


def test_relationship_disposition_reports_role_binding_instead_of_a_proven_purpose():
    from server.recon_contract import disposition_event_issues
    item = {**{key: BRANCH[key] for key in SOURCE_FIELDS}, 'url': URL,
            'reason': 'source_relationship_unverified', 'source_role': 'organizational_membership'}
    args = {key: item[key] for key in SOURCE_FIELDS}
    value = {**proof('学院领导'), **args, 'role_supported': True,
             'confirmed': True, 'non_faculty_purpose': True}
    history = events(value, unit=None)
    history[0]['data']['arguments'] = json.dumps(args)
    issue, = disposition_event_issues({'source_dispositions': [item]}, history)
    assert issue['failed_condition'] == 'unknown_source_role_binding_missing'
    assert issue['closest_observation']['non_faculty_purpose'] is True
    assert 'source_role=unverified_people_source' in issue['need']


def test_latest_exact_failed_call_retires_prior_success_and_explains_all_failures():
    good = proof()
    bad = {**good, 'confirmed': False, 'college_bound': False, 'witness_visible': False,
           'dom_stable': False, 'network_idle': False, 'role_supported': False,
           'reason': 'source_context_not_visible'}
    history = events(good, call_id='old') + events(bad, call_id='current')
    assert source_event_proof(BRANCH, history) is None
    issue, = source_event_issues(BRANCH, history)
    assert set(issue['failed_conditions']) == {
        'source_witness_not_visible', 'target_institution_unverified',
        'faculty_relationship_unverified', 'source_dom_unstable', 'source_network_not_idle'}
    assert issue['closest_observation']['reason'] == 'source_context_not_visible'


def test_other_source_and_parameter_proof_cannot_supply_current_observation():
    history = events({**proof(), 'url': 'https://other.edu/roster'})
    history[0]['data']['source_url'] = 'https://other.edu/roster'
    issue, = source_event_issues(BRANCH, history)
    assert issue['failed_condition'] == 'current_source_role_proof_missing'
    assert not issue.get('closest_observation')
    assert source_event_issues({**BRANCH, 'source_container_selector': '#different'}, events(proof()))[0][
        'failed_condition'] == 'current_source_role_proof_missing'


def test_unit_binding_mismatch_has_a_specific_condition():
    issue, = source_event_issues(BRANCH, events(proof(), unit='.other li'))
    assert issue['failed_conditions'] == ['current_source_unit_binding_missing']


def test_old_source_proof_is_reverified_instead_of_reused_by_new_protocol():
    old = {key: value for key, value in proof().items() if key != 'source_evidence_version'}
    assert source_event_proof(BRANCH, events(old)) is None
    issue, = source_event_issues(BRANCH, events(old))
    assert issue['failed_conditions'] == ['source_evidence_version_mismatch']


@pytest.mark.parametrize('kind', ['tool_error', 'pending', 'bad_json', 'wrong_url'])
def test_latest_call_failure_never_falls_back_to_old_success(kind):
    latest = events(proof(), call_id='new')
    block = latest[1]['data']['message']['content'][0]
    if kind == 'tool_error':
        block.update(isError=True, content=[{'type': 'text', 'text': json.dumps({
            'url': URL, 'source_call_url': URL, 'status': 'browser_source_verification_failed'})}])
    elif kind == 'pending':
        latest = latest[:1]
    elif kind == 'bad_json':
        block['content'][0]['text'] = 'invalid JSON'
    else:
        block['content'][0]['text'] = json.dumps({**proof(), 'url': 'https://other.edu/roster'})
    history = events(proof(), call_id='old') + latest
    assert source_event_proof(BRANCH, history) is None
    assert source_event_issues(BRANCH, history)


def test_reply_order_does_not_override_the_latest_initiated_call():
    old, new = events(proof(), call_id='old'), events({**proof(), 'confirmed': False}, call_id='new')
    history = [old[0], new[0], new[1], old[1]]
    assert source_event_proof(BRANCH, history) is None


def test_same_css_failure_on_another_page_does_not_revoke_this_source():
    other = 'https://example.edu/another'
    failure = events(proof(), call_id='other')
    failure[0]['data']['source_url'] = other
    failure[1]['data']['message']['content'][0].update(isError=True, content=[{
        'type': 'text', 'text': json.dumps({'url': other, 'source_call_url': other,
                                          'status': 'browser_source_verification_failed'})}])
    history = events(proof(), call_id='good') + failure
    assert source_event_proof(BRANCH, history) is not None
    assert not source_event_issues(BRANCH, history)
    assert source_event_issues({**BRANCH, 'url': other}, history)[0]['failed_condition'] == 'source_verification_tool_failed'


def test_hashed_complete_packet_controls_the_gate_and_tampering_retires_it(tmp_path):
    value = proof()
    path = tmp_path / 'complete-source.json'
    raw = json.dumps(value).encode('utf8')
    path.write_bytes(raw)
    packet = {'url': URL, 'confirmed': False, 'full_source_result': {
        'evidence_path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}}
    assert read_source_packet(packet) == value
    assert source_event_proof(BRANCH, events(packet)) is not None
    history = events(value, call_id='old') + events(packet, call_id='packet')
    path.write_bytes(raw + b' ')
    assert read_source_packet(packet) is None
    assert source_event_proof(BRANCH, history) is None
    assert source_event_issues(BRANCH, history)
    path.unlink()
    assert read_source_packet(packet) is None


def test_failed_packet_and_pending_call_after_survey_use_sdk_page_observations(tmp_path):
    old = events({**proof(), 'source_call_url': URL}, call_id='old')
    old[0]['data'].pop('source_url')
    other = 'https://example.edu/other'
    navigation = [
        {'type': 'tool/call', 'data': {'callId': 'navigate', 'name': 'mcp__playwright__browser_navigate',
            'arguments': json.dumps({'url': other})}},
        {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': 'navigate',
            'content': [{'type': 'text', 'text': json.dumps({'url': other})}]}]}}},
        {'type': 'tool/call', 'data': {'callId': 'survey', 'name': 'mcp__playwright__browser_survey',
            'arguments': json.dumps({'urls': [URL]})}},
        {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': 'survey',
            'content': [{'type': 'text', 'text': json.dumps({'pages': [{'url': URL}]})}]}]}}},
    ]
    latest = events({'url': URL, 'source_call_url': URL, 'full_source_result': {
        'sha256': 'a' * 64, 'evidence_path': str(tmp_path / 'missing.json')}}, call_id='latest')
    latest[0]['data'].pop('source_url')
    assert source_event_proof(BRANCH, old + navigation + latest) is None
    assert source_event_issues(BRANCH, old + navigation + latest)
    assert source_event_proof(BRANCH, old + navigation + latest[:1]) is None


@pytest.mark.browser
@pytest.mark.asyncio
async def test_real_source_tool_failure_has_original_and_retires_the_success(tmp_path, monkeypatch):
    from playwright.async_api import async_playwright
    from server.recon_browser_mcp import BrowserScout
    import server.source_qualification as qualification

    html = '<title>教师名单</title><header>样本大学样本学院</header><h1>教师名单</h1><main><li>正式名单</li></main>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        scout = BrowserScout(page, tmp_path, 'discovery', {**TARGET, 'url': URL})
        await page.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf8'))
        await scout.call('browser_navigate', {'url': URL})
        config = {key: BRANCH[key] for key in SOURCE_FIELDS}
        config['unit_selector'] = 'main li'
        good = await scout.call('browser_verify_source', config)
        assert json.loads(good['content'][0]['text'])['confirmed']
        async def broken(*_args, **_kwargs):
            raise RuntimeError('instrumented structure-tool failure')
        monkeypatch.setattr(qualification, 'verify_source', broken)
        error = await scout.call('browser_verify_source', config)
        receipt = json.loads(error['content'][0]['text'])
        assert error['isError'] and receipt['source_call_url'] == URL
        assert Path(receipt['original_path']).is_file() and Path(receipt['evidence_path']).is_file()
        assert source_event_proof(BRANCH, scout.events) is None
        assert source_event_issues(BRANCH, scout.events)[0]['failed_condition'] == 'source_verification_tool_failed'
        branch = {key: BRANCH[key] for key in (*SOURCE_FIELDS, 'url')}
        submitted = json.loads((await scout.call('submit_plan', {
            'plan': {**TARGET, 'branches': [branch]}}))['content'][0]['text'])
        assert not submitted['accepted']
        await browser.close()
