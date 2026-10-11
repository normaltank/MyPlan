"""Independent adversarial review of implementation identity and durable consumers.

No live browser, model, host or default database. A real temporary Python module
keeps its registration/version fixed while its source bytes change.
"""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import sys

import pytest

from server import page_types, source_review
from server.faculty_extractor import FacultyCandidate
from server.harvest_state import HarvestState
from server.recon_plan_cache import ReconPlanCache


@pytest.fixture
def reviewed_detail(tmp_path, monkeypatch):
    monkeypatch.setattr(page_types, '_REGISTRY', dict(page_types._REGISTRY))
    path = tmp_path / 'reviewed_detail.py'
    path.write_text('''from server.page_types import ProfileCapture
async def capture(_old, *args, **kwargs):
    return ProfileCapture('<article>independent original</article>', None, [],
                          {'final_url': 'https://review.example.edu/person'})
def probe(context, result):
    return {'source_hash': context.source_html_sha256, 'unit_count': 1}
def valid(context, result, payload):
    return (payload.get('source_hash') == context.source_html_sha256
            and type(payload.get('unit_count')) is int and payload['unit_count'] == 1)
''')
    name = 'independent_reviewed_detail'
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    original = page_types.resolve_type({'type_id': 'detail.profile', 'implementation_version': 1}, 'detail')
    page_types.register_type(replace(original, type_id='detail.independent_digest',
        implementation=module.capture, stage_probe=module.probe,
        evidence_validator=module.valid, evidence_contract='same source hash and one explicit unit'))
    plan = {'school': '样本大学', 'college': '样本学院', 'branches': [{
        'url': 'https://review.example.edu/list', 'card_selector': 'main li', 'single_page': True,
        'source_role': 'faculty_roster', 'source_container_selector': 'main',
        'role_evidence_selector': 'h1', 'source_context_selector': 'header',
        'page_types': {'detail': {'type_id': 'detail.independent_digest', 'implementation_version': 1,
                                  'params': {}}}}]}
    return path, plan


@pytest.mark.asyncio
async def test_same_version_module_change_invalidates_proof_state_cache_and_source_decision(
        tmp_path, reviewed_detail):
    module_path, plan = reviewed_detail
    branch = plan['branches'][0]
    person_url = 'https://review.example.edu/person'

    def forbidden_fallback(*args, **kwargs):
        pytest.fail('the selected independent detail implementation must run')

    capture = await page_types.invoke_async('detail', branch, forbidden_fallback)
    proof = await page_types.stage_evidence_for_result('detail', branch, capture, requested_url=person_url)
    validation = {'requested_url': person_url, 'final_url': person_url,
                  'source_html_sha256': proof['source_html_sha256'],
                  'source_html': capture.html, 'result': capture}
    assert page_types.validate_stage_evidence('detail', branch, proof, **validation)

    original_state = HarvestState(tmp_path / 'checkpoint', plan)
    original_state.save_page('profile', person_url, capture.html)
    cache = ReconPlanCache(tmp_path / 'plans')
    entry_url = 'https://review.example.edu/'
    cache.put(plan, entry_url, [{'url': entry_url, 'links': [branch['url']], 'fingerprint': 'a' * 64}])
    assert cache.get(plan['school'], plan['college'], entry_url) is not None

    artifact = tmp_path / 'profile-original.html'
    artifact.write_text(capture.html)
    candidate = FacultyCandidate(name='王芳', url=person_url, title='教授')
    [item] = source_review.build_items([candidate], {}, {person_url: capture.html}, plan, 'review-task',
                                      [{'url': person_url, 'path': str(artifact), 'kind': 'profile'}], [])
    decisions = tmp_path / source_review.DECISIONS_FILE
    decision = source_review.save_decision(decisions, item, 'confirm', evidence_basis='official_source',
        reason='verified fixed original', source_refs=[person_url], reviewed_by=7)
    assert source_review.valid_decision(item, decision) is not None
    original_decision_bytes = decisions.read_bytes()

    # Registration, callable object, parameters and declared version stay fixed.
    module_path.write_text(module_path.read_text() + '\n# reviewed implementation source changed\n')
    assert not page_types.validate_stage_evidence('detail', branch, proof, **validation)
    changed_state = HarvestState(tmp_path / 'checkpoint', plan)
    assert changed_state.plan_digest == original_state.plan_digest
    assert changed_state.directory != original_state.directory
    assert changed_state.load_page('profile', person_url) is None
    assert original_state.load_page('profile', person_url) == capture.html
    assert cache.get(plan['school'], plan['college'], entry_url) is None
    assert source_review.valid_decision(item, decision) is None
    assert decisions.read_bytes() == original_decision_bytes


@pytest.mark.asyncio
async def test_stage_proof_rejects_foreign_binding_request_final_document_and_digest(reviewed_detail):
    _, plan = reviewed_detail
    branch = plan['branches'][0]
    person_url = 'https://review.example.edu/person'
    capture = await page_types.invoke_async('detail', branch, lambda *_a, **_kw: None)
    proof = await page_types.stage_evidence_for_result('detail', branch, capture, requested_url=person_url)
    kwargs = {'requested_url': person_url, 'final_url': person_url,
              'source_html_sha256': proof['source_html_sha256'],
              'source_html': capture.html, 'result': capture}
    assert page_types.validate_stage_evidence('detail', branch, proof, **kwargs)
    for key, wrong in [('role', 'entrance'), ('type_id', 'detail.profile'),
                       ('implementation_version', 2), ('params', {'profile_ready_selector': 'foreign'}),
                       ('requested_url', person_url + '/foreign'), ('final_url', person_url + '/foreign'),
                       ('source_html_sha256', '0' * 64), ('implementation_digest', '0' * 64)]:
        altered = {**deepcopy(proof), key: wrong}
        assert not page_types.validate_stage_evidence('detail', branch, altered, **kwargs), key
    foreign = replace(capture, html=capture.html + '<p>foreign document</p>')
    assert not page_types.validate_stage_evidence('detail', branch, proof, **{**kwargs, 'result': foreign})
    with pytest.raises(ValueError, match='actual capture'):
        await page_types.stage_evidence_for_result('detail', branch, capture, requested_url=person_url,
                                                  source_html='substituted original')
