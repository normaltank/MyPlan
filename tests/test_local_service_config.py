"""Web and worker share the same configured local endpoint."""
import json

import pytest


def settings_tree(tmp_path, local_port=None):
    server = tmp_path / 'server'
    server.mkdir()
    (server / 'config.json').write_text(json.dumps({'host': '127.0.0.1', 'port': 8000}), encoding='utf-8')
    if local_port is not None:
        runtime = tmp_path / '.runtime'
        runtime.mkdir()
        (runtime / 'local-services.json').write_text(json.dumps({'port': local_port}), encoding='utf-8')
    return tmp_path


def test_local_port_override_keeps_web_and_callback_on_the_same_endpoint(tmp_path):
    from server.local_service_config import load_web_config, local_web_url

    root = settings_tree(tmp_path, 18000)
    config = load_web_config(root)
    assert config == {'host': '127.0.0.1', 'port': 18000}
    assert local_web_url(root) == 'http://127.0.0.1:18000'


def test_default_endpoint_is_unchanged_without_local_settings(tmp_path):
    from server.local_service_config import load_web_config, local_web_url

    root = settings_tree(tmp_path)
    assert load_web_config(root)['port'] == 8000
    assert local_web_url(root) == 'http://127.0.0.1:8000'


@pytest.mark.parametrize('port', [0, 65536, True, 18000.5, '18000'])
def test_invalid_local_port_is_rejected_before_launch(tmp_path, port):
    from server.local_service_config import load_web_config

    with pytest.raises(ValueError, match='port'):
        load_web_config(settings_tree(tmp_path, port))


@pytest.mark.parametrize('host,lan,shown', [
    ('0.0.0.0', '192.168.1.5', '同事访问地址:    http://192.168.1.5:18000'),
    ('0.0.0.0', None, '未找到本机局域网地址'),
    ('127.0.0.1', '192.168.1.5', '当前只允许本机访问'),
])
def test_web_launcher_uses_override_for_browser_listener_and_instructions(monkeypatch, capsys, host, lan, shown):
    from server import local_service_config, start_server

    monkeypatch.setattr(local_service_config, 'load_web_config', lambda: {'host': host, 'port': 18000})
    monkeypatch.setattr(local_service_config, 'local_web_url', lambda: 'http://127.0.0.1:18000')
    monkeypatch.setattr(start_server, 'lan_address', lambda: lan)
    application = object()
    opened, listeners = [], []
    monkeypatch.setattr(start_server, 'load_web_app', lambda: application)
    monkeypatch.setattr(start_server, 'port_is_open', lambda port: False)
    monkeypatch.setattr(start_server.webbrowser, 'open', opened.append)
    monkeypatch.setattr(start_server.uvicorn, 'run', lambda app, **kwargs: listeners.append((app, kwargs)))

    start_server.main()

    assert opened == ['http://127.0.0.1:18000']
    assert listeners == [(application, {'host': host, 'port': 18000})]
    output = capsys.readouterr().out
    assert shown in output
    assert 'HTTPS' not in output and '管理员' not in output
    assert ':8000' not in output


@pytest.mark.parametrize('explicit', [None, 'http://127.0.0.1:19000'])
def test_sync_cli_uses_local_config_and_preserves_explicit_endpoint(monkeypatch, explicit):
    import sys
    from server import browser_batch_sync, local_service_config

    monkeypatch.setattr(local_service_config, 'local_web_url', lambda: 'http://127.0.0.1:18000')
    argv = ['sync', '--audit', 'unused.json', '--task-id', 't', '--worker-id', 'w']
    if explicit:
        argv += ['--base-url', explicit]
    monkeypatch.setattr(sys, 'argv', argv)
    endpoints = []

    async def capture(args):
        endpoints.append(args.base_url)
        return {'status': 'ok'}

    monkeypatch.setattr(browser_batch_sync, '_run_cli', capture)
    browser_batch_sync.main()
    assert endpoints == [explicit or 'http://127.0.0.1:18000']
