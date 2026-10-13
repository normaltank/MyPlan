"""A completed SDK phase without an accepted plan must retain its actual refusal."""
import json

import pytest

from server import agent_recon


@pytest.mark.asyncio
async def test_plan_error_retains_controlled_last_gate_in_audit_and_repair(tmp_path, monkeypatch):
    issue = {'path': '$.plan', 'status': 'schema_model_type',
             'failed_condition': 'plan_json_not_object', 'need': 'Supply the plan object.'}
    def failed(_prompt, _sid, cfg):
        cfg['gate_rejections']['recon-test-discover'] = [issue]
        raise agent_recon.ReconPlanError('unsafe rejected material teacher@example.edu')
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', failed)
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        await agent_recon.reconnoiter('样本大学', '样本学院', 'https://example.edu/',
            session_id='recon-test', config={'workspace': str(tmp_path)}, timeout_seconds=3)
    error = caught.value
    assert error.diagnostics['last_gate_rejections'] == [{
        'session_id': 'recon-test-discover', 'issues': [issue]}]
    assert error.safe_feedback['issues'] == [{
        'status': 'plan_contract_rejected', 'gate_issues': [issue]}]
    assert 'teacher@example.edu' not in json.dumps([error.diagnostics, error.safe_feedback])


@pytest.mark.asyncio
async def test_no_actual_refusal_does_not_invent_one(tmp_path, monkeypatch):
    def failed(*_args):
        raise agent_recon.ReconPlanError('unsafe rejected material teacher@example.edu')
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', failed)
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        await agent_recon.reconnoiter('样本大学', '样本学院', 'https://example.edu/',
            session_id='recon-test', config={'workspace': str(tmp_path)}, timeout_seconds=3)
    assert 'last_gate_rejections' not in getattr(caught.value, 'diagnostics', {})
    assert not getattr(caught.value, 'safe_feedback', {}).get('issues')


@pytest.mark.asyncio
async def test_specific_page_fact_keeps_its_recovery_classification(tmp_path, monkeypatch):
    from server.recovery_policy import classify_issues
    issue = {'status': 'profile_content_not_verified', 'url': 'https://example.edu/profile',
             'failed_condition': 'network_not_idle'}
    def failed(_prompt, _sid, cfg):
        cfg['gate_rejections']['recon-test-branch'] = [issue]
        raise agent_recon.ReconGateStopped('recon_page_unverifiable', [issue])
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', failed)
    with pytest.raises(agent_recon.ReconGateStopped) as caught:
        await agent_recon.reconnoiter('样本大学', '样本学院', 'https://example.edu/',
            session_id='recon-test', config={'workspace': str(tmp_path)}, timeout_seconds=3)
    feedback = caught.value.safe_feedback
    assert feedback['issues'][0]['status'] == 'recon_page_unverifiable'
    assert len(feedback['issues']) == 1
    assert classify_issues(feedback['issues']).access
    assert not classify_issues(feedback['issues']).structure


@pytest.mark.asyncio
async def test_worker_persists_actual_refusal_from_failed_sdk_phase(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from server.agent_worker import AgentWorker

    issue = {'path': '$.plan', 'status': 'schema_model_type',
             'failed_condition': 'plan_json_not_object', 'need': 'Supply the plan object.'}
    def failed(_prompt, _sid, cfg):
        cfg['gate_rejections']['recon-test-discover'] = [issue]
        error = agent_recon.ReconPlanError('unsafe rejected material teacher@example.edu')
        error.usage_unknown = True
        raise error
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', failed)
    worker = AgentWorker(db_path=tmp_path / 'tasks.db', output_dir=tmp_path / 'out',
                         max_recon_repairs=0)
    monkeypatch.setattr(agent_recon, '_config_from_env', lambda: {'workspace': str(tmp_path)})
    task = SimpleNamespace(school='样本大学', college='样本学院', url='https://example.edu/')
    report = await worker._harvest_with_repair(task, tmp_path / 'out')
    audit = json.loads((tmp_path / 'out' / 'recon-attempts.json').read_text(encoding='utf-8'))
    assert not report['delivery_ready']
    assert report['unresolved'][0]['gate_issues'] == [issue]
    assert audit['attempts'][0]['last_gate_rejections'][0]['issues'] == [issue]
    assert audit['attempts'][0]['issues'][0]['gate_issues'] == [issue]
    assert audit['usage_unknown'] is True
    assert 'teacher@example.edu' not in json.dumps([audit, report])
