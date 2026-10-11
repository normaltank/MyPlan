"""Actual browser sessions reclassify only independently proved non-pagers."""
import copy
import json

import pytest
from playwright.async_api import async_playwright

from server.agent_recon import selector_evidence_issues
from server.browser_context import IsolatedPage
from server.nonpagination_controls import nonpagination_evidence
from server.recon_browser_mcp import BrowserScout

pytestmark = [pytest.mark.browser, pytest.mark.verified_source_boundary]
URL = 'https://fixture.example.edu/roster'
CARDS = '.roster > li'
ROSTER = '<main><ul class="roster"><li>王芳</li><li>李华</li></ul></main>'
BANNER = ('<div id="slideBox" class="slideBox"><div class="hd"><ul><li></li></ul></div>'
          '<div class="bd"><ul><li><img src="/banner.png"></li></ul></div>'
          '<a class="prev" href="javascript:void(0)"></a><a class="next" href="javascript:void(0)"></a></div>')
NAV = '<nav><a href="/roster">师资目录</a></nav>'


async def _reply(scout, args):
    return json.loads((await scout.call('browser_verify', args))['content'][0]['text'])


@pytest.mark.asyncio
@pytest.mark.parametrize('control,selector', [
    (BANNER, '#slideBox > a.next'),
    (NAV, 'nav a'),
    ('<div><a href="/">首页</a> &gt;&gt; <a id="self" href="/roster">师资目录</a></div>', '#self'),
    ('<div><img src="/icon.png"><a href="/">首页</a> &gt;&gt; <a id="self" href="/roster">师资目录</a></div>', '#self'),
    ('<dl><dt>师资栏目</dt><dd><a href="/other">其它栏目</a></dd><dd><a class="cur" href="/roster">当前名录</a></dd></dl>', 'a.cur'),
])
async def test_native_nonpaging_guesses_do_not_poison_single_page_submission(tmp_path, control, selector):
    html = '<meta charset="utf-8">' + ROSTER + control
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context()
        await ctx.route('**/*', lambda r:r.fulfill(body=html,content_type='text/html; charset=utf-8'))
        scout = BrowserScout(IsolatedPage(await ctx.new_page(), ctx), tmp_path, 'branch',
            {'school':'Fixture','college':'Department','url':URL})
        try:
            await scout.call('browser_navigate', {'url':URL})
            await _reply(scout, {'card_selector':CARDS,'next_selector':selector})
            await scout.call('browser_navigate', {'url':URL})
            final = await _reply(scout, {'card_selector':CARDS})
            assert final['pagination']['total'] == 0
            assert final['pagination_termination']['confirmed'] is True
            [remembered] = final['pagination_termination']['observed_forward_controls']
            assert remembered['count'] > 0 and remembered['terminal'] is False
            assert remembered['nonpagination']['source_sha256'] == final['pagination_termination']['source_sha256']
            branch = {'url':URL,'card_selector':CARDS,'single_page':True}
            submitted = json.loads((await scout.call('submit_plan',{'plan':branch}))['content'][0]['text'])
            assert submitted['accepted'] is True, submitted
            # An old proof copied into a new source observation does not discharge the remembered control.
            bad_events = copy.deepcopy(scout.events)
            for event in bad_events:
                if event.get('type') != 'tool/result':
                    continue
                for block in event.get('data',{}).get('message',{}).get('content',[]):
                    for content in block.get('content',[]):
                        if content.get('type') != 'text':
                            continue
                        value = json.loads(content['text'])
                        for item in value.get('pagination_termination',{}).get('observed_forward_controls',[]):
                            if item.get('nonpagination'):
                                item['nonpagination']['source_sha256'] = '0' * 64
                        content['text'] = json.dumps(value)
            assert selector_evidence_issues({'branches':[branch]},bad_events)
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['real_button','same_url_ajax','near_banner_data','mixed','body_link'])
async def test_unproved_or_roster_controls_remain_blocked_when_selector_is_omitted(tmp_path, kind):
    append = "document.querySelector('.roster').insertAdjacentHTML('beforeend','<li>张明</li>')"
    control = f'<button id="advance" onclick="{append}">Continue records</button>'
    selector = '#advance'
    html = ROSTER + control
    if kind == 'same_url_ajax':
        html = ROSTER + f'<nav><a id="advance" href="/roster" onclick="{append};return false">当前名录</a></nav>'
    elif kind == 'near_banner_data':
        html = '<div id="slideBox" class="slideBox">' + ROSTER + '<img src="/a.png"><img src="/b.png">' + control + '</div>'
    elif kind == 'mixed':
        html = ROSTER + BANNER + control
        selector = '#slideBox > a.next, #advance'
    elif kind == 'body_link':
        html = ROSTER + '<p>正文里的相关说明 <a id="advance" href="/roster">当前名录</a></p>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context()
        await ctx.route('**/*', lambda r:r.fulfill(body=html,content_type='text/html; charset=utf-8'))
        scout = BrowserScout(IsolatedPage(await ctx.new_page(), ctx), tmp_path, 'branch',
            {'school':'Fixture','college':'Department','url':URL})
        try:
            await scout.call('browser_navigate',{'url':URL})
            await _reply(scout,{'card_selector':CARDS,'next_selector':selector})
            final = await _reply(scout,{'card_selector':CARDS})
            assert final['pagination_termination']['confirmed'] is False
            assert not final['pagination_termination']['observed_forward_controls'][0].get('nonpagination')
            if kind in {'real_button','same_url_ajax'}:
                await scout.call('browser_click',{'selector':'#advance'})
                assert await scout.page.locator(CARDS).count() == 3
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_external_ajax_listener_cannot_supply_current_navigation_proof(tmp_path):
    html = ('<meta charset="utf-8">' + ROSTER + '<nav><a id="advance" href="/roster">当前名录</a></nav>'
            '<script>document.querySelector("#advance").addEventListener("click",event=>{'
            'event.preventDefault();document.querySelector(".roster").insertAdjacentHTML('
            '"beforeend","<li>张明</li>");});</script>')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context()
        await ctx.route('**/*', lambda r:r.fulfill(body=html,content_type='text/html; charset=utf-8'))
        scout = BrowserScout(IsolatedPage(await ctx.new_page(), ctx), tmp_path, 'branch',
            {'school':'Fixture','college':'Department','url':URL})
        try:
            await scout.call('browser_navigate', {'url':URL})
            await _reply(scout, {'card_selector':CARDS,'next_selector':'#advance'})
            await scout.call('browser_click', {'selector':'#advance'})
            assert await scout.page.locator(CARDS).count() >= 3
            final = await _reply(scout, {'card_selector':CARDS})
            assert final['pagination_termination']['confirmed'] is False
            assert not final['pagination_termination']['observed_forward_controls'][0].get('nonpagination')
            submitted = json.loads((await scout.call('submit_plan', {'plan':
                {'url':URL,'card_selector':CARDS,'single_page':True}}))['content'][0]['text'])
            assert submitted['accepted'] is False
        finally:
            await browser.close()


@pytest.mark.parametrize('control', [
    '<article><nav><a id="self" href="/roster">当前名录</a></nav></article>',
    '<nav><a id="self" href="/roster" rel="next">下一页</a></nav>',
    '<div class="pager"><a id="self" href="/roster">当前名录</a></div>',
    '<nav class="pagerNav"><a id="self" href="/roster">当前名录</a></nav>',
    '<nav><a id="self" href="/other">当前名录</a></nav>',
    '<div><img src="/person.png" alt="教师简介"><a href="/">首页</a> &gt;&gt; <a id="self" href="/roster">当前名录</a></div>',
    '<div><a href="/">首页</a><img src="/data.png"> &gt;&gt; <a id="self" href="/roster">当前名录</a></div>',
])
def test_current_navigation_proof_rejects_article_forward_pager_and_different_uri(control):
    assert nonpagination_evidence(ROSTER + control,URL,CARDS,'#self') is None


def test_current_uri_and_navigation_markup_alone_do_not_prove_nonpagination():
    assert nonpagination_evidence(ROSTER + NAV, URL, CARDS, 'nav a') is None
