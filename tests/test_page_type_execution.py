"""Offline execution of registered mechanisms and exact explicit child bindings."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from server import browser_batch_tool as batch, page_types as types
from server.harvest_workflow import (CapturedRosterPage, RosterReconciliation, capture_profiles,
                                     capture_sources, reconcile_rosters, roster_card_reading)
from test_harvest_acceptance import CASES, ReplayContext

pytestmark = pytest.mark.verified_source_boundary


@pytest.fixture
def registered_counter(monkeypatch):
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))
    counts = Counter()
    refs = {}
    for role, original in (('entrance', 'entrance.navigation'), ('roster', 'roster.cards'),
                           ('pagination', 'pagination.single_page'), ('detail', 'detail.profile')):
        definition = types._REGISTRY[(original, 1)]
        def implementation(operation, *args, _role=role, **kwargs):
            counts[_role] += 1
            return operation(*args, **kwargs)
        type_id = 'execution.roster' if role == 'roster' else original
        instrumented = replace(definition, type_id=type_id, implementation=implementation)
        if role == 'roster':
            types.register_type(instrumented)
        else:
            types._REGISTRY[(original, 1)] = instrumented
        refs[role] = {'type_id': type_id, 'implementation_version': 1, 'params': {}}
    return refs, counts


def mixed_plan(refs):
    case = deepcopy(next(case for case in CASES if case['id'] == 'ordinary_full_biography'))
    cards = case['plan']['branches'][0]
    root = 'https://acceptance.example.edu/departments'
    segmented = {'url': 'https://acceptance.example.edu/segmented', 'kind': 'segmented',
        'single_page': True, 'container_selector': '#people', 'record_start_selector': '#people h2',
        'source_role': 'faculty_roster', 'source_container_selector': '#people',
        'role_evidence_selector': 'h1', 'source_context_selector': 'header',
        'page_types': {'pagination': refs['pagination']}}
    cards.update(source_role='faculty_roster', source_container_selector='main',
                 role_evidence_selector='h1', source_context_selector='header',
                 page_types={role: refs[role] for role in ('roster', 'pagination', 'detail')})
    navigation = {'url': root, 'kind': 'navigation', 'child_link_selector': 'nav a',
        'source_role': 'faculty_roster', 'source_container_selector': 'nav',
        'role_evidence_selector': 'h1', 'source_context_selector': 'header',
        'child_branches': [cards, segmented], 'page_types': {'entrance': refs['entrance']}}
    plan = {**case['plan'], 'branches': [navigation]}
    pages = case['pages']
    pages[root] = [{'url': root, 'html': '<nav><a href="/roster">Branch A</a>'
                    '<a href="/segmented">Branch B</a></nav>'}]
    pages[segmented['url']] = [{'url': segmented['url'], 'html':
        '<h1>师资名单</h1><header>Review University Review College</header><div id="people">'
        '<h2>刘敏</h2><p>职称：讲师</p><p>研究方向：高分子。</p></div>'}]
    return plan, pages


@pytest.mark.asyncio
async def test_mixed_children_execute_all_registered_roles_and_preserve_fields(tmp_path, registered_counter):
    refs, counts = registered_counter
    plan, pages = mixed_plan(refs)
    browser = ReplayContext({url: states for url, states in pages.items() if 'file' in states[0]})
    browser.pages.update({url: states for url, states in pages.items() if 'html' in states[0]})
    report = await batch.run_plan(plan, browser, tmp_path)
    assert report['delivery_ready'] is True and report['unresolved'] == []
    assert report['coverage']['complete'] is True
    records = json.loads(open(report['records_path'], encoding='utf8').read())
    assert len(records) == 4
    assert next(row for row in records if row['姓名'] == '刘敏')['简介'] == '刘敏\n职称：讲师\n研究方向：高分子。'
    assert counts == {'entrance': 1, 'roster': 1, 'pagination': 2, 'detail': 3}
    assert report['branches'][0]['discovery_scope'] == 'rendered_navigation_state'
    assert len(report['branches'][0]['source_html_sha256']) == 64
    assert report['branches'][0]['page_types']['entrance'] == refs['entrance'] | {
        'params': {'child_link_selector': 'nav a'}}
    assert report['branches'][0]['child_visits'][0]['page_types']['roster']['type_id'] == refs['roster']['type_id']
    assert report['branches'][0]['child_visits'][1]['page_types']['roster']['type_id'] == 'roster.segmented'
    assert report['branches'][0]['executed_page_type_roles'] == ['entrance']
    assert report['branches'][0]['child_visits'][0]['executed_page_type_roles'] == ['entrance', 'pagination']
    assert all(item['executed_page_type_roles'] == ['detail'] for item in report['profile_page_type_evidence'].values())


@pytest.mark.asyncio
@pytest.mark.parametrize('problem', ['missing', 'extra', 'duplicate', 'drift'])
async def test_explicit_children_never_fall_back_to_a_default(problem):
    root, a, b = ['https://example.edu/' + part for part in ('entry', 'a', 'b')]
    children = [{'url': a, 'single_page': True, 'card_selector': 'li'}]
    discovered = [a]
    if problem == 'missing':
        discovered.append(b)
    elif problem == 'extra':
        children.append({'url': b, 'single_page': True, 'card_selector': 'li'})
    elif problem == 'duplicate':
        children.append(deepcopy(children[0]))
    else:
        discovered = [b]
    branch = {'url': root, 'kind': 'navigation', 'child_branches': children,
              'child_link_selector': 'nav a'}
    visited = []
    async def discover(_context, branch):
        return '<nav></nav>', discovered, [], {'url': root, 'kind': 'navigation'}
    async def capture(_context, branch):
        visited.append(branch['url'])
        return [(branch['url'], '<main></main>')], [], {'url': branch['url'], 'end': 'next_control_absent'}
    result = await capture_sources({'branches': [branch]}, object(), capture_branch=capture,
                                   discover_children=discover)
    assert result.unresolved and all(issue['status'] == 'child_roster_not_verified' for issue in result.unresolved)
    assert b not in visited
    if problem in {'duplicate', 'drift'}:
        assert visited == []


@pytest.mark.asyncio
async def test_detail_binding_conflict_does_not_fetch_or_choose_first(monkeypatch):
    url = 'https://example.edu/person'
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))
    definition = types._REGISTRY[('detail.profile', 1)]
    types.register_type(replace(definition, type_id='execution.other_detail'))
    branches = [{'page_types': {'detail': {'type_id': type_id, 'implementation_version': 1, 'params': {}}}}
                for type_id in ('detail.profile', 'execution.other_detail')]
    class Browser:
        async def new_page(self):
            pytest.fail('a conflicting detail binding must not open any page')
    pages, issues = await capture_profiles(Browser(), [url], 1, profile_type_branches={url: branches})
    assert pages == {url: None}
    assert issues == [{'url': url, 'status': 'profile_page_type_conflict'}]


@pytest.mark.asyncio
async def test_detail_ready_parameter_is_used_in_actual_capture():
    url = 'https://example.edu/person'
    waited = []
    class Page:
        async def goto(self, _url, **kwargs):
            return SimpleNamespace(status=200)
        def locator(self, selector):
            async def wait_for(**kwargs):
                waited.append(selector)
            return SimpleNamespace(wait_for=wait_for)
        async def content(self):
            return '<article>张伟</article>'
        async def close(self):
            pass
    class Browser:
        async def new_page(self):
            return Page()
    branch = {'page_types': {'detail': {'type_id': 'detail.profile', 'implementation_version': 1,
                                      'params': {'profile_ready_selector': 'article.person'}}}}
    pages, issues = await capture_profiles(Browser(), [url], 1, 'main.global', profile_type_branches={url: [branch]})
    assert pages[url] == '<article>张伟</article>' and issues == []
    assert waited == ['article.person']


@pytest.mark.asyncio
async def test_empty_custom_detail_output_is_unresolved(monkeypatch):
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))
    definition = types._REGISTRY[('detail.profile', 1)]
    types.register_type(replace(definition, type_id='execution.empty_detail', implementation=lambda *_args, **_kwargs: None))
    url = 'https://example.edu/person'
    branch = {'page_types': {'detail': {'type_id': 'execution.empty_detail', 'implementation_version': 1, 'params': {}}}}
    pages, issues = await capture_profiles(object(), [url], 1, profile_type_branches={url: [branch]})
    assert pages == {url: None}
    assert issues == [{'url': url, 'status': 'profile_page_type_output_invalid'}]


@pytest.mark.parametrize('binding', [
    {'type_id': 'execution.missing', 'implementation_version': 1, 'params': {}},
    {'type_id': 'roster.cards', 'implementation_version': 999, 'params': {}},
])
def test_unknown_implementation_rejected_before_any_capture(binding):
    case = deepcopy(next(case for case in CASES if case['id'] == 'ordinary_full_biography'))
    case['plan']['branches'][0]['page_types'] = {'roster': binding}
    with pytest.raises(ValueError, match='unknown page type'):
        batch._validate_plan(case['plan'])


def rank_grid_branch():
    return {'page_types': {'roster': {'type_id': 'roster.rank_grid', 'implementation_version': 1,
        'params': {'container_selector': '.rankgroup', 'record_start_selector': 'h2',
                   'card_selector': '.names'}}}}


def test_typed_roster_probe_uses_token_units_and_only_counts():
    html = '<section class="rankgroup"><h2>教授</h2><div class="names">王芳、李华</div></section>'
    reading = roster_card_reading(html, 'https://example.edu/ranks', '.names', branch=rank_grid_branch())
    assert reading == {'listed_items': 2, 'extracted_candidates': 2, 'awaiting_review': 0,
                       'reviewed_exclusions': 0, 'issues': []}
    assert '王芳' not in json.dumps(reading, ensure_ascii=False)


def test_typed_roster_probe_retains_unknown_tokens_as_controlled_issues():
    html = '<section class="rankgroup"><h2>教授</h2><div class="names">王芳、待定</div></section>'
    reading = roster_card_reading(html, 'https://example.edu/ranks', '.names', branch=rank_grid_branch())
    assert reading['listed_items'] == 2
    assert 'rank_grid_name_unrecognized' in reading['issues'] and 'roster_type_count_mismatch' in reading['issues']
    assert '待定' not in json.dumps(reading, ensure_ascii=False)


def test_custom_roster_empty_output_cannot_claim_a_read_page(monkeypatch):
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))
    definition = types._REGISTRY[('roster.cards', 1)]
    types.register_type(replace(definition, type_id='execution.empty_roster',
        implementation=lambda *_args, **_kwargs: RosterReconciliation([], {}, [], [])))
    branch = {'card_selector': '.person', 'page_types': {'roster': {
        'type_id': 'execution.empty_roster', 'implementation_version': 1, 'params': {}}}}
    result = reconcile_rosters([CapturedRosterPage('https://example.edu/list', '<div class="person">王芳</div>', branch)], {})
    assert result.unresolved == [{'url': 'https://example.edu/list', 'status': 'roster_page_type_output_invalid'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('next_control', [False, True])
async def test_explicit_navigation_rejects_unimplemented_parent_pagination(registered_counter, next_control):
    refs, _counts = registered_counter
    plan, _pages = mixed_plan(refs)
    if next_control:
        plan['branches'][0]['next_selector'] = 'a.next'
    else:
        definition = types._REGISTRY[('pagination.single_page', 1)]
        types.register_type(replace(definition, type_id='execution.navigation_pagination'))
        plan['branches'][0]['page_types']['pagination'] = {
            'type_id': 'execution.navigation_pagination', 'implementation_version': 1, 'params': {}}
    async def must_not_capture(*args, **kwargs):
        pytest.fail('unsupported parent pagination must be rejected before capture')
    with pytest.raises(ValueError, match='navigation pagination is not applicable'):
        await capture_sources(plan, object(), capture_branch=must_not_capture, discover_children=must_not_capture)


def test_cards_new_version_invokes_current_parser_and_returns_typed_evidence(monkeypatch):
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))
    definition = types._REGISTRY[('roster.rank_grid', 1)]
    calls = []
    def version_two(operation, *args, **kwargs):
        calls.append(2)
        return definition.implementation(operation, *args, **kwargs)
    types.register_type(replace(definition, type_id='roster.cards', implementation_version=2,
                                implementation=version_two))
    branch = rank_grid_branch()
    branch['page_types']['roster'].update(type_id='roster.cards', implementation_version=2)
    html = '<section class="rankgroup"><h2>教授</h2><div class="names">王芳、李华</div></section>'
    reading = roster_card_reading(html, 'https://example.edu/ranks', '.names', branch=branch)
    assert calls == [2]
    assert reading == {'listed_items': 2, 'extracted_candidates': 2, 'awaiting_review': 0,
                       'reviewed_exclusions': 0, 'issues': []}


@pytest.mark.parametrize('type_id,kind,params,html', [
    ('roster.cards', 'roster', {'card_selector': '.person'}, '<div class="person">王芳</div>'),
    ('roster.segmented', 'segmented', {'container_selector': '#people', 'record_start_selector': 'h2'},
     '<div id="people"><h2>王芳</h2><p>职称：教授</p></div>'),
    ('roster.image_table', 'image_table', {'card_selector': 'img'}, '<img src="/roster.png">'),
])
def test_new_version_cannot_inherit_legacy_evidence_exemption(monkeypatch, type_id, kind, params, html):
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))
    definition = types._REGISTRY[(type_id, 1)]
    calls = []
    def legacy_output(operation, *args, **kwargs):
        calls.append(2)
        return definition.implementation(operation, *args, **kwargs)
    types.register_type(replace(definition, implementation_version=2, implementation=legacy_output))
    branch = {'kind': kind, 'page_types': {'roster': {'type_id': type_id,
        'implementation_version': 2, 'params': params}}}
    reading = roster_card_reading(html, 'https://example.edu/list', params.get('card_selector', 'h2'), branch=branch)
    assert calls == [2]
    assert isinstance(reading, dict) and 'issues' in reading
    assert 'roster_type_output_invalid' in reading['issues']


@pytest.mark.asyncio
@pytest.mark.parametrize('role,type_id', [('entrance', 'entrance.direct'),
    ('pagination', 'pagination.single_page'), ('detail', 'detail.profile')])
async def test_run_plan_rejects_unproven_role_version_before_browser_access(monkeypatch, tmp_path, role, type_id):
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))
    definition = types._REGISTRY[(type_id, 1)]
    types.register_type(replace(definition, implementation_version=2))
    case = deepcopy(next(case for case in CASES if case['id'] == 'ordinary_full_biography'))
    case['plan']['branches'][0]['page_types'] = {role: {
        'type_id': type_id, 'implementation_version': 2, 'params': {}}}
    class Browser:
        async def new_page(self):
            pytest.fail('unsupported version must be rejected before opening a browser page')
    with pytest.raises(ValueError, match=f'unsupported {role} page type evidence contract'):
        await batch.run_plan(case['plan'], Browser(), tmp_path)
