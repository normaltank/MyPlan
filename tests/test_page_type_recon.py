"""Real recon validators consume controlled offline browser events and bindings."""
import copy
import json

import pytest

from server import agent_recon
from server.page_types import normalize_branch
from server.recon_contract import Branch, validate_submission
from server.source_qualification import SOURCE_FIELDS, source_unit_selector, witness

TARGET = {'school': '样本大学', 'college': '样本学院', 'url': 'https://example.edu/'}
CLAIM = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
         'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
PARENT = 'https://example.edu/departments'
CHILDREN = ['https://example.edu/cards', 'https://example.edu/article']


def pair(name, args, result, cid):
    return [
        {'type': 'tool/call', 'data': {'callId': cid, 'name': 'mcp__playwright__' + name,
                                     'arguments': json.dumps(args)}},
        {'type': 'tool/result', 'data': {'message': {'content': [
            {'toolCallId': cid, 'content': [{'type': 'text', 'text': json.dumps(result)}]}]}}},
    ]


def binding(type_id, **params):
    return {'type_id': type_id, 'implementation_version': 1, 'params': params}


def branch(url=CHILDREN[0]):
    return {**CLAIM, 'url': url, 'page_types': {
        'roster': binding('roster.cards', card_selector='.cards > li'),
        'pagination': binding('pagination.single_page', single_page=True)}}


def navigation():
    return {**CLAIM, 'url': PARENT, 'kind': 'navigation', 'page_types': {
        'entrance': binding('entrance.navigation', child_link_selector='#nav > a'),
        'pagination': binding('pagination.single_page', single_page=True)},
        'child_branches': [branch(), {**CLAIM, 'url': CHILDREN[1], 'kind': 'segmented', 'page_types': {
            'roster': binding('roster.segmented', container_selector='article', record_start_selector='h2'),
            'pagination': binding('pagination.single_page', single_page=True)}}]}


def plan(value):
    return {'school': TARGET['school'], 'college': TARGET['college'], 'branches': [value]}


def source_html():
    links = ''.join(f'<a href="{url}">Department {index}</a>' for index, url in enumerate(CHILDREN))
    return (f'<html><head><title>教师名单</title></head><body><header>{TARGET["school"]}{TARGET["college"]}</header>'
            f'<h1>教师名单</h1><main><div id="nav">{links}</div>'
            '<ul class="cards"><li>王芳</li><li>李华</li></ul>'
            '<article><h2>王芳</h2><p>原始简介一</p><h2>李华</h2><p>原始简介二</p></article>'
            '</main></body></html>')


def source_events(value, cid='source'):
    value = normalize_branch(value)
    args = {key: value[key] for key in SOURCE_FIELDS}
    if unit := source_unit_selector(value):
        args['unit_selector'] = unit
    proof = witness(source_html(), value['url'], value, TARGET)
    assert proof['role_supported']
    proof.update(confirmed=True, dom_stable=True, network_idle=True, witness_visible=True)
    return pair('browser_verify_source', args, proof, cid), proof


def selector_events(value, cid='selector', *, urls=None, include_urls=True):
    value = normalize_branch(value)
    source, proof = source_events(value, cid+'-source')
    if value.get('kind') == 'segmented':
        args = {key: value[key] for key in ('container_selector', 'record_start_selector')}
        result = {**args, 'url': value['url'], 'structure_ok': True, 'segment_count': 2}
        tool = 'browser_verify_segments'
        selector = value['container_selector']
    else:
        selector = value.get('card_selector') or value['child_link_selector']
        args = {'card_selector': selector}
        result = {'url': value['url'], 'cardCount': 2, 'linkedSamples': []}
        tool = 'browser_verify'
    result['pagination_termination'] = {'url': value['url'], 'unit_selector': selector,
        'confirmed': True, 'source_sha256': proof['source_html_sha256'],
        'unresolved_forward_controls': 0, 'basis': 'rendered_forward_controls'}
    if value.get('kind') == 'navigation':
        result['linkedSamples'] = CHILDREN[:2]
        if include_urls:
            args['include_child_urls'] = True
            result['child_urls'] = CHILDREN if urls is None else urls
            result['child_url_evidence'] = {'url': value['url'], 'selector': selector,
                'source_sha256': proof['source_html_sha256'], 'complete': True,
                'discovery_scope': 'current_rendered_navigation_links',
                'invalid_links': 0, 'selected_count': 2}
    return source + pair(tool, args, result, cid)


