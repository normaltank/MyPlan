"""Pagination proof belongs to the source widget, including logical terminal states."""
import json

import pytest

from server.browser_batch_tool import without_banner_arrows

URL = 'https://faculty.example.edu/list.htm'
SELECTOR = '#wp_news_w7 ul > li'
PAGE = '''<div id="wp_news_w7"><ul><li>张伟</li><li>李明</li></ul></div>
<div id="wp_paging_w7"><ul class="wp_paging">
<li class="pages_count"><em class="per_count">14</em><em class="all_count">2</em></li>
<li class="page_nav"><a class="next" href="javascript:void(0)">下一页</a></li>
<li class="page_jump"><em class="curr_page">1</em><em class="all_pages">1</em></li>
</ul></div>'''


def test_one_media_carousel_is_not_a_roster_pager():
    html = ('<div id="slideBox"><div class="hd"><ul><li>1</li></ul></div>'
            '<div class="bd"><ul><li><img src="/a.png"></li></ul></div>'
            '<a class="prev" href="javascript:void(0)"></a>'
            '<a class="next" href="javascript:void(0)"></a></div>'
            '<ul id="staff"><li>张伟</li><li>李明</li></ul>')
    proof = {'total': 1, 'items': [{'selector': '#slideBox > a.next'}]}
    assert without_banner_arrows(html, '#staff > li', proof)['total'] == 0


def test_media_roster_itself_and_real_pager_are_never_ignored_as_a_banner():
    html = ('<div id="slideBox" class="pager"><ul id="staff"><li><img src="/a.png"></li></ul>'
            '<a class="prev" href="javascript:void(0)"></a>'
            '<a class="next" href="javascript:void(0)"></a></div>')
    proof = {'total': 1, 'items': [{'selector': '#slideBox > a.next'}]}
    assert without_banner_arrows(html, '#staff > li', proof)['total'] == 1


@pytest.mark.parametrize('replacement', [
    lambda page: page.replace('wp_paging_w7', 'wp_paging_w8'),
    lambda page: page.replace('class="all_count">2', 'class="all_count">3'),
    lambda page: page.replace('class="all_pages">1', 'class="all_pages">2'),
    lambda page: page.replace('<ul class="wp_paging">', '<ul class="wp_paging" style="display:none">'),
    lambda page: page + '<script>document.querySelector("a.next").onclick=()=>{document.querySelector("#wp_news_w7 ul").append(document.createElement("li"));}</script>',
    lambda page: page + '<script>document.querySelector("a.next").onclick=()=>window.open("/other");</script>',
    lambda page: page + '<script>document.querySelector("a.next").onclick=()=>setTimeout(()=>window.open("about:blank"),450);</script>',
    lambda page: page + '<script>throw new Error("pager init failed")</script>',
])
@pytest.mark.asyncio
@pytest.mark.browser
async def test_terminal_claim_rejects_wrong_binding_counts_or_actual_change(tmp_path, replacement):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.recon_browser_mcp import BrowserScout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser, polite=False)
            await context.route('**/*', lambda r: r.fulfill(body=replacement(PAGE), content_type='text/html; charset=utf-8'))
            scout = BrowserScout(await context.new_page(), tmp_path, 'branch',
                                 {'school': '测试大学', 'college': '测试学院', 'url': URL})
            await scout.call('browser_navigate', {'url': URL})
            response = await scout.call('browser_verify', {'card_selector': SELECTOR})
            reply = json.loads(response['content'][0]['text'])
            if response.get('isError'):
                assert reply['status'] == 'roster_changed_during_verification'
            else:
                assert not any(p.get('confirmed') for p in reply['pagination'].get('terminal_pagers', []))
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_failed_paging_script_cannot_turn_noop_into_terminal_proof(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.recon_browser_mcp import BrowserScout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser, polite=False)
            async def route(request):
                if request.request.url.endswith('/paging.js'):
                    await request.fulfill(status=404, body='', content_type='text/javascript')
                else:
                    await request.fulfill(body=PAGE+'<script src="/paging.js"></script>',
                                          content_type='text/html; charset=utf-8')
            await context.route('**/*', route)
            scout = BrowserScout(await context.new_page(), tmp_path, 'branch',
                                 {'school': '测试大学', 'college': '测试学院', 'url': URL})
            await scout.call('browser_navigate', {'url': URL})
            reply = json.loads((await scout.call('browser_verify', {'card_selector': SELECTOR}))['content'][0]['text'])
            assert reply['pagination']['total'] == 1
            assert reply['pagination']['terminal_pagers'][0]['reason'] == 'page_load_failed'
        finally:
            await browser.close()


def test_terminal_evidence_does_not_authorize_a_different_snapshot():
    from server.pager_evidence import scope_digest, proof_valid
    proof = {'confirmed': True, 'roster_selector': SELECTOR,
             'pager_selector': '#wp_paging_w7 .wp_paging',
             'source_sha256': scope_digest(PAGE, SELECTOR),
             'pager_sha256': scope_digest(PAGE, '#wp_paging_w7 .wp_paging')}
    assert proof_valid(proof, PAGE, SELECTOR)
    assert not proof_valid(proof, PAGE.replace('张伟', '赵强'), SELECTOR)


@pytest.mark.parametrize('change_before_probe', [False, True])
@pytest.mark.asyncio
@pytest.mark.browser
async def test_batch_capture_requires_proof_for_its_own_source_snapshot(monkeypatch, change_before_probe):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.browser_batch_tool import _capture_branch
    from server import pager_evidence

    original = pager_evidence.terminal_pagers
    async def probe(page, selector):
        if change_before_probe:
            await page.evaluate("document.querySelector('#wp_news_w7 li').textContent='赵强'")
        return await original(page, selector)
    monkeypatch.setattr(pager_evidence, 'terminal_pagers', probe)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser, polite=False)
            await context.route('**/*', lambda r: r.fulfill(body=PAGE, content_type='text/html; charset=utf-8'))
            pages, unresolved, evidence = await _capture_branch(
                context, {'url':URL, 'card_selector':SELECTOR, 'single_page':True})
            assert len(pages) == 1
            if change_before_probe:
                assert any(i['status'] == 'single_page_pagination_unverified' for i in unresolved)
            else:
                assert unresolved == []
                assert evidence['terminal_pagers'][0]['confirmed'] is True
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_counter_bound_terminal_pager_is_verified_in_a_real_browser(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.recon_browser_mcp import BrowserScout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser, polite=False)
            await context.route('**/*', lambda r: r.fulfill(body=PAGE, content_type='text/html; charset=utf-8'))
            scout = BrowserScout(await context.new_page(), tmp_path, 'branch',
                                 {'school': '测试大学', 'college': '测试学院', 'url': URL})
            await scout.call('browser_navigate', {'url': URL})
            reply = json.loads((await scout.call('browser_verify', {'card_selector': SELECTOR}))['content'][0]['text'])
            assert reply['pagination']['total'] == 0
            assert reply['pagination']['terminal_pagers'][0]['confirmed'] is True
        finally:
            await browser.close()
