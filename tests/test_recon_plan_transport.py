"""Encoded configurations have identical gates; malformed plans explain the repair."""
import json
from pathlib import Path

import pytest

from server.recon_contract import (
    SubmissionPlanInputError, plan_digest, submission_plan, submitted_plan, validate_submission,
)
from server.recon_browser_mcp import BrowserScout, phase_tools


def test_real_encoded_discovery_configuration_is_not_source_authority():
    args = json.loads(Path('tests/fixtures/recon_receipts/repair4_encoded_discovery_arguments.json').read_bytes())
    obj = submission_plan(args['plan'])
    assert plan_digest(args['plan']) == plan_digest(obj)
    target = {'school': obj['school'], 'college': obj['college'], 'url': 'https://www.fjsmu.edu.cn/smxyszb/'}
    rejected = validate_submission(args['plan'], 'discovery', target, [])
    assert rejected and not any(issue['status'] == 'schema_model_type' for issue in rejected)
    assert any(issue['status'] == 'source_role_evidence_unverified' for issue in rejected)


@pytest.mark.parametrize('value,condition', [
    (None, 'plan_object_missing'), ([], 'plan_object_missing'),
    ('[]', 'plan_json_not_object'), ('"{}"', 'plan_json_not_object'),
    ('{"url":"a","url":"b"}', 'plan_json_duplicate_key'),
    ('{"nested":{"url":"a","url":"b"}}', 'plan_json_duplicate_key'),
    ('{"max_pages":NaN}', 'plan_json_nonfinite'), ('{"max_pages":1e9999}', 'plan_json_nonfinite'),
    ('{"college":"\\ud800"}', 'plan_json_invalid_unicode'), ('\ud800', 'plan_json_invalid_unicode'),
    ('{} trailing', 'plan_json_invalid'),
    ('[' * 1200, 'plan_json_invalid'), ('x' * 1048577, 'plan_json_too_large'),
], ids=['missing', 'array-input', 'array-json', 'double-encoded', 'duplicate-key',
        'nested-duplicate-key', 'nonfinite', 'float-overflow', 'escaped-surrogate', 'raw-surrogate',
        'malformed', 'deep-json', 'oversized'])
def test_transport_errors_are_controlled_and_do_not_grant_a_plan(value, condition):
    with pytest.raises(SubmissionPlanInputError, match=condition):
        submission_plan(value)
    issues = validate_submission(value, 'branch', {}, [])
    assert issues[0]['failed_condition'] == condition
    assert 'arguments.plan' in issues[0]['need']
    assert len(plan_digest(value)) == 64


def test_discovery_submit_instructions_do_not_request_an_unavailable_catalog():
    tools = phase_tools('discovery')
    submit = next(item for item in tools if item['name'] == 'submit_plan')
    assert 'page_type_catalog' not in {item['name'] for item in tools}
    assert 'later branch phase' in submit['description']
    assert 'Query page_type_catalog' not in submit['description']


@pytest.mark.asyncio
async def test_equivalent_transport_and_whitespace_do_not_reset_rejected_plan(tmp_path):
    scout = BrowserScout(None, tmp_path, 'branch', {'school': '样本大学', 'college': '样本学院',
                                                'url': 'https://example.edu/faculty'})
    obj = {'url': 'https://example.edu/faculty', 'card_selector': 'main li', 'single_page': True}
    first = await scout.call('submit_plan', {'plan': obj})
    second = await scout.call('submit_plan', {'plan': json.dumps(obj, indent=4)})
    assert first['isError'] and second['isError']
    repeated = json.loads(second['content'][0]['text'])
    assert repeated['unchanged_resubmission'] == 1
    third = await scout.call('submit_plan', {'plan': json.dumps(obj, separators=(',', ':'))})
    assert json.loads(third['content'][0]['text'])['stop'] == 'recon_gate_stalled'


@pytest.mark.browser
@pytest.mark.asyncio
async def test_actual_encoded_source_submission_receipt_recovers_the_dictionary(tmp_path):
    from playwright.async_api import async_playwright
    url = 'https://fixture.example.edu/faculty'
    branch = {'url': url, 'source_role': 'faculty_roster', 'source_container_selector': 'main',
              'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
    plan = {'school': '样本大学', 'college': '样本学院', 'branches': [branch]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context()
            await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf-8'))
            await context.route('**/*', lambda route: route.fulfill(content_type='text/html; charset=utf-8',
                body='<title>师资队伍</title><h1>师资队伍</h1><header>样本大学样本学院</header><main>官方名单</main>'))
            scout = BrowserScout(await context.new_page(), tmp_path, 'discovery', {**plan, 'url': url})
            await scout.call('browser_navigate', {'url': url})
            await scout.call('browser_verify_source', {key: value for key, value in branch.items() if key != 'url'})
            encoded = json.dumps(plan, ensure_ascii=False, indent=2)
            accepted = json.loads((await scout.call('submit_plan', {'plan': encoded}))['content'][0]['text'])
            assert accepted == {'accepted': True, 'digest': plan_digest(plan)}
            canonical = json.loads(next(tmp_path.glob('*.accepted.json')).read_bytes())
            assert canonical == plan
            events = [*scout.events,
                {'type': 'tool/call', 'data': {'name': 'mcp__playwright__submit_plan', 'callId': 'encoded',
                                             'arguments': json.dumps({'plan': encoded})}},
                {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': 'encoded',
                    'isError': False, 'content': [{'type': 'text', 'text': json.dumps(accepted)}]}]}}}]
            assert submitted_plan(events) == plan
            events[-1]['data']['message']['content'][0]['isError'] = True
            assert submitted_plan(events) is None
        finally:
            await browser.close()