def all_events(value):
    return selector_events(value, 'parent') + [event for index, child in enumerate(value['child_branches'])
        for event in selector_events(child, f'child-{index}')]


def result_in(events, cid):
    return json.loads(next(event['data']['message']['content'][0]['content'][0]['text']
        for event in events if event['type'] == 'tool/result'
        and event['data']['message']['content'][0]['toolCallId'] == cid))


def replace_result(events, cid, result):
    for event in events:
        if event['type'] == 'tool/result' and event['data']['message']['content'][0]['toolCallId'] == cid:
            event['data']['message']['content'][0]['content'][0]['text'] = json.dumps(result)


def test_params_are_materialized_for_actual_schema_hygiene_and_selector_gate():
    value = branch()
    before = copy.deepcopy(value)
    Branch.model_validate(value)
    agent_recon.validate_model_plan(plan(value), school=TARGET['school'], college=TARGET['college'])
    assert not validate_submission(value, 'branch', {**TARGET, 'url': value['url']}, selector_events(value))
    assert value == before and 'card_selector' not in value


@pytest.mark.parametrize('reference', [
    binding('roster.nonexistent'),
    {'type_id': 'roster.cards', 'implementation_version': 999, 'params': {}},
    binding('detail.profile'),
    binding('roster.cards', teacher_name='王芳'),
    binding('roster.cards', card_selector='li[data-name="王芳"]'),
    binding('roster.cards', card_selector='li[data-email="x@example.edu"]'),
])
def test_binding_metadata_cannot_hide_data_or_unknown_code(reference):
    value = branch()
    value['page_types']['roster'] = reference
    with pytest.raises(agent_recon.PlanHygieneError):
        agent_recon.validate_model_plan(plan(value), school=TARGET['school'], college=TARGET['college'])
    assert agent_recon.selector_evidence_issues(plan(value), [])


def test_each_heterogeneous_child_has_its_own_source_and_selector_proof():
    value = navigation()
    assert not validate_submission(value, 'branch', {**TARGET, 'url': PARENT}, all_events(value))
    partial = selector_events(value, 'parent') + selector_events(value['child_branches'][0], 'child-0')
    issues = agent_recon.selector_evidence_issues(plan(value), partial)
    assert any(issue['url'] == CHILDREN[1] and issue['status'] == 'segments_not_verified' for issue in issues)
    assert any(issue['url'] == CHILDREN[1] and issue['status'] == 'source_role_evidence_unverified' for issue in issues)


@pytest.mark.parametrize('observed', [CHILDREN[:1], [*CHILDREN, 'https://example.edu/omitted']])
def test_missing_or_extra_current_child_urls_cannot_be_silently_dropped(observed):
    value = navigation()
    events = selector_events(value, 'parent', urls=observed) + [event
        for index, child in enumerate(value['child_branches']) for event in selector_events(child, f'child-{index}')]
    issues = agent_recon.selector_evidence_issues(plan(value), events)
    assert any(issue.get('failed_condition') == 'explicit_child_set_unverified' for issue in issues)


@pytest.mark.parametrize('damage', ['samples_only', 'wrong_hash', 'role_hash', 'not_complete', 'forward_control'])
def test_samples_or_stale_or_incomplete_parent_state_do_not_prove_all_children(damage):
    value = navigation()
    events = all_events(value)
    result = result_in(events, 'parent')
    if damage == 'samples_only':
        result.pop('child_url_evidence')
    elif damage == 'wrong_hash':
        result['child_url_evidence']['source_sha256'] = 'a'*64
    elif damage == 'role_hash':
        result['pagination_termination']['source_sha256'] = 'a'*64
        result['child_url_evidence']['source_sha256'] = 'a'*64
    elif damage == 'not_complete':
        result['child_url_evidence']['complete'] = False
    else:
        result['pagination_termination'].update(confirmed=False, unresolved_forward_controls=1)
    replace_result(events, 'parent', result)
    assert agent_recon.selector_evidence_issues(plan(value), events)


def test_changed_type_selector_cannot_reuse_earlier_child_proof():
    value = navigation()
    events = all_events(value)
    value['child_branches'][0]['page_types']['roster']['params']['card_selector'] = '.changed > li'
    issues = agent_recon.selector_evidence_issues(plan(value), events)
    assert any(issue.get('url') == CHILDREN[0] and issue['status'] == 'selector_not_verified' for issue in issues)
    assert any(issue.get('url') == CHILDREN[0] and issue['status'] == 'source_role_evidence_unverified' for issue in issues)


