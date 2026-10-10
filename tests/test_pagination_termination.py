"""Local Chromium proves completion independently of candidate counts."""
import json

import pytest
from playwright.async_api import async_playwright

from server.browser_batch_tool import _capture_branch
from server.agent_recon import selector_evidence_issues
from server.browser_context import IsolatedPage
from server.recon_browser_mcp import BrowserScout

pytestmark = [pytest.mark.browser, pytest.mark.verified_source_boundary]
URL = "https://fixture.example.edu/roster"
ROSTER = "<meta charset='utf-8'><main><ul class='roster'><li>Record A</li></ul>{}</main>"
APPEND = "document.querySelector('.roster').insertAdjacentHTML('beforeend','<li>Record B</li>');this.remove()"


@pytest.mark.asyncio
@pytest.mark.parametrize("control", [
    f'<input id="advance" type="button" value="下一页" onclick="{APPEND}">',
    f'<button id="advance" onclick="{APPEND}">更多人员</button>',
])
async def test_unmarked_forward_control_blocks_single_page_and_preserves_source(tmp_path, control):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("**/*", lambda route: route.fulfill(body=ROSTER.format(control), content_type="text/html"))
        page = IsolatedPage(await context.new_page(), context)
        scout = BrowserScout(page, tmp_path, "branch", {"school": "Fixture", "college": "Department", "url": URL})
        try:
            await scout.call("browser_navigate", {"url": URL})
            reply = await scout.call("browser_verify", {"card_selector": ".roster > li"})
            observed = json.loads(reply["content"][0]["text"])
            submission = await scout.call("submit_plan", {"plan": {
                "url": URL, "card_selector": ".roster > li", "single_page": True}})
            assert json.loads(submission["content"][0]["text"])["accepted"] is False
            assert observed["pagination_termination"]["confirmed"] is False
            # An incomplete heuristic result must not overrule actual controls.
            for event in scout.events:
                if event.get('type') != 'tool/result':
                    continue
                for block in (event['data'].get('message') or {}).get('content', []):
                    for content in block.get('content', []):
                        if content.get('type') == 'text':
                            value = json.loads(content['text'])
                            if isinstance(value, dict) and 'cardCount' in value:
                                value['pagination'] = {'total': 0, 'items': []}
                                content['text'] = json.dumps(value)
            assert selector_evidence_issues({'branches': [{
                'url': URL, 'card_selector': '.roster > li', 'single_page': True}]}, scout.events)
            assert await page.locator(".roster > li").count() == 1
            await scout.call("browser_click", {"selector": "#advance"})
            assert await page.locator(".roster > li").count() == 2
            pages, errors, evidence = await _capture_branch(context, {
                "url": URL, "card_selector": ".roster > li", "single_page": True})
            assert len(pages) == 1 and control in pages[0][1]
            assert any(item["status"] == "single_page_pagination_unverified" for item in errors)
            assert evidence["pagination_termination"]["confirmed"] is False
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('control', ['', '<button>搜索</button>',
    '<input type="button" value="下一页" disabled>', '<nav class="pagination"><button disabled>2</button></nav>'])
async def test_normal_single_page_and_native_disabled_terminal_remain_supported(tmp_path, control):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route('**/*', lambda route: route.fulfill(body=ROSTER.format(control), content_type='text/html'))
        page = IsolatedPage(await context.new_page(), context)
        scout = BrowserScout(page, tmp_path, 'branch', {'school': 'Fixture', 'college': 'Department', 'url': URL})
        try:
            await scout.call('browser_navigate', {'url': URL})
            reply = await scout.call('browser_verify', {'card_selector': '.roster > li'})
            assert json.loads(reply['content'][0]['text'])['pagination_termination']['confirmed'] is True
            submission = await scout.call('submit_plan', {'plan': {
                'url': URL, 'card_selector': '.roster > li', 'single_page': True}})
            assert json.loads(submission['content'][0]['text'])['accepted'] is True
            pages, errors, evidence = await _capture_branch(context, {
                'url': URL, 'card_selector': '.roster > li', 'single_page': True})
            assert len(pages) == 1 and not errors
            assert evidence['pagination_termination']['confirmed'] is True
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_observed_dynamic_next_is_usable_without_heuristic_candidate(tmp_path):
    control = f'<button id="advance" onclick="{APPEND}">Continue records</button>'
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route('**/*', lambda route: route.fulfill(body=ROSTER.format(control), content_type='text/html'))
        page = IsolatedPage(await context.new_page(), context)
        scout = BrowserScout(page, tmp_path, 'branch', {'school': 'Fixture', 'college': 'Department', 'url': URL})
        try:
            await scout.call('browser_navigate', {'url': URL})
            reply = await scout.call('browser_verify', {'card_selector': '.roster > li', 'next_selector': '#advance'})
            value = json.loads(reply['content'][0]['text'])
            assert value['pagination']['total'] == 0 and value['pagination_termination']['confirmed'] is False
            await scout.call('browser_click', {'selector': '#advance'})
            await scout.call('browser_verify', {'card_selector': '.roster > li'})
            submission = await scout.call('submit_plan', {'plan': {
                'url': URL, 'card_selector': '.roster > li', 'next_selector': '#advance'}})
            assert json.loads(submission['content'][0]['text'])['accepted'] is True
            pages, errors, evidence = await _capture_branch(context, {
                'url': URL, 'card_selector': '.roster > li', 'next_selector': '#advance'})
            assert len(pages) == 2 and not errors and evidence['end'] == 'next_control_absent'
            assert 'Record B' in pages[1][1]
        finally:
            await browser.close()
