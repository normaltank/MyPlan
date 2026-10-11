"""Actual staged discovery/branch gates, with a fixed model event boundary."""
import json
from types import SimpleNamespace

import pytest

from server import agent_recon
from server.source_qualification import SOURCE_FIELDS, witness

TARGET = {'school': '样本大学', 'college': '样本学院', 'url': 'https://example.edu/'}
URL = 'https://example.edu/faculty'
CLAIM = {'url': URL, 'source_role': 'faculty_roster', 'source_container_selector': 'main ul',
         'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
HTML = '<title>教师名录</title><header>样本大学样本学院</header><h1>教师名录</h1><main><ul><li>王芳</li></ul></main>'


def pair(name, args, result, cid):
    return [{'type': 'tool/call', 'data': {'callId': cid, 'name': 'mcp__playwright__'+name, 'arguments': json.dumps(args)}},
            {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': cid, 'content': [{'type': 'text', 'text': json.dumps(result)}]}]}}}]


def source_events(branch, cid):
    args = {key: branch[key] for key in SOURCE_FIELDS}
    if branch.get('card_selector'):
        args['unit_selector'] = branch['card_selector']
    proof = {**witness(HTML, URL, branch, TARGET), 'confirmed': True, 'dom_stable': True, 'network_idle': True, 'witness_visible': True}
    return pair('browser_verify_source', args, proof, cid)


@pytest.mark.parametrize('missing_proof', [False, True])
def test_staged_harness_requires_fresh_branch_role_proof(monkeypatch, missing_proof):
    calls = []
    def model(_prompt, sid, cfg, patches):
        calls.append(cfg['phase'])
        if cfg['phase'] == 'discovery':
            value = {'school': TARGET['school'], 'college': TARGET['college'], 'branches': [CLAIM]}
            events = pair('browser_navigation', {}, {'items': [{'url': URL}]}, sid+'nav') + source_events(CLAIM, sid+'source')
        else:
            value = {**CLAIM, 'card_selector': 'main ul > li', 'single_page': True}
            events = pair('browser_verify', {'card_selector': value['card_selector']}, {'url': URL, 'cardCount': 1, 'pagination': {'total': 0}}, sid+'cards')
            if not missing_proof:
                events += source_events(value, sid+'source')
        return SimpleNamespace(final_response=json.dumps(value), finish_reason='completed', events=events)
    monkeypatch.setattr(agent_recon, '_execute_harness', model)
    if missing_proof:
        with pytest.raises(agent_recon.ReconPlanError) as error:
            agent_recon._run_staged_harness('roles', {'target': TARGET, 'timeout_seconds': 30}, ())
        assert any(issue['status'] == 'source_role_evidence_unverified' for issue in error.value.safe_feedback['issues'])
    else:
        result = agent_recon._run_staged_harness('roles', {'target': TARGET, 'timeout_seconds': 30}, ())
        assert json.loads(result.final_response)['branches'][0]['source_role'] == 'faculty_roster'
    assert calls == ['discovery', 'branch']