def test_parent_personal_introductions_still_conflict_with_navigation_type():
    value = navigation()
    events = all_events(value)
    result = result_in(events, 'parent')
    result['profile_intro_link_count'] = 1
    replace_result(events, 'parent', result)
    assert any(issue.get('failed_condition') == 'navigation_links_are_profiles'
        for issue in agent_recon.selector_evidence_issues(plan(value), events))


def test_navigation_links_do_not_need_to_be_person_cards_but_rosters_still_do():
    value = navigation()
    events = all_events(value)
    result = result_in(events, 'parent')
    result['card_mismatch'] = {'visible_cards': 2, 'extracted_candidates': 0}
    replace_result(events, 'parent', result)
    assert not agent_recon.selector_evidence_issues(plan(value), events)
    child_result = result_in(events, 'child-0')
    child_result['card_mismatch'] = {'visible_cards': 2, 'extracted_candidates': 1}
    replace_result(events, 'child-0', child_result)
    assert any(issue.get('url') == CHILDREN[0] and issue['status'] == 'roster_card_mismatch'
        for issue in agent_recon.selector_evidence_issues(plan(value), events))


def test_explicit_navigation_next_control_is_not_applicable_but_uniform_model_stays_compatible():
    value = navigation()
    value['page_types']['pagination'] = binding('pagination.next_control', next_selector='a.next')
    with pytest.raises(agent_recon.PlanHygieneError, match='explicit navigation pagination is not applicable'):
        agent_recon.validate_model_plan(plan(value), school=TARGET['school'], college=TARGET['college'])
    assert any(issue.get('failed_condition') == 'explicit_navigation_pagination_not_applicable'
        for issue in agent_recon.selector_evidence_issues(plan(value), []))
    uniform = copy.deepcopy(value)
    uniform.pop('child_branches')
    uniform['child_branch'] = {**CLAIM, 'card_selector': '.cards > li', 'single_page': True}
    agent_recon.validate_model_plan(plan(uniform), school=TARGET['school'], college=TARGET['college'])


def test_explicit_navigation_custom_pagination_without_next_is_not_applicable(monkeypatch):
    from dataclasses import replace
    from server import page_types
    original = page_types.resolve_type(binding('pagination.single_page'), 'pagination')
    monkeypatch.setattr(page_types, '_REGISTRY', dict(page_types._REGISTRY))
    page_types.register_type(replace(original, type_id='pagination.test_parent'))
    value = navigation()
    value['page_types']['pagination'] = binding('pagination.test_parent', single_page=True)
    # The production role guard rejects this registered custom mechanism even
    # before the current-rendered explicit-navigation check.
    with pytest.raises(agent_recon.PlanHygieneError, match='page type evidence contract is not supported'):
        agent_recon.validate_model_plan(plan(value), school=TARGET['school'], college=TARGET['college'])
    assert any(issue.get('failed_condition') == 'explicit_navigation_pagination_not_applicable'
        for issue in agent_recon.selector_evidence_issues(plan(value), []))


@pytest.mark.asyncio
async def test_type_catalog_is_discoverable_without_browser_and_returns_trusted_contracts(tmp_path):
    from server.page_types import catalog
    from server.recon_browser_mcp import BrowserScout, phase_tools
    scout = BrowserScout(None, tmp_path)
    for phase in ('branch', 'full'):
        assert 'page_type_catalog' in {item['name'] for item in phase_tools(phase)}
    entries, offset = [], 0
    while True:
        reply = await scout.call('page_type_catalog', {'role': 'roster', 'offset': offset, 'limit': 1})
        result = json.loads(reply['content'][0]['text'])
        entries.extend(result['items'])
        if result['nextOffset'] is None:
            break
        assert result['nextOffset'] > offset
        offset = result['nextOffset']
    assert entries == [item for item in catalog() if item['role'] == 'roster']
    rank_grid = next(item for item in entries if item['type_id'] == 'roster.rank_grid')
    assert set(rank_grid) == {'role', 'type_id', 'implementation_version', 'params_schema',
        'input_contract', 'output_contract', 'failure_contract', 'applicability_contract'}
    assert rank_grid['params_schema']['additionalProperties'] is False
    assert 'rank heading' in rank_grid['applicability_contract']


