"""Version/cache/coverage boundaries that composition must preserve."""
import copy
from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from server.harvest_coverage import build_coverage_ledger
from server.harvest_state import HarvestState
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
from server.page_types import normalize_branch, register_type, resolve_type


def test_saved_sixty_person_source_has_identical_fields_and_evidence_with_explicit_types():
    fixtures = Path(__file__).parent / 'fixtures' / 'harvest_acceptance'
    metadata = json.loads((fixtures / 'real_source.json').read_text())
    html = (fixtures / 'real_hnucm_directory.html').read_text()
    implicit = {'url': metadata['url'], 'card_selector': metadata['selector']}
    explicit = normalize_branch(implicit)
    plan = {'source_kind': 'university_faculty_roster'}
    old_shape = reconcile_rosters([CapturedRosterPage(metadata['url'], html, implicit)], plan)
    typed = reconcile_rosters([CapturedRosterPage(metadata['url'], html, explicit)], plan)
    assert len(typed.candidates) == 60
    assert [asdict(person) for person in typed.candidates] == [asdict(person) for person in old_shape.candidates]
    assert typed.candidate_sources == old_shape.candidate_sources
    assert typed.observations == old_shape.observations
    assert typed.unresolved == old_shape.unresolved == []


def test_implementation_version_change_cannot_restore_previous_page_checkpoint(tmp_path):
    original = resolve_type({'type_id': 'roster.cards', 'implementation_version': 1}, 'roster')
    for version in (1, 2):
        register_type(replace(original, type_id='roster.checkpoint_contract', implementation_version=version))
    plan = {'branches': [{'url': 'https://fixture.example.edu/list', 'card_selector': 'li',
                         'page_types': {'roster': {'type_id': 'roster.checkpoint_contract',
                                                  'implementation_version': 1}}}]}
    first = HarvestState(tmp_path, plan)
    first.save_page('roster', plan['branches'][0]['url'], '<main>old source</main>')
    changed = copy.deepcopy(plan)
    changed['branches'][0]['page_types']['roster']['implementation_version'] = 2
    second = HarvestState(tmp_path, changed)
    assert first.plan_digest != second.plan_digest
    assert second.load_page('roster', plan['branches'][0]['url']) is None
    assert first.load_page('roster', plan['branches'][0]['url']) is not None


def test_plan_cache_contract_includes_executable_registry_source(tmp_path, monkeypatch):
    from server import recon_plan_cache
    from server.page_types import registered_implementation_manifest
    source = recon_plan_cache.HERE
    replica = tmp_path / 'server'
    replica.mkdir()
    for name in ('recon_discovery.md', 'recon_instructions.md', 'recon_provided_links.md',
                 'recon_dom_probe.js', 'recon_contract.py', 'source_qualification.py', 'page_types.py',
                 'page_type_rank_grid.py'):
        (replica / name).write_bytes((source / name).read_bytes())
    for name in registered_implementation_manifest():
        assert not Path(name).is_absolute(), 'temporary registry definitions must clean up after their test'
        original = source.parent / name
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(original.read_bytes())
    monkeypatch.setattr(recon_plan_cache, 'HERE', replica)
    before = recon_plan_cache._contract()
    with (replica / 'page_types.py').open('a') as stream:
        stream.write('\n# isolated registry revision\n')
    assert recon_plan_cache._contract() != before


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'drift', 'unterminated', 'parent_loading'])
def test_nested_explicit_composition_does_not_claim_coverage_for_incomplete_children(damage):
    leaf = {'url': 'https://fixture.example.edu/list'}
    nested = {'url': 'https://fixture.example.edu/groups', 'kind': 'navigation', 'child_branches': [leaf]}
    branch = {'url': 'https://fixture.example.edu/home', 'kind': 'navigation', 'child_branches': [nested]}
    evidence = {'url': branch['url'], 'kind': 'navigation', 'navigation_termination_verified': True, 'child_visits': [
        {'url': nested['url'], 'kind': 'navigation', 'navigation_termination_verified': True, 'child_visits': [
            {'url': leaf['url'], 'end': 'next_control_absent'}]}]}
    capture = SimpleNamespace(branches=[evidence], roster_pages=[])
    args = ({'branches': [branch]}, capture, [], [], {}, [], [])
    assert build_coverage_ledger(*args)['complete']
    children = evidence['child_visits'][0]['child_visits']
    if damage == 'missing':
        children.clear()
    elif damage == 'duplicate':
        children.append(dict(children[0]))
    elif damage == 'drift':
        children[0]['url'] += '?changed'
    elif damage == 'unterminated':
        children[0]['end'] = 'max_pages_reached'
    else:
        evidence['navigation_termination_verified'] = False
    ledger = build_coverage_ledger(*args)
    assert not ledger['complete']
    assert ledger['issues'][0]['status'] == 'branch_termination_unverified'
    assert ledger['discovery_completeness'] == 'requires_independent_navigation_evidence'
