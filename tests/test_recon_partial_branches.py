"""Failed siblings remain unresolved without suppressing independently proved branches."""
import hashlib
import json
import threading
from types import SimpleNamespace

import pytest

from server import agent_recon
from server.recon_contract import disposition_event_issues
from server.source_qualification import SOURCE_FIELDS, witness
from test_source_role_staged import pair, TARGET, HTML


def proof_events(branch, cid, *, roster=False):
    url = branch['url']
    events = pair('browser_navigate', {'url': url}, {'url': url, 'requested_url': url,
        'source_html_sha256': hashlib.sha256(HTML.encode()).hexdigest(), 'status': 200}, cid + '-visit')
    args = {key: branch[key] for key in SOURCE_FIELDS}
    if roster:
        args['unit_selector'] = branch['card_selector']
    proof = {**witness(HTML, url, branch, TARGET), 'confirmed': True,
        'dom_stable': True, 'network_idle': True, 'witness_visible': True}
    events += pair('browser_verify_source', args, proof, cid + '-source')
    if roster:
        events += pair('browser_verify', {'card_selector': branch['card_selector']},
            {'url': url, 'cardCount': 1, 'pagination': {'total': 0}}, cid + '-roster')
    return events


@pytest.mark.parametrize('failure', ['structure', 'budget', 'cancel', 'hygiene', 'external_cancel'])
def test_proved_branch_survives_only_supported_sibling_failure(monkeypatch, failure):
    claims = [{'url': TARGET['url'] + name, 'source_role': 'faculty_roster',
        'source_container_selector': 'main ul', 'role_evidence_selector': 'h1',
        'source_context_selector': 'header'} for name in ('good', 'pending')]
    accepted = {**claims[0], 'card_selector': 'main ul > li', 'single_page': True}
    def model(prompt, sid, cfg, patches):
        if cfg['phase'] == 'discovery':
            value = {'school': TARGET['school'], 'college': TARGET['college'], 'branches': claims}
            events = [event for index, claim in enumerate(claims)
                      for event in proof_events(claim, f'{sid}-{index}')]
        elif sid.endswith('branch-1'):
            value, events = accepted, proof_events(accepted, sid, roster=True)
        else:
            errors = {'structure': agent_recon.ReconPlanError, 'budget': agent_recon.ReconBudgetExceeded,
                      'cancel': agent_recon.ReconCancelled, 'hygiene': agent_recon.PlanHygieneError,
                      'external_cancel': agent_recon.ReconBudgetExceeded}
            if failure == 'external_cancel':
                cfg['cancel_event'].set()
            error = errors[failure]('controlled sibling failure')
            error.diagnostics = {'model_calls': 2,
                'token_usage': {'uncached_input': 10, 'cached_input': 20, 'output': 5},
                'budget_total': {'calls': 2}}
            raise error
        return SimpleNamespace(final_response=json.dumps(value), finish_reason='completed', events=events)
    monkeypatch.setattr(agent_recon, '_execute_harness', model)
    cfg = {'target': TARGET, 'timeout_seconds': 30, 'cancel_event': threading.Event()}
    if failure in {'cancel', 'hygiene', 'external_cancel'}:
        with pytest.raises(agent_recon.ReconError):
            agent_recon._run_staged_harness('partial', cfg, ())
        return
    result = agent_recon._run_staged_harness('partial', cfg, ())
    plan = json.loads(result.final_response)
    assert plan['branches'] == [accepted]
    assert plan['source_dispositions'] == [{'url': claims[1]['url'], 'reason': 'source_structure_unresolved'}]
    assert not disposition_event_issues(plan, result.events)
    assert result.phase_metrics[-1]['partial'] is True
    if failure == 'budget':
        assert result.partial_metrics['model_calls'] == 2
        assert result.stop_reason == 'recon_budget_exceeded'
        assert not cfg['cancel_event'].is_set()
        assert any(phase.get('stop_reason') == 'recon_budget_exceeded' for phase in result.phase_metrics)


def test_no_accepted_branch_still_raises_original_failure(monkeypatch):
    from test_source_role_staged import test_staged_harness_requires_fresh_branch_role_proof
    test_staged_harness_requires_fresh_branch_role_proof(monkeypatch, True)


def test_template_reuses_geometry_but_preserves_current_source_relationship(monkeypatch):
    claims = [{'url': TARGET['url'] + name, 'source_role': role,
        'source_container_selector': 'main ul', 'role_evidence_selector': 'h1',
        'source_context_selector': 'header'} for name, role in (
            ('faculty', 'faculty_roster'), ('members', 'organizational_membership'))]
    first = {**claims[0], 'card_selector': 'main ul > li', 'single_page': True}
    seen = []
    def model(prompt, sid, cfg, patches):
        if cfg['phase'] == 'discovery':
            value = {'school': TARGET['school'], 'college': TARGET['college'], 'branches': claims}
            events = [event for index, claim in enumerate(claims)
                      for event in proof_events(claim, f'{sid}-{index}')]
        else:
            value, events = first, proof_events(first, sid, roster=True)
        return SimpleNamespace(final_response=json.dumps(value), finish_reason='completed', events=events)
    def reuse(branch, target, root, timeout, fingerprint):
        seen.append(branch)
        assert branch['source_role'] == 'organizational_membership'
        return {'valid': True, 'events': proof_events(branch, 'reuse', roster=True), 'evidence_root': str(root)}
    from server import recon_templates
    monkeypatch.setattr(agent_recon, '_execute_harness', model)
    monkeypatch.setattr(recon_templates, 'inspect_templates', lambda urls, *a: {url: 'same-shape' for url in urls})
    monkeypatch.setattr(recon_templates, 'verify_template', reuse)
    result = agent_recon._run_staged_harness('templates', {
        'target': TARGET, 'timeout_seconds': 30, 'template_reuse': True}, ())
    branches = json.loads(result.final_response)['branches']
    assert len(seen) == 1
    assert [branch['source_role'] for branch in branches] == ['faculty_roster', 'organizational_membership']
