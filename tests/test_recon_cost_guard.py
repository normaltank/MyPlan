"""A college's reconnaissance spend is bounded across phases and repairs."""
from __future__ import annotations

from types import SimpleNamespace
import json

import pytest

from server import agent_recon
from server.agent_worker import AgentWorker


def _message(input_tokens=10, cached_tokens=20, output_tokens=5):
    return {"type": "assistant/message", "data": {"usage": {
        "inputTokens": input_tokens, "cacheReadTokens": cached_tokens,
        "outputTokens": output_tokens}}}


def test_budget_counts_cached_input_and_stops_at_call_limit():
    budget = agent_recon.ReconBudget(max_calls=2, max_tokens=100)
    budget.observe(_message())
    with pytest.raises(agent_recon.ReconBudgetExceeded):
        budget.observe(_message())
    assert budget.snapshot()["calls"] == 2
    assert budget.snapshot()["total_tokens"] == 70
    token_budget = agent_recon.ReconBudget(max_calls=10, max_tokens=100)
    token_budget.observe(_message())
    token_budget.observe(_message())
    with pytest.raises(agent_recon.ReconBudgetExceeded):
        token_budget.observe(_message())
    assert token_budget.snapshot()["calls"] == 3
    assert token_budget.snapshot()["total_tokens"] == 105


def test_runtime_notification_stops_session_and_keeps_partial_usage(monkeypatch):
    monkeypatch.setattr(agent_recon, "check_runtime_composition", lambda *_: None)
    seen = []

    class FakeHarness:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_):
            seen.append("closed")

        def run(self, _prompt, *, session_id, on_notification):
            for _ in range(3):
                on_notification(SimpleNamespace(method="session.event", payload={
                    "sessionId": session_id, "event": _message()}))
            return SimpleNamespace(events=[], finish_reason="completed", final_response="{}")

    import deepseek_harness
    monkeypatch.setattr(deepseek_harness, "DeepSeekHarness", FakeHarness)
    budget = agent_recon.ReconBudget(max_calls=2, max_tokens=1000)
    cfg = {"dsh_home": "home", "workspace": ".", "dsh_bin": None,
           "profile": "sdk", "provider": "deepseek", "model": "deepseek-flash",
           "effort": "high", "api_key": "test", "budget": budget}
    with pytest.raises(agent_recon.ReconBudgetExceeded) as caught:
        agent_recon._execute_harness("structure only", "sample", cfg, ())
    assert seen == ["closed"]
    assert caught.value.diagnostics["model_calls"] == 2
    assert caught.value.diagnostics["token_usage"]["cached_input"] == 40


def test_completed_usage_is_counted_when_a_parallel_sibling_has_cancelled(monkeypatch):
    import threading
    import deepseek_harness
    cancel = threading.Event()
    cancel.set()
    class Harness:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def run(self, prompt, *, session_id, on_notification):
            on_notification(SimpleNamespace(method='session.event', payload={
                'sessionId': session_id, 'event': _message()}))
    monkeypatch.setattr(deepseek_harness, 'DeepSeekHarness', Harness)
    monkeypatch.setattr(agent_recon, 'check_runtime_composition', lambda *_: None)
    budget = agent_recon.ReconBudget()
    cfg = {'dsh_home': 'home', 'workspace': '.', 'dsh_bin': None,
           'profile': 'sdk', 'provider': 'deepseek-official', 'model': 'deepseek-flash',
           'effort': 'high', 'api_key': 'fixture', 'budget': budget, 'cancel_event': cancel}
    with pytest.raises(agent_recon.ReconCancelled):
        agent_recon._execute_harness('structure only', 'cancelled', cfg, ())
    snapshot = budget.snapshot()
    assert snapshot['calls'] == 1 and snapshot['token_usage']['cached_input'] == 20
    assert snapshot['cost']['known_usd'] > 0
    assert snapshot['cost']['estimated_usd'] is None


