"""Native navigation proofs with real source qualification and whole-plan gates."""
import base64
import json

import pytest
from playwright.async_api import async_playwright

from server.browser_context import new_browser_context
from server.nonpagination_controls import nonpagination_evidence_valid
from server.recon_browser_mcp import BrowserScout

pytestmark = pytest.mark.browser
URL = 'https://fixture.example.edu/roster'
CARDS = '.roster > li'
CLAIM = {'source_role':'faculty_roster', 'source_container_selector':'main',
         'role_evidence_selector':'h1', 'source_context_selector':'header'}
BASE = ('<meta charset="utf-8"><title>教师名录</title><header>样本大学样本学院</header>'
        '<h1>教师名录</h1><main><ul class="roster"><li>王芳</li><li>李华</li></ul></main>')


def _value(reply):
    return json.loads(reply['content'][0]['text'])


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,accepted', [
    ('normal_listener', True), ('hover_dropdown', True), ('hidden_without_expansion', False),
    ('external_ajax', False), ('delegated_ajax', False), ('no_document_navigation', False),
    ('history_only', False), ('same_count_changed_card', False), ('same_count_changed_image', False),
    ('same_count_hidden_card', False),
])
async def test_current_navigation_requires_document_response_and_unchanged_complete_roster(tmp_path, kind, accepted):
    control = '<nav><a id="advance" href="/roster">当前名录</a></nav>'
    script = ''
    if kind in {'hover_dropdown', 'hidden_without_expansion'}:
        control = ('<nav><ul><li class="menu"><a href="/other">栏目</a>'
                   '<dl style="display: none;"><dd><a id="advance" href="/roster">当前名录</a></dd></dl>'
                   '</li></ul></nav>')
        if kind == 'hover_dropdown':
            script = ('document.querySelector(".menu").addEventListener("mouseenter",()=>{'
                      'document.querySelector("dl").style.display="block";});')
    elif kind == 'normal_listener':
        script = 'document.querySelector("#advance").addEventListener("click",()=>{});'
    elif kind in {'external_ajax', 'delegated_ajax'}:
        node = 'document' if kind == 'delegated_ajax' else 'document.querySelector("#advance")'
        script = (node + '.addEventListener("click",event=>{if(!event.target.closest("#advance"))return;'
                  'event.preventDefault();document.querySelector(".roster").insertAdjacentHTML('
                  '"beforeend","<li>张明</li>");});')
    elif kind in {'no_document_navigation', 'history_only'}:
        action = 'history.pushState({},"",location.href);' if kind == 'history_only' else ''
        script = ('document.querySelector("#advance").addEventListener("click",event=>{'
                  'event.preventDefault();' + action + '});')
    html = BASE + control + '<script>' + script + '</script>'
    if kind == 'same_count_changed_image':
        html = html.replace('<li>王芳</li>', '<li>王芳<img src="/a.png"></li>')
    navigations = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await new_browser_context(browser, polite=False)

        async def route(r):
            nonlocal navigations
            if not r.request.is_navigation_request():
                if r.request.url.endswith('.png'):
                    await r.fulfill(body=base64.b64decode(
                        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aR3sAAAAASUVORK5CYII='),
                        content_type='image/png')
                else:
                    await r.abort()
                return
            navigations += 1
            current = html
            if navigations > 1 and kind == 'same_count_changed_card':
                current = current.replace('王芳', '赵东')
            elif navigations > 1 and kind == 'same_count_hidden_card':
                current += '<style>.roster li:last-child{display:none}</style>'
            elif navigations > 1 and kind == 'same_count_changed_image':
                current = current.replace('/a.png', '/b.png')
            await r.fulfill(body=current, content_type='text/html; charset=utf-8')

        await ctx.route('**/*', route)
        scout = BrowserScout(await ctx.new_page(), tmp_path, 'branch',
            {'school':'样本大学', 'college':'样本学院', 'url':URL})
        try:
            await scout.call('browser_navigate', {'url':URL})
            first_reply = await scout.call('browser_verify', {'card_selector':CARDS,'next_selector':'#advance'})
            first = _value(first_reply)
            if kind == 'same_count_hidden_card':
                assert first_reply.get('isError') is True
                assert first['hiddenCardCount'] == 1 and first['status'] == 'roster_cards_not_visible'
                assert first.get('pagination_termination',{}).get('confirmed') is not True
                source = _value(await scout.call('browser_verify_source', {**CLAIM,'unit_selector':CARDS}))
                assert source['confirmed'] is False
                submitted = _value(await scout.call('submit_plan', {'plan':
                    {'url':URL,'card_selector':CARDS,'single_page':True,**CLAIM}}))
                assert submitted['accepted'] is False
                return
            final = _value(await scout.call('browser_verify', {'card_selector':CARDS}))
            assert final['pagination_termination']['confirmed'] is accepted
            [item] = final['pagination_termination']['observed_forward_controls']
            if accepted:
                assert navigations >= 3
                assert item['nonpagination']['controls'][0]['activation'] == 'same_url_document_navigation'
                assert item['nonpagination']['source_sha256'] == final['pagination_termination']['source_sha256']
                native = item['nonpagination']['controls'][0]
                activation = native.pop('activation')
                assert not nonpagination_evidence_valid(item, URL, CARDS,
                    final['pagination_termination']['source_sha256'])
                native['activation'] = activation
            else:
                assert not item.get('nonpagination')
                if kind in {'external_ajax', 'delegated_ajax'}:
                    assert first['cardCount'] == 3 and final['cardCount'] == 3
                    await scout.call('browser_click', {'selector':'#advance'})
                    assert await scout.page.locator(CARDS).count() == 4
            source = _value(await scout.call('browser_verify_source', {**CLAIM,'unit_selector':CARDS}))
            assert source['confirmed'] is True, source
            submitted = _value(await scout.call('submit_plan', {'plan':
                {'url':URL,'card_selector':CARDS,'single_page':True,**CLAIM}}))
            assert submitted['accepted'] is accepted, submitted
        finally:
            await browser.close()
