import json

import pytest

from server.browser_context import new_browser_context
from server import local_service_config


class Browser:
    async def new_context(self, **kwargs):
        self.options = kwargs
        return object()


@pytest.mark.asyncio
async def test_configured_browser_proxy_reaches_context(monkeypatch):
    monkeypatch.setenv('HARVEST_BROWSER_PROXY','http://127.0.0.1:7897')
    monkeypatch.setenv('HARVEST_BROWSER_PROXY_BYPASS','.example.edu,localhost')
    browser=Browser()
    await new_browser_context(browser,locale='zh-CN')
    assert browser.options['proxy']=={'server':'http://127.0.0.1:7897','bypass':'.example.edu,localhost'}
    assert browser.options['locale']=='zh-CN'


@pytest.mark.asyncio
async def test_offline_context_and_explicit_proxy_are_not_reconfigured(monkeypatch):
    monkeypatch.setenv('HARVEST_BROWSER_PROXY','http://127.0.0.1:7897')
    browser=Browser()
    await new_browser_context(browser,polite=False)
    assert 'proxy' not in browser.options
    explicit={'server':'http://127.0.0.1:4567'}
    await new_browser_context(browser,proxy=explicit)
    assert browser.options['proxy']==explicit


def project(tmp_path,proxy):
    (tmp_path/'server').mkdir()
    (tmp_path/'.runtime').mkdir()
    (tmp_path/'server/config.json').write_text(json.dumps({'port':8000}),encoding='utf-8')
    (tmp_path/'.runtime/local-services.json').write_text(json.dumps({'port':18000,'browser_proxy':proxy}),encoding='utf-8')
    return tmp_path


def test_local_proxy_configuration_is_separate_from_web_endpoint(tmp_path,monkeypatch):
    monkeypatch.delenv('HARVEST_BROWSER_PROXY',raising=False)
    root=project(tmp_path,{'server':'http://127.0.0.1:7897','bypass':'.example.edu'})
    assert local_service_config.browser_proxy_options(root)=={'server':'http://127.0.0.1:7897','bypass':'.example.edu'}
    assert local_service_config.local_web_url(root)=='http://127.0.0.1:18000'


@pytest.mark.parametrize('proxy',[{'server':'javascript:alert(1)'},{'server':'http://user:secret@localhost:7897'},
                                  {'server':'http://localhost:7897','bypass':'a\nb'},True])
def test_bad_transport_configuration_fails_before_launcher_restart(tmp_path,monkeypatch,proxy):
    monkeypatch.delenv('HARVEST_BROWSER_PROXY',raising=False)
    root=project(tmp_path,proxy)
    with pytest.raises(ValueError,match='browser proxy') as error:
        local_service_config.load_web_config(root)
    assert 'secret' not in str(error.value)
