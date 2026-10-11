"""New registrations use their own proof payload, with standard capture DTOs.

The scout runs independent synthetic handlers without network. Qualification
events use the actual captured bytes and the production witness constructor;
this verifies the registration/transport/gate contract, not a live college.
"""
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

from bs4 import BeautifulSoup
import pytest

from server import agent_recon, page_types
from server.recon_browser_mcp import BrowserScout
from server.browser_batch_tool import single_page_evidence
from server.source_qualification import SOURCE_FIELDS, source_unit_selector, witness
from test_page_type_recon import (TARGET, CHILDREN, PARENT, all_events, binding,
                                  navigation, pair, plan, source_html)


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(page_types, '_REGISTRY', dict(page_types._REGISTRY))


def source_events(branch, html):
    branch = page_types.normalize_branch(branch)
    args = {key: branch[key] for key in SOURCE_FIELDS}
    args['unit_selector'] = source_unit_selector(branch)
    proof = witness(html, branch['url'], branch, TARGET)
    assert proof['role_supported']
    proof.update(confirmed=True, dom_stable=True, network_idle=True, witness_visible=True)
    return pair('browser_verify_source', args, proof, 'independent-source')


@pytest.mark.asyncio
async def test_registered_entrance_uses_actual_capture_urls_without_payload_source_urls(tmp_path, registry):
    html = source_html()
    calls = []

    async def handler(_old, context, branch):
        calls.append(branch['url'])
        return page_types.EntranceCapture(html, CHILDREN, [], {'url': PARENT, 'final_url': PARENT})

    def probe(context, result):
        return {'hash': context.source_html_sha256, 'anchor_count': len(result.source_urls)}

    def validator(context, result, payload):
        if not isinstance(result, page_types.EntranceCapture):
            return False
        nodes = BeautifulSoup(context.source_html, 'html.parser').select(context.branch['child_link_selector'])
        return (result.source_urls == [node['href'] for node in nodes]
                and payload == {'hash': context.source_html_sha256, 'anchor_count': len(nodes)})

    original = page_types.resolve_type(binding('entrance.navigation'), 'entrance')
    page_types.register_type(replace(original, type_id='entrance.independent_transport',
        implementation=handler, stage_probe=probe, evidence_validator=validator,
        evidence_contract='actual returned source URLs equal every selected retained anchor'))
    parent = navigation()
    events = all_events(parent)
    parent['page_types']['entrance'] = binding('entrance.independent_transport', child_link_selector='#nav > a')
    scout = BrowserScout(SimpleNamespace(context=object(), url=PARENT), tmp_path, 'branch', TARGET)
    arguments = {'role': 'entrance', 'branch': parent}
    reply = await scout.call('browser_verify_stage', arguments)
    packet = json.loads(reply['content'][0]['text'])
    assert packet['results'][0]['stage_evidence']['payload'] == {
        'hash': hashlib.sha256(html.encode()).hexdigest(), 'anchor_count': 2}
    assert calls == [PARENT]
    events += pair('browser_verify_stage', arguments, packet, 'independent-stage')
    assert not agent_recon.selector_evidence_issues(plan(parent), events)
    assert calls == [PARENT]  # Validation consumes originals, not another execution.