@pytest.mark.asyncio
async def test_worker_stops_repair_loop_and_saves_budget_audit(tmp_path, monkeypatch):
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "outputs",
                         max_recon_calls=2, max_recon_tokens=1000)
    task = SimpleNamespace(task_id="sample", school="样本大学", college="样本学院",
                           url="https://example.edu/roster")
    async def no_cache(*_):
        return None
    seen = []
    async def costly_recon(*_, **kwargs):
        seen.append(kwargs["budget"])
        before = kwargs["budget"].snapshot()
        try:
            for _ in range(3):
                kwargs["budget"].observe(_message())
        except agent_recon.ReconBudgetExceeded as exc:
            exc.diagnostics = kwargs["budget"].delta(before)
            exc.usage_unknown = False
            raise
    monkeypatch.setattr(worker, "_cached_recon", no_cache)
    monkeypatch.setattr("server.agent_worker.reconnoiter", costly_recon)
    with pytest.raises(agent_recon.ReconBudgetExceeded):
        await worker._harvest_with_repair(task, tmp_path / "outputs" / "tasks" / task.task_id)
    assert len(seen) == 1
    audit = json.loads((tmp_path / "outputs" / "tasks" / task.task_id /
                        "recon-attempts.json").read_text(encoding="utf-8"))
    assert audit["status"] == "recon_budget_exceeded"
    assert audit["usage_unknown"] is False


@pytest.mark.parametrize('unknown_attempt', [
    {'phases': [{'phase': 'discovery', 'usage_unknown': True}]},
    {'cost': {'unknown_calls': 1, 'estimated_usd': None, 'known_usd': 0.1}},
    {'usage_unknown': True},
])
def test_recon_audit_preserves_unknown_spend_across_known_budget_exit(tmp_path, unknown_attempt):
    attempts = [unknown_attempt, {'status': 'recon_budget_exceeded', 'usage_unknown': False,
                                 'cost': {'unknown_calls': 0, 'estimated_usd': 0.2}}]
    AgentWorker._save_recon_audit(tmp_path, attempts, 'recon_budget_exceeded', usage_unknown=False)
    path = tmp_path / 'recon-attempts.json'
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['usage_unknown'] is True and saved['attempts'] == attempts
    AgentWorker._save_recon_audit(tmp_path, None, 'capture_failed', usage_unknown=False)
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['usage_unknown'] is True and saved['attempts'] == attempts


def test_recon_audit_retains_legacy_unknown_flag_when_only_status_changes(tmp_path):
    path = tmp_path / 'recon-attempts.json'
    path.write_text(json.dumps({'status': 'recon_failed', 'usage_unknown': True, 'attempts': []}), encoding='utf-8')
    AgentWorker._save_recon_audit(tmp_path, None, 'capture_failed', usage_unknown=False)
    assert json.loads(path.read_text(encoding='utf-8'))['usage_unknown'] is True


def test_recon_audit_keeps_fully_accounted_completion_known(tmp_path):
    attempts = [{'usage_unknown': False, 'phases': [{'usage_unknown': False}],
                 'cost': {'unknown_calls': 0, 'known_usd': 0.2, 'estimated_usd': 0.2}}]
    AgentWorker._save_recon_audit(tmp_path, attempts, 'recon_completed')
    saved = json.loads((tmp_path / 'recon-attempts.json').read_text(encoding='utf-8'))
    assert saved['usage_unknown'] is False and saved['attempts'] == attempts


@pytest.mark.asyncio
@pytest.mark.parametrize('unknown,unknown_budget,expected', [(False, False, False), (True, False, True),
                                                          (False, True, True)])
async def test_recon_result_and_worker_audit_keep_completion_usage_flag(
        tmp_path, monkeypatch, unknown, unknown_budget, expected):
    from source_contract_fixture import upgrade

    plan = upgrade({'school': '样本大学', 'college': '样本学院', 'branches': [
        {'url': 'https://example.edu/faculty', 'card_selector': 'li', 'single_page': True}]})
    budget = agent_recon.ReconBudget()
    def completed(*_):
        budget.observe(_message())
        if unknown_budget:
            budget.mark_usage_unknown()
        return SimpleNamespace(events=[_message()], finish_reason='completed',
                               final_response=json.dumps(plan), usage_unknown=unknown,
                               phase_metrics=[{'phase': 'branch', 'usage_unknown': unknown}])
    monkeypatch.setattr(agent_recon, '_run_harness_blocking', completed)
    result = await agent_recon.reconnoiter('样本大学', '样本学院', 'https://example.edu/',
        config={'workspace': str(tmp_path), 'patches': ()}, budget=budget, session_id='usage-completion')
    summary = result.summary()
    assert summary['usage_unknown'] is expected
    assert summary['model_calls'] == 1 and summary['token_usage']['cached_input'] == 20
    AgentWorker._save_recon_audit(tmp_path, [summary], 'recon_completed')
    saved = json.loads((tmp_path / 'recon-attempts.json').read_text(encoding='utf-8'))
    assert saved['usage_unknown'] is expected and saved['attempts'] == [summary]
