"""Inspect actual runtime composition, without a model/API request."""
import os
import subprocess
from pathlib import Path
import pytest
import yaml
from server import agent_recon

class ConfigLoader(yaml.SafeLoader):
    pass


ConfigLoader.add_constructor('tag:yaml.org,2002:js', lambda loader,node: loader.construct_scalar(node))


def test_default_launcher_uses_this_environment_runtime_not_ambient_path(monkeypatch,tmp_path):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-key')
    monkeypatch.delenv('DSH_BIN',raising=False)
    monkeypatch.delenv('DSH_HOME',raising=False)
    monkeypatch.setenv('PATH',str(tmp_path))
    cfg=agent_recon._config_from_env()
    assert cfg['dsh_bin'] is None
    assert Path(cfg['dsh_home'])==Path(agent_recon.__file__).resolve().parents[1]/'.runtime/dsh-home'


def test_generated_patch_inserts_browser_plugin_into_empty_sdk_profile(tmp_path):
    from deepseek_harness_runtime import bundled_runtime_path
    patch=tmp_path/'runtime.yml'
    agent_recon.write_runtime_patch(patch,phase='discovery',session_id='composition-test')
    result=subprocess.run([str(bundled_runtime_path()),'--profile','sdk','--patch',str(patch),'--dump-config'],
        env={**os.environ,'DSH_HOME':str(tmp_path/'home')},capture_output=True,text=True,encoding='utf-8',timeout=30)
    assert result.returncode==0
    entries=yaml.load(result.stdout,Loader=ConfigLoader)
    active=[e for e in entries if e.get('disabled') is not True]
    browsers=[e for e in active if e.get('name')=='@deepseek-ai/dsh-mcp-client']
    assert len(browsers)==1, result.stderr
    assert browsers[0]['config']['args'][-4:]==['--phase','discovery','--session','composition-test']
    other_tools=[e['id'] for e in active if e.get('name','').startswith('@deepseek-ai/dsh-tool-') and e['id']!='timeout-policy']
    assert other_tools==[],other_tools


@pytest.mark.parametrize("tree", [[], [{"id":"workflow","name":"@deepseek-ai/dsh-tool-workflow"}]])
def test_runtime_preflight_rejects_missing_browser_before_model_run(monkeypatch,tree):
    from types import SimpleNamespace
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs: SimpleNamespace(returncode=0,stdout=yaml.safe_dump(tree),stderr=''))
    with pytest.raises(agent_recon.ReconError,match='browser'):
        agent_recon.check_runtime_composition({'dsh_bin':'explicit-dsh','dsh_home':'isolated-home','profile':'sdk'},('patch.yml',))


def test_runtime_preflight_rejects_unexpected_general_tools(monkeypatch):
    from types import SimpleNamespace
    tree=[{'id':'faculty-recon-browser','name':'@deepseek-ai/dsh-mcp-client','config':{'serverName':'playwright','args':['-m','server.recon_browser_mcp']}},
          {'id':'workflow','name':'@deepseek-ai/dsh-tool-workflow'}]
    monkeypatch.setattr(subprocess,'run',lambda *args,**kwargs: SimpleNamespace(returncode=0,stdout=yaml.safe_dump(tree),stderr=''))
    with pytest.raises(agent_recon.ReconError,match='tool'):
        agent_recon.check_runtime_composition({'dsh_bin':'explicit-dsh','dsh_home':'isolated-home','profile':'sdk'},('patch.yml',))


def test_preflight_failure_is_known_zero_model_usage(monkeypatch):
    def reject(*args): raise agent_recon.ReconError('Harness browser plugin is missing')
    monkeypatch.setattr(agent_recon,'check_runtime_composition',reject)
    with pytest.raises(agent_recon.ReconError) as caught:
        agent_recon._execute_harness('unused','preflight',{},())
    assert caught.value.usage_unknown is False
    assert caught.value.diagnostics['model_calls']==0


@pytest.mark.asyncio
async def test_worker_records_preflight_failure_as_known_zero(tmp_path,monkeypatch):
    import json
    from types import SimpleNamespace
    from server.agent_worker import AgentWorker
    worker=AgentWorker(db_path=tmp_path/'tasks.db',output_dir=tmp_path/'outputs')
    async def reject(*args,**kwargs):
        error=agent_recon.ReconError('Harness browser plugin is missing')
        error.usage_unknown=False
        error.diagnostics=agent_recon.run_metrics([])
        raise error
    monkeypatch.setattr('server.agent_worker.reconnoiter',reject)
    task=SimpleNamespace(school='Example University',college='Example College',url='https://example.edu/')
    with pytest.raises(agent_recon.ReconError):
        await worker._harvest_with_repair(task,tmp_path/'outputs')
    audit=json.loads((tmp_path/'outputs/recon-attempts.json').read_text())
    assert audit['usage_unknown'] is False
    assert audit['attempts'][0]['model_calls']==0