@pytest.mark.asyncio
async def test_registered_pagination_uses_actual_evidence_without_payload_end_or_clicks(tmp_path, registry):
    first, second = CHILDREN[0], CHILDREN[0] + '?page=2'
    initial = source_html() + f'<a class="next" href="{second}">Next</a>'
    final = source_html()
    captured = [(first, initial), (second, final)]
    calls = []

    async def handler(_old, context, branch, **kwargs):
        calls.append(branch['url'])
        return captured, [], {'url': first, 'end': 'next_control_absent', 'visited_pages': 2,
                             'independent_transition': 'href_to_exact_next_original'}

    def probe(context, result):
        return {'hash': context.source_html_sha256, 'page_count': len(result[0])}

    def validator(context, result, payload):
        if not isinstance(result, tuple) or len(result) != 3:
            return False
        pages, errors, evidence = result
        if len(pages) != 2 or errors or evidence.get('independent_transition') != 'href_to_exact_next_original':
            return False
        next_control = BeautifulSoup(pages[0][1], 'html.parser').select_one(context.branch['next_selector'])
        return (next_control is not None and next_control['href'] == pages[1][0]
                and not BeautifulSoup(pages[1][1], 'html.parser').select(context.branch['next_selector'])
                and payload == {'hash': context.source_html_sha256, 'page_count': 2})

    original = page_types.resolve_type(binding('pagination.next_control'), 'pagination')
    page_types.register_type(replace(original, type_id='pagination.independent_transport',
        implementation=handler, stage_probe=probe, evidence_validator=validator,
        evidence_contract='returned captures and independent transition prove exact next original'))
    branch = {'url': first, 'card_selector': '.cards > li', 'next_selector': 'a.next', 'max_pages': 3,
        'source_role': 'faculty_roster', 'source_container_selector': 'main',
        'role_evidence_selector': 'h1', 'source_context_selector': 'header',
        'page_types': {'pagination': binding('pagination.independent_transport')}}
    events = source_events(branch, initial) + pair('browser_verify',
        {'card_selector': '.cards > li', 'next_selector': 'a.next'},
        {'url': first, 'cardCount': 2, 'nextCount': 1, 'nextDisabled': False, 'nextUrl': second,
         'linkedSamples': []}, 'independent-selector')
    scout = BrowserScout(SimpleNamespace(context=object(), url=first), tmp_path, 'branch', TARGET)
    arguments = {'role': 'pagination', 'branch': branch}
    reply = await scout.call('browser_verify_stage', arguments)
    packet = json.loads(reply['content'][0]['text'])
    assert packet['results'][0]['stage_evidence']['payload'] == {
        'hash': hashlib.sha256(initial.encode()).hexdigest(), 'page_count': 2}
    assert calls == [first]
    events += pair('browser_verify_stage', arguments, packet, 'independent-stage')
    assert not agent_recon.selector_evidence_issues(plan(branch), events)
    assert calls == [first]


@pytest.mark.asyncio
async def test_large_registered_pagination_reply_preserves_complete_proof_and_latest_ref_fails_closed(
        tmp_path, registry):
    first = CHILDREN[0]
    captured = [(first if index == 0 else first + '?page=' + str(index + 1), source_html()
                 + (f'<a class="next" href="{first}?page={index + 2}">Next</a>'
                    if index < 19 else '')) for index in range(20)]
    calls = []

    async def handler(_old, context, branch, **kwargs):
        calls.append(branch['url'])
        final_url, final_html = captured[-1]
        return captured, [], {'url': first, 'end': 'next_control_absent', 'next_clicks': 19,
            'pagination_termination': single_page_evidence(final_html, final_url, branch['card_selector'])}

    original = page_types.resolve_type(binding('pagination.verified_next'), 'pagination')
    page_types.register_type(replace(original, type_id='pagination.independent_long_chain', implementation=handler))
    branch = {'url': first, 'card_selector': '.cards > li', 'next_selector': 'a.next', 'max_pages': 20,
        'source_role': 'faculty_roster', 'source_container_selector': 'main',
        'role_evidence_selector': 'h1', 'source_context_selector': 'header',
        'page_types': {'pagination': binding('pagination.independent_long_chain')}}
    events = source_events(branch, captured[0][1]) + pair('browser_verify',
        {'card_selector': '.cards > li', 'next_selector': 'a.next'},
        {'url': first, 'cardCount': 2, 'nextCount': 1, 'nextDisabled': False,
         'nextUrl': captured[1][0], 'linkedSamples': []}, 'independent-selector')
    scout = BrowserScout(SimpleNamespace(context=object(), url=first), tmp_path, 'branch', TARGET)
    arguments = {'role': 'pagination', 'branch': branch}
    reply = await scout.call('browser_verify_stage', arguments)
    packet = json.loads(reply['content'][0]['text'])
    assert len(reply['content'][0]['text'].encode()) < 8000
    assert 'full_stage_result' in packet
    full = agent_recon._read_stage_packet(packet, 'pagination')
    assert len(full['results'][0]['originals']) == 20
    assert len(full['results'][0]['stage_evidence']['payload']['pages']) == 20
    events += pair('browser_verify_stage', arguments, packet, 'independent-large-stage')
    assert not agent_recon.selector_evidence_issues(plan(branch), events)
    assert calls == [first]

    bad = {**packet, 'full_stage_result': {**packet['full_stage_result'], 'sha256': '0' * 64}}
    events += pair('browser_verify_stage', arguments, bad, 'independent-invalid-latest-ref')
    assert agent_recon.selector_evidence_issues(plan(branch), events)
    assert calls == [first]