def test_registered_cards_version_cannot_reuse_legacy_browser_proof(monkeypatch):
    from dataclasses import replace
    from server import page_types
    value = branch()
    events = selector_events(value)
    target = {**TARGET, 'url': value['url']}
    assert not validate_submission(value, 'branch', target, events)
    original = page_types.resolve_type(binding('roster.cards'), 'roster')
    monkeypatch.setattr(page_types, '_REGISTRY', dict(page_types._REGISTRY))
    page_types.register_type(replace(original, implementation_version=2, implementation=lambda *_a, **_kw: None))
    value['page_types']['roster']['implementation_version'] = 2
    assert any(issue['status'] == 'roster_type_evidence_unverified'
        for issue in validate_submission(value, 'branch', target, events))


@pytest.mark.parametrize('role,type_id', [('entrance', 'entrance.direct'),
    ('pagination', 'pagination.single_page'), ('detail', 'detail.profile')])
def test_other_production_roles_reject_registered_unproved_new_version(monkeypatch, role, type_id):
    from dataclasses import replace
    from server import page_types
    value = branch()
    events = selector_events(value)
    target = {**TARGET, 'url': value['url']}
    assert not validate_submission(value, 'branch', target, events)
    original = page_types.resolve_type(binding(type_id), role)
    monkeypatch.setattr(page_types, '_REGISTRY', dict(page_types._REGISTRY))
    page_types.register_type(replace(original, implementation_version=2))
    value['page_types'][role] = {**binding(type_id), 'implementation_version': 2}
    assert validate_submission(value, 'branch', target, events)


@pytest.mark.parametrize('mechanism', ['segmented', 'image_table', 'empty'])
def test_specialized_legacy_proofs_fail_closed_for_registered_new_version(monkeypatch, mechanism):
    from dataclasses import replace
    from server import page_types
    if mechanism == 'segmented':
        value = navigation()['child_branches'][1]
        type_id = 'roster.segmented'
    elif mechanism == 'image_table':
        type_id = 'roster.image_table'
        value = {**CLAIM, 'url': CHILDREN[0], 'kind': 'image_table', 'single_page': True,
                 'page_types': {'roster': binding(type_id, card_selector='main > img')}}
    else:
        type_id = 'roster.cards'
        value = branch()
        value.update(empty_state_selector='.empty', roster_container_selector='main')
    old_issues = agent_recon.selector_evidence_issues(plan(value), [])
    assert not any(issue.get('failed_condition') == 'specialized_roster_version_not_applicable'
        for issue in old_issues)
    agent_recon.validate_model_plan(plan(value), school=TARGET['school'], college=TARGET['college'])
    original = page_types.resolve_type(binding(type_id), 'roster')
    monkeypatch.setattr(page_types, '_REGISTRY', dict(page_types._REGISTRY))
    page_types.register_type(replace(original, implementation_version=2))
    value['page_types']['roster']['implementation_version'] = 2
    assert any(issue.get('failed_condition') == 'specialized_roster_version_not_applicable'
        for issue in agent_recon.selector_evidence_issues(plan(value), []))
    with pytest.raises(agent_recon.PlanHygieneError, match='page type evidence contract is not supported'):
        agent_recon.validate_model_plan(plan(value), school=TARGET['school'], college=TARGET['college'])
    assert validate_submission(value, 'branch', {**TARGET, 'url': value['url']}, []) == [
        {'path': '$', 'status': 'structure_contract_rejected'}]


