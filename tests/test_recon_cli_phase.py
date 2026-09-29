"""MCP stage isolation must survive a host filtering child environment variables."""
import asyncio,json,os,subprocess,sys,threading,uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
import yaml
from server.agent_recon import write_runtime_patch


def test_runtime_patch_passes_stage_and_namespace_as_process_arguments(tmp_path):
    path=tmp_path/'runtime.yml'
    write_runtime_patch(path,phase='discovery',session_id='test-discover')
    entries=yaml.safe_load(path.read_text(encoding='utf-8'))
    entries=[child for entry in entries for child in entry.get('insert',[entry])]
    mcp=next(entry['config'] for entry in entries if entry.get('config',{}).get('serverName')=='playwright')
    assert mcp['args']==['-m','server.recon_browser_mcp','--phase','discovery','--session','test-discover']


@pytest.mark.asyncio
async def test_actual_mcp_process_honors_cli_stage_and_saves_discovery_namespace():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type','text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(b'<nav><a href="/faculty">Faculty directory</a></nav>')
        def log_message(self,*_args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    sid='test-cli-'+uuid.uuid4().hex+'-discover'
    env={**os.environ,'FACULTY_RECON_PHASE':'full','FACULTY_RECON_SESSION':'standalone','PYTHONIOENCODING':'utf-8'}
    process=subprocess.Popen([sys.executable,'-m','server.recon_browser_mcp','--phase','discovery','--session',sid],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',env=env)
    async def request(index,method,params=None):
        process.stdin.write(json.dumps({'jsonrpc':'2.0','id':index,'method':method,'params':params or {}})+'\n')
        process.stdin.flush()
        text=await asyncio.wait_for(asyncio.to_thread(process.stdout.readline),30)
        assert text, 'MCP child unexpectedly exited'
        return json.loads(text)['result']
    try:
        await request(1,'initialize')
        names={tool['name'] for tool in (await request(2,'tools/list'))['tools']}
        assert 'browser_navigation' in names and 'browser_verify' not in names
        result=await request(3,'tools/call',{'name':'browser_navigate','arguments':{'url':f'http://127.0.0.1:{server.server_port}/'}})
        assert not result['isError']
        root=Path(__file__).parents[1]/'server/outputs/recon-evidence'/sid
        evidence=list(root.glob('*/*.discovery.json'))
        assert evidence
        saved=json.loads(evidence[0].read_text(encoding='utf-8'))
        assert set(saved)=={'url','links','fingerprint'} and saved['links']
        denied=await request(4,'tools/call',{'name':'browser_verify','arguments':{'card_selector':'main li'}})
        assert denied['isError']
    finally:
        process.stdin.close()
        await asyncio.wait_for(asyncio.to_thread(process.wait),15)
        process.stdout.close()
        process.stderr.close()
        server.shutdown()
        server.server_close()
