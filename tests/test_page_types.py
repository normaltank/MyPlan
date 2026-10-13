"""Structure binding boundaries and real trusted dispatch, without network/DB."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from server import page_types as types
from server.recon_contract import Branch


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))


def ref(type_id, params=None, version=1):
    return {'type_id': type_id, 'implementation_version': version, 'params': params or {}}


def source_branch(**fields):
    return {'url': 'https://example.edu/list', 'card_selector': '.person', 'single_page': True,
            'source_role': 'faculty_roster', 'source_container_selector': 'main',
            'role_evidence_selector': 'h1', 'source_context_selector': 'header', **fields}


def test_legacy_bindings_are_stable_and_leave_input_evidence_unchanged():
    branch = source_branch(profile_content_selector='.bio')
    normalized = types.normalize_branch(branch)
    assert 'page_types' not in branch
    assert normalized['source_container_selector'] == branch['source_container_selector']
    assert types.normalize_branch(normalized) == normalized
    assert {role: binding['type_id'] for role, binding in normalized['page_types'].items()} == {
        'entrance': 'entrance.direct', 'roster': 'roster.cards',
        'pagination': 'pagination.single_page', 'detail': 'detail.profile'}


@pytest.mark.parametrize('binding', [ref('roster.absent'), ref('roster.cards', version=2),
                                   ref('detail.profile'), ref('roster.cards', {'teacher_json': 'x'}),
                                   ref('roster.cards', {'card_selector': '张三'}),
                                   ref('roster.cards', {'source_role': 'faculty_roster'}),
                                   ref('roster.cards', {'card_selector': {'name': 'x'}})])
def test_unknown_versions_roles_and_nonstructural_payloads_fail(binding):
    with pytest.raises(ValueError):
        types.normalize_branch({'page_types': {'roster': binding}})


@pytest.mark.parametrize('params', [{'max_pages': True}, {'max_pages': 0},
                                   {'single_page': 1}, {'next_selector': ''}])
def test_structural_parameter_scalar_types_are_strict(params):
    with pytest.raises(ValueError):
        types.normalize_branch({'page_types': {'pagination': ref('pagination.next_control', params)}})


def test_parameter_projection_preserves_and_cannot_override_proven_structure():
    branch = source_branch(page_types={'detail': ref('detail.profile', {'profile_content_selector': '.bio'})})
    assert types.normalize_branch(branch)['profile_content_selector'] == '.bio'
    branch['profile_content_selector'] = '.different'
    with pytest.raises(ValueError, match='conflicts'):
        types.normalize_branch(branch)


def test_implicit_and_explicit_same_effective_detail_are_canonical_equal():
    implicit = {'profile_content_selector': '.bio'}
    explicit = {'page_types': {'detail': ref('detail.profile', {'profile_content_selector': '.bio'})}}
    assert types.binding_for(implicit, 'detail') == types.binding_for(explicit, 'detail')


def test_registration_is_immutable_and_requires_structure_only_model(registry):
    original = types.resolve_type(ref('roster.cards'), 'roster')
    with pytest.raises(ValueError, match='already registered'):
        types.register_type(original)

    class PersonParams(types.StructureParams):
        name: str

    with pytest.raises(ValueError, match='structure-only'):
        types.register_type(replace(original, type_id='roster.people', params_model=PersonParams))


def test_catalog_publishes_new_trusted_registration_contracts_without_executable_code(registry):
    original = types.resolve_type(ref('roster.cards'), 'roster')

    def private_implementation(*args):
        raise RuntimeError('PRIVATE_IMPLEMENTATION_CODE')

    types.register_type(replace(original, type_id='roster.catalog_extension',
        implementation=private_implementation, applicability_contract='New reviewed structural condition.'))
    entries = types.catalog()
    entry = next(item for item in entries if item['type_id'] == 'roster.catalog_extension')
    assert set(entry) == {'role', 'type_id', 'implementation_version', 'params_schema',
                          'input_contract', 'output_contract', 'failure_contract', 'applicability_contract'}
    assert entry['applicability_contract'] == 'New reviewed structural condition.'
    assert entry['params_schema']['additionalProperties'] is False
    serialized = json.dumps(entries)
    assert 'PRIVATE_IMPLEMENTATION_CODE' not in serialized
    assert 'private_implementation' not in serialized
    assert '__module__' not in serialized
    assert 'roster.rank_grid' in serialized
    entry['params_schema']['properties']['card_selector']['title'] = 'mutated caller copy'
    fresh = next(item for item in types.catalog() if item['type_id'] == 'roster.catalog_extension')
    assert fresh['params_schema']['properties']['card_selector']['title'] != 'mutated caller copy'


@pytest.mark.parametrize('role,legacy_id', [
    ('entrance', 'entrance.direct'), ('pagination', 'pagination.single_page'), ('detail', 'detail.profile'),
])
@pytest.mark.parametrize('new_version', [False, True])
def test_unproved_role_extensions_register_but_are_not_production_selectable_or_catalogued(
        registry, role, legacy_id, new_version):
    original = types.resolve_type(ref(legacy_id), role)
    type_id = legacy_id if new_version else role + '.not_proved'
    version = (max(version for existing_id, version in types._REGISTRY if existing_id == type_id) + 1
               if new_version else 1)
    types.register_type(replace(original, type_id=type_id, implementation_version=version))
    branch = {'page_types': {role: ref(type_id, version=version)}}
    assert types.binding_for(branch, role).implementation_version == version
    with pytest.raises(ValueError, match=f'unsupported {role} page type evidence contract'):
        types.validate_production_bindings(branch)
    assert not any(item['type_id'] == type_id and item['implementation_version'] == version
                   for item in types.catalog())


def test_production_guard_recurses_both_navigation_child_modes(registry):
    original = types.resolve_type(ref('detail.profile'), 'detail')
    types.register_type(replace(original, implementation_version=2))
    child = {'page_types': {'detail': ref('detail.profile', version=2)}}
    for children in ({'child_branch': child}, {'child_branches': [child]}):
        with pytest.raises(ValueError, match='unsupported detail page type evidence contract'):
            types.validate_production_bindings({'kind': 'navigation', **children})


def test_production_guard_preserves_legacy_and_typed_rank_grid_bindings():
    types.validate_production_bindings(source_branch())
    types.validate_production_bindings(types.normalize_branch(source_branch()))
    types.validate_production_bindings(rank_grid_page().branch)


@pytest.mark.parametrize('kind,legacy_id,structure', [
    ('segmented', 'roster.segmented', {'container_selector': 'main', 'record_start_selector': 'h2'}),
    ('image_table', 'roster.image_table', {'card_selector': 'main img'}),
    ('roster', 'roster.cards', {'empty_state_selector': '.empty', 'roster_container_selector': 'main'}),
])
def test_special_roster_evidence_requires_exact_legacy_version_even_in_direct_batch(
        registry, kind, legacy_id, structure):
    branch = source_branch(kind=kind, **structure)
    if kind == 'segmented':
        branch.pop('card_selector')
    types.validate_production_bindings(branch)
    types.validate_production_bindings(types.normalize_branch(branch))
    original = types.resolve_type(ref(legacy_id), 'roster')
    types.register_type(replace(original, implementation_version=2))
    types.register_type(replace(original, type_id='roster.new_special'))
    for binding in (ref(legacy_id, version=2), ref('roster.new_special')):
        changed = {**branch, 'page_types': {'roster': binding}}
        # Registration is not approval of the specialized browser evidence protocol.
        assert types.binding_for(changed, 'roster').type_id == binding['type_id']
        with pytest.raises(ValueError, match='unsupported roster page type evidence contract'):
            types.validate_production_bindings(changed)
        with pytest.raises(ValueError, match='unsupported roster page type evidence contract'):
            types.validate_production_bindings({'kind': 'navigation', 'child_branches': [changed]})


def test_new_trusted_type_replaces_operation_and_keeps_stage_contract(registry):
    calls = []

    def implementation(fallback, captured, plan):
        calls.append(captured)
        return SimpleNamespace(candidates=['newly parsed observation'], candidate_sources={},
                               observations=[{'kind': 'new_shape'}], unresolved=[])

    original = types.resolve_type(ref('roster.cards'), 'roster')
    types.register_type(replace(original, type_id='roster.new_shape', implementation=implementation))
    branch = {'page_types': {'roster': ref('roster.new_shape')}}

    def wrong_old_parser(*args):
        pytest.fail('the existing parser must not run for the new structure')

    result = types.invoke('roster', branch, wrong_old_parser, '<captured html>', {})
    assert calls == ['<captured html>']
    assert result.observations == [{'kind': 'new_shape'}]
    assert types.binding_for({}, 'roster').type_id == 'roster.cards'


def test_async_dispatch_preserves_result_and_rejects_dropped_stage_evidence(registry):
    async def capture():
        return [('https://example.edu/list', '<main/>')], [], {'complete': True}

    result = asyncio.run(types.invoke_async('pagination', {}, capture))
    assert result[2] == {'complete': True}

    async def loses_evidence():
        return [('https://example.edu/list', '<main/>')], []

    with pytest.raises(ValueError, match='output contract'):
        asyncio.run(types.invoke_async('pagination', {}, loses_evidence))


def test_schema_accepts_explicit_heterogeneous_children_and_rejects_ambiguity():
    child = source_branch(kind='segmented', card_selector=None,
                          container_selector='main', record_start_selector='h2',
                          page_types={'roster': ref('roster.segmented')})
    # None is allowed by the old structural model; full hygiene still runs outside.
    nav = source_branch(kind='navigation', child_link_selector='.group a', child_branches=[child])
    assert Branch.model_validate(nav).child_branches[0].page_types.roster.type_id == 'roster.segmented'
    with pytest.raises(ValidationError, match='without child_branch'):
        Branch.model_validate({**nav, 'child_branch': {key: value for key, value in source_branch().items()
                                                     if key != 'url'}})
    with pytest.raises(ValidationError, match='duplicate'):
        Branch.model_validate({**nav, 'child_branches': [child, child]})
    with pytest.raises(ValidationError):
        Branch.model_validate({**nav, 'child_branches': [{**child, 'name': 'a person'}]})


def test_schema_binding_does_not_supply_source_qualification():
    with pytest.raises(ValidationError):
        Branch.model_validate({'url': 'https://example.edu/list', 'single_page': True,
                               'page_types': {'roster': ref('roster.cards')}})


def rank_grid_page(extra='', heading='教授'):
    from server.harvest_workflow import CapturedRosterPage
    html = ('<html><head><title>教师名单</title></head><body><header>样本大学样本学院</header>'
            '<h1>教师名单</h1><main><section class="rankgroup"><h2>' + heading + '</h2>'
            '<div class="names"><a href="/zhang">张三</a>、<a href="/li">李四</a></div></section>'
            '<section class="rankgroup"><h2>副教授</h2><div class="names">王五、赵六</div></section>'
            + extra + '</main></body></html>')
    branch = source_branch(container_selector='.rankgroup', record_start_selector='h2',
                           card_selector='.names', profile_content_selector='.bio',
                           page_types={'roster': ref('roster.rank_grid')})
    return CapturedRosterPage(branch['url'], html, branch)


def test_real_rank_grid_parser_replaces_card_cardinality_preserves_person_links_and_source():
    from server.harvest_workflow import reconcile_rosters
    from server.source_qualification import digest
    page = rank_grid_page()
    result = reconcile_rosters([page], {'school': '样本大学', 'college': '样本学院'})
    assert [(p.name, p.title, p.url) for p in result.candidates] == [
        ('张三', '教授', 'https://example.edu/zhang'), ('李四', '教授', 'https://example.edu/li'),
        ('王五', '副教授', page.url), ('赵六', '副教授', page.url)]
    assert result.observations[0]['listed_items'] == 4
    assert result.observations[0]['groups'] == 2
    assert result.unresolved == []
    assert result.profile_content_selectors == {
        'https://example.edu/zhang': {('.bio', None)}, 'https://example.edu/li': {('.bio', None)}}
    for sources in result.candidate_sources.values():
        assert len(sources) == 1
        source = next(iter(sources))
        assert source.source_html_sha256 == digest(page.html)
        assert source.status == 'unknown'  # no forged browser proof in a parser unit test


@pytest.mark.parametrize('extra,heading,status', [
    ('<div class="names">孙七</div>', '教授', 'rank_grid_cell_outside_group'),
    ('', '实验室负责人', 'rank_grid_rank_unverified'),
    ('<section class="rankgroup"><h2>讲师</h2><div class="names">??</div></section>',
     '教授', 'rank_grid_name_unrecognized'),
])
def test_rank_grid_unknown_scope_rank_or_item_blocks_instead_of_silently_dropping(extra, heading, status):
    from server.harvest_workflow import reconcile_rosters
    result = reconcile_rosters([rank_grid_page(extra, heading)], {})
    assert status in {item['status'] for item in result.unresolved}


def test_rank_grid_same_page_same_name_in_other_rank_retains_positions_and_blocks():
    from server.harvest_workflow import reconcile_rosters
    extra = '<section class="rankgroup"><h2>讲师</h2><div class="names">王五</div></section>'
    result = reconcile_rosters([rank_grid_page(extra)], {})
    issue = next(item for item in result.unresolved if item['status'] == 'rank_grid_identity_ambiguous')
    assert issue['name'] == '王五'
    assert [item['rank'] for item in issue['positions']] == ['副教授', '讲师']
    assert sum(item.get('name') == '王五' for item in result.observations[0]['items']) == 2