@pytest.mark.browser
@pytest.mark.asyncio
async def test_registered_cards_new_version_browser_proof_dispatches_and_binds_exact_version(monkeypatch, tmp_path):
    from dataclasses import replace
    from pathlib import Path
    from playwright.async_api import async_playwright
    from server import page_types
    from server.recon_browser_mcp import BrowserScout
    original = page_types.resolve_type(binding('roster.cards'), 'roster')
    invoked = []
    def typed_cards(fallback, captured, plan, **kwargs):
        invoked.append(True)
        result = fallback(captured, plan, **kwargs)
        for observation in result.observations:
            observation.update(listed_items=observation['visible_cards'],
                               awaiting_review=0, reviewed_exclusions=0)
        return result
    monkeypatch.setattr(page_types, '_REGISTRY', dict(page_types._REGISTRY))
    page_types.register_type(replace(original, implementation_version=2, implementation=typed_cards))
    value = branch()
    value['page_types']['roster']['implementation_version'] = 2
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(service_workers='block')
        await context.add_init_script(Path('server/recon_dom_probe.js').read_text())
        await context.route('**/*', lambda route: route.fulfill(status=200,
            content_type='text/html; charset=utf-8', body=source_html()))
        try:
            scout = BrowserScout(await context.new_page(), tmp_path, 'branch', TARGET)
            await scout.call('browser_navigate', {'url': value['url']})
            await scout.call('browser_verify_source', {**CLAIM, 'unit_selector': '.cards > li'})
            await scout.call('browser_verify', {'card_selector': '.cards > li'})
            assert any(issue['status'] == 'roster_type_evidence_unverified'
                for issue in validate_submission(value, 'branch', {**TARGET, 'url': value['url']}, scout.events))
            assert not invoked
            reply = await scout.call('browser_verify', {'card_selector': '.cards > li',
                'page_types': {'roster': value['page_types']['roster']}})
            result = json.loads(reply['content'][0]['text'])
            assert invoked and 'card_mismatch' not in result
            proof = result['roster_type_evidence']
            assert proof['type_id'] == 'roster.cards' and proof['implementation_version'] == 2
            assert proof['reading'] == {'listed_items': 2, 'extracted_candidates': 2,
                'awaiting_review': 0, 'reviewed_exclusions': 0, 'issues': []}
            assert not validate_submission(value, 'branch', {**TARGET, 'url': value['url']}, scout.events)
            # Matching selectors/source cannot erase an implementation-version
            # mismatch in either the actual tool arguments or its result.
            last_call = scout.events[-2]['data']
            last_text = scout.events[-1]['data']['message']['content'][0]['content'][0]
            saved_args, saved_text = last_call['arguments'], last_text['text']
            changed = json.loads(saved_args)
            changed['page_types']['roster']['implementation_version'] = 1
            last_call['arguments'] = json.dumps(changed)
            assert agent_recon.selector_evidence_issues(plan(value), scout.events)
            last_call['arguments'] = saved_args
            changed = json.loads(saved_text)
            changed['roster_type_evidence']['implementation_version'] = 1
            last_text['text'] = json.dumps(changed)
            assert agent_recon.selector_evidence_issues(plan(value), scout.events)
        finally:
            await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_browser_probe_binds_complete_current_links_and_retains_old_samples(tmp_path):
    from pathlib import Path
    from playwright.async_api import async_playwright
    from server.recon_browser_mcp import BrowserScout
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(service_workers='block')
        await context.add_init_script(Path('server/recon_dom_probe.js').read_text())
        await context.route('**/*', lambda route: route.fulfill(status=200,
            content_type='text/html; charset=utf-8', body=source_html()))
        try:
            value = navigation()
            scout = BrowserScout(await context.new_page(), tmp_path, 'branch', TARGET)
            await scout.call('browser_navigate', {'url': PARENT})
            normalized = normalize_branch(value)
            await scout.call('browser_verify_source', {**{key: normalized[key] for key in SOURCE_FIELDS},
                'unit_selector': normalized['child_link_selector']})
            reply = await scout.call('browser_verify', {'card_selector': '#nav > a', 'include_child_urls': True})
            result = json.loads(reply['content'][0]['text'])
            assert result['child_urls'] == CHILDREN
            assert result['linkedSamples'] == CHILDREN[:2]
            proof = result['child_url_evidence']
            assert proof['complete'] and proof['discovery_scope'] == 'current_rendered_navigation_links'
            assert proof['source_sha256'] == result['pagination_termination']['source_sha256']
            # Actual current parent proof, synthetic child proofs: no source gate is mocked.
            events = scout.events + [event for index, child in enumerate(value['child_branches'])
                                     for event in selector_events(child, f'child-{index}')]
            assert not agent_recon.selector_evidence_issues(plan(value), events)
            # The real DOM's whole URL set, rather than submit schema or its
            # first two samples, decides whether every child is bound.
            missing = copy.deepcopy(value)
            missing['child_branches'].pop()
            extra = copy.deepcopy(value)
            extra['child_branches'].append(branch('https://example.edu/undeclared'))
            for changed in (missing, extra):
                assert any(issue.get('failed_condition') == 'explicit_child_set_unverified'
                    for issue in agent_recon.selector_evidence_issues(plan(changed), events))
        finally:
            await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['clean', 'unknown', 'rare', 'duplicate'])
