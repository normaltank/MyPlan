"""Stage-proof identity, actual captures and implementation drift, entirely offline."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from server import page_types as types


@pytest.fixture
def registry(monkeypatch):
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))


def ref(identifier, version=1):
    return {'type_id': identifier, 'implementation_version': version, 'params': {}}


def probe(context, result):
    return {'source_hash': context.source_html_sha256, 'mechanism': 'test_capture',
            'has_actual_capture': isinstance(result, types.ProfileCapture)}


def validator(context, result, payload):
    return (payload == {'source_hash': context.source_html_sha256,
                       'mechanism': 'test_capture', 'has_actual_capture': True}
            and (result is None or isinstance(result, types.ProfileCapture)))


def install_detail():
    original = types.resolve_type(ref('detail.profile'), 'detail')
    types.register_type(replace(original, type_id='detail.test_capture',
        stage_probe=probe, evidence_validator=validator, evidence_contract='actual-capture-v1'))
    return {'url': 'https://example.edu/list', 'profile_content_selector': 'article',
            'page_types': {'detail': ref('detail.test_capture')}}


def capture(html='<article>body</article>'):
    return types.ProfileCapture(html, {'confirmed': True}, [], {'final_url': 'https://example.edu/person'})


def proof_for(branch, result=None):
    return asyncio.run(types.stage_evidence_for_result('detail', branch, result or capture(),
                        requested_url='https://example.edu/person'))


def valid(branch, proof, **changes):
    args = {'requested_url': 'https://example.edu/person', 'final_url': 'https://example.edu/person',
            'source_html_sha256': hashlib.sha256(capture().html.encode()).hexdigest(),
            'source_html': capture().html, **changes}
    return types.validate_stage_evidence('detail', branch, proof, **args)


def test_handler_really_executes_once_and_result_is_preserved(registry):
    branch = install_detail()
    calls, local = [], {}

    async def handler():
        calls.append('actual handler')
        return capture()

    proof = asyncio.run(types.run_stage_probe('detail', branch, handler,
        requested_url='https://example.edu/person', local_capture=local))
    assert calls == ['actual handler']
    assert local['result'] == capture()
    assert valid(branch, proof)
    assert set(proof) == {'role', 'type_id', 'implementation_version', 'params', 'requested_url',
                         'final_url', 'source_html_sha256', 'implementation_digest', 'payload'}
    assert types.requires_stage_evidence('detail', branch)
    types.validate_production_bindings(branch)
    assert any(entry['type_id'] == 'detail.test_capture' for entry in types.catalog())


@pytest.mark.parametrize('field,value', [
    ('role', 'entrance'), ('type_id', 'detail.profile'), ('implementation_version', 2),
    ('params', {}), ('requested_url', 'https://example.edu/other'),
    ('final_url', 'https://example.edu/other'), ('source_html_sha256', '0' * 64),
    ('implementation_digest', '0' * 64), ('payload', {'mechanism': 'fake'}),
])
def test_proof_cannot_change_type_structure_source_or_implementation(registry, field, value):
    branch = install_detail()
    proof = proof_for(branch)
    proof[field] = value
    assert not valid(branch, proof)


def test_unknown_fields_bad_hash_and_wrong_current_html_fail_closed(registry):
    branch = install_detail()
    proof = proof_for(branch)
    assert not valid(branch, {**proof, 'teacher': 'bad'})
    assert not valid(branch, proof, source_html_sha256='not a digest')
    assert not valid(branch, proof, source_html='<article>changed</article>')
    assert not valid(branch, proof, result=capture('<article>changed</article>'))


def test_raw_capture_cannot_be_overridden_and_failure_originals_remain(registry):
    branch = install_detail()
    for overrides in ({'source_html': '<article>earlier</article>'}, {'final_url': 'https://example.edu/old'}):
        with pytest.raises(ValueError, match='actual capture'):
            asyncio.run(types.stage_evidence_for_result('detail', branch, capture(),
                requested_url='https://example.edu/person', **overrides))
    local = {}
    failure = types.ProfileCapture('<article/>', None, [{'status': 'blocked'}], {})
    with pytest.raises(ValueError, match='successful captured'):
        asyncio.run(types.run_stage_probe('detail', branch, lambda: failure,
            requested_url='https://example.edu/person', local_capture=local))
    assert local['result'] is failure


def test_new_detail_cannot_use_legacy_none_mutation_contract(registry):
    branch = install_detail()
    with pytest.raises(ValueError, match='output contract'):
        types.invoke('detail', branch, lambda: None)
    assert types.invoke('detail', {}, lambda: None) is None


def test_partial_stage_hooks_and_async_validator_are_rejected(registry):
    original = types.resolve_type(ref('detail.profile'), 'detail')
    with pytest.raises(ValueError, match='probe, validator and contract'):
        types.register_type(replace(original, type_id='detail.partial', stage_probe=probe))

    async def asynchronous_validator(*args):
        return True

    with pytest.raises(ValueError, match='synchronous'):
        types.register_type(replace(original, type_id='detail.async_validator', stage_probe=probe,
            evidence_validator=asynchronous_validator, evidence_contract='x'))


def test_legacy_version_copy_does_not_inherit_proof_exemption(registry):
    original = types.resolve_type(ref('detail.profile'), 'detail')
    types.register_type(replace(original, implementation_version=2))
    branch = {'page_types': {'detail': ref('detail.profile', 2)}}
    with pytest.raises(ValueError, match='unsupported detail'):
        types.requires_stage_evidence('detail', branch)
    assert types.requires_stage_evidence('detail', {}) is False


def test_source_manifest_override_and_selected_digest_are_file_bound(registry, tmp_path):
    branch = install_detail()
    plan = {'branches': [branch]}
    before = types.selected_implementation_digest(plan)
    manifest = types.registered_implementation_manifest()
    repo = Path(types.__file__).resolve().parent.parent
    for relative in manifest:
        assert not Path(relative).is_absolute()
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((repo / relative).read_bytes())
    assert types.selected_implementation_digest(plan, root=tmp_path / 'server') == before
    copied_test = tmp_path / 'tests/test_page_type_stage_registry.py'
    copied_test.write_text(copied_test.read_text() + '\n# source drift\n')
    assert types.selected_implementation_digest(plan, root=tmp_path / 'server') != before
    copied_test.unlink()
    with pytest.raises(FileNotFoundError):
        types.selected_implementation_digest(plan, root=tmp_path / 'server')


def test_selected_digest_ignores_unselected_registry_additions_and_recurses(registry):
    branch = install_detail()
    plan = {'branches': [branch]}
    before = types.selected_implementation_digest(plan)
    original = types.resolve_type(ref('roster.cards'), 'roster')
    types.register_type(replace(original, type_id='roster.unselected'))
    assert types.selected_implementation_digest(plan) == before
    assert types.selected_implementation_digest({'branches': [{}]}, only_nonlegacy=True) == ''
    nested = {'branches': [{'kind': 'navigation', 'child_branches': [deepcopy(branch)]}]}
    assert types.selected_implementation_digest(nested, only_nonlegacy=True)
    assert types.selected_implementation_digest(nested, only_nonlegacy=True) == types.selected_implementation_digest(
        plan, only_nonlegacy=True)


def test_three_actual_stage_definitions_are_independently_selectable():
    ids = {item['type_id'] for item in types.catalog()}
    assert {'entrance.scoped_links', 'pagination.verified_next', 'detail.article_region'} <= ids
    branch = {'kind': 'navigation', 'child_link_selector': '.group',
              'page_types': {'entrance': ref('entrance.scoped_links')}, 'child_branches': [{
                  'card_selector': '.person', 'next_selector': 'a.next', 'profile_content_selector': 'article',
                  'page_types': {'pagination': ref('pagination.verified_next'), 'detail': ref('detail.article_region')}}]}
    types.validate_production_bindings(branch)
    assert types.requires_stage_evidence('entrance', branch)
    child = branch['child_branches'][0]
    assert types.requires_stage_evidence('pagination', child)
    assert types.requires_stage_evidence('detail', child)


def test_actual_handler_probe_validator_execution_never_changes_digest():
    # A real registered parser/probe/validator path formerly mutated marshal's
    # reference encoding without any source or mechanism change.
    identifier = 'detail.article_region'
    branch = {'profile_content_selector': 'article', 'page_types': {'detail': ref(identifier)}}
    definition = types.resolve_type(ref(identifier), 'detail')
    binding = types.binding_for(branch, 'detail')
    html = '<article>Profile body</article>'
    sha = hashlib.sha256(html.encode()).hexdigest()
    before = types.implementation_digest(binding, 'detail')
    context = types.StageEvidenceContext('detail', binding, types.normalize_branch(branch),
        'https://example.edu/person', 'https://example.edu/person', sha, html)
    result = types.ProfileCapture(html, {'confirmed': True, 'container_count': 1,
        'container_visible': True, 'dom_stable': True, 'network_idle': True,
        'html_sha256': sha, 'profile_content_selector': 'article',
        'profile_content_start_selector': None}, [], {'final_url': context.final_url})
    for _ in range(20):
        payload = definition.stage_probe(context, result)
        assert definition.evidence_validator(context, result, payload)
        assert types.implementation_digest(binding, 'detail') == before

    class FailedContext:
        async def new_page(self):
            raise RuntimeError('offline browser is absent')

    # Run the actual selected handler as well; it fails before browser access,
    # while still executing its code object and imported helpers.
    with pytest.raises(RuntimeError, match='offline browser'):
        asyncio.run(definition.implementation(None, FailedContext(), context.requested_url, branch))
    assert types.implementation_digest(binding, 'detail') == before


def test_digest_is_stable_across_fresh_python_processes():
    refs = [('entrance.scoped_links', 'entrance'), ('pagination.verified_next', 'pagination'),
            ('detail.article_region', 'detail')]
    expected = [types.implementation_digest(ref(identifier), role) for identifier, role in refs]
    script = ('import json\nfrom server import page_types as t\n'
              f'refs={refs!r}\n'
              'print(json.dumps([t.implementation_digest({"type_id":i,'
              '"implementation_version":1,"params":{}},r) for i,r in refs]))\n')
    root = Path(types.__file__).resolve().parent.parent
    for _ in range(2):
        output = subprocess.check_output([sys.executable, '-c', script], cwd=root, text=True)
        assert json.loads(output) == expected