async def test_rank_grid_browser_proof_uses_registered_parser_and_token_dispositions(tmp_path, case):
    from pathlib import Path
    from playwright.async_api import async_playwright
    from server.recon_browser_mcp import BrowserScout
    value = {**CLAIM, 'url': CHILDREN[0], 'page_types': {
        'roster': binding('roster.rank_grid', container_selector='.rankgroup',
                          record_start_selector='h2', card_selector='.names'),
        'pagination': binding('pagination.single_page', single_page=True)}}
    extra = {'clean': '', 'unknown': '、TBD', 'rare': '、巢明', 'duplicate': '、王芳'}[case]
    html = (f'<html><title>教师名单</title><body><header>{TARGET["school"]}{TARGET["college"]}</header>'
            f'<h1>教师名单</h1><main><div class="rankgroup"><h2>教授</h2><p class="names">王芳、李华{extra}</p></div>'
            '<div class="rankgroup"><h2>副教授</h2><p class="names">张伟、赵强</p></div></main></body></html>')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(service_workers='block')
        await context.add_init_script(Path('server/recon_dom_probe.js').read_text())
        await context.route('**/*', lambda route: route.fulfill(status=200,
            content_type='text/html; charset=utf-8', body=html))
        try:
            scout = BrowserScout(await context.new_page(), tmp_path, 'branch', {**TARGET, 'url': value['url']})
            await scout.call('browser_navigate', {'url': value['url']})
            await scout.call('browser_verify_source', {**CLAIM, 'unit_selector': '.names'})
            reply = await scout.call('browser_verify', {**value['page_types']['roster']['params'],
                'page_types': {'roster': value['page_types']['roster']}})
            result = json.loads(reply['content'][0]['text'])
            assert result['cardCount'] == 2
            evidence = result['roster_type_evidence']
            assert evidence['type_id'] == 'roster.rank_grid'
            assert evidence['reading']['listed_items'] == (4 if case == 'clean' else 5)
            assert evidence['reading']['extracted_candidates'] == (5 if case == 'duplicate' else 4)
            assert '王芳' not in json.dumps(result, ensure_ascii=False)
            assert 'TBD' not in json.dumps(result, ensure_ascii=False)
            issues = agent_recon.selector_evidence_issues(plan(value), scout.events)
            if case in {'unknown', 'duplicate'}:
                assert evidence['reading']['issues']
                assert any(issue['status'] == 'roster_type_evidence_unverified' for issue in issues)
            else:
                if case == 'rare':
                    assert evidence['reading']['issues'] == ['rare_surname_needs_review']
                    assert evidence['reading']['awaiting_review'] == 1
                else:
                    assert evidence['reading']['issues'] == []
                assert not issues
                assert not validate_submission(value, 'branch', {**TARGET, 'url': value['url']}, scout.events)
                if case == 'rare':
                    # A pending-name code is not permission to hide other
                    # structural failures or to silently auto-admit the name.
                    typed_event = next(event for event in scout.events if event['type'] == 'tool/result'
                        and 'roster_type_evidence' in event['data']['message']['content'][0]['content'][0]['text'])
                    block = typed_event['data']['message']['content'][0]['content'][0]
                    original = block['text']
                    for hidden_failure in ('zero_pending', 'ambiguous_identity'):
                        changed = json.loads(original)
                        reading = changed['roster_type_evidence']['reading']
                        if hidden_failure == 'zero_pending':
                            reading.update(awaiting_review=0, extracted_candidates=5)
                        else:
                            reading['issues'].append('rank_grid_identity_ambiguous')
                        block['text'] = json.dumps(changed)
                        assert any(issue['status'] == 'roster_type_evidence_unverified'
                            for issue in agent_recon.selector_evidence_issues(plan(value), scout.events))
                    block['text'] = original
                # Old cards evidence cannot authorize the new mechanism, even
                # when it was collected on the same current document.
                for event in scout.events:
                    if event['type'] == 'tool/result':
                        block = event['data']['message']['content'][0]
                        observed = json.loads(block['content'][0]['text'])
                        observed.pop('roster_type_evidence', None)
                        block['content'][0]['text'] = json.dumps(observed)
                assert any(issue['status'] == 'roster_type_evidence_unverified'
                    for issue in agent_recon.selector_evidence_issues(plan(value), scout.events))
        finally:
            await browser.close()
