"""Real browser replay of an official site's legacy global Map implementation."""
import json
import pytest
from playwright.async_api import Error, async_playwright

from server.browser_context import new_browser_context

pytestmark = pytest.mark.browser


@pytest.mark.asyncio
async def test_legacy_page_map_is_unchanged_and_tools_are_isolated(tmp_path):
    # Minimized replay of the saved official jq.js legacy Map; no site script is rewritten.
    custom_map = '''function Map() {this.keys = new Array();this.data = new Object();
        this.set = function(key,value) {if(this.data[key]==null&&this.keys.indexOf(key)==-1)this.keys.push(key);this.data[key]=value;};
        this.get = function(key) {return this.data[key];};}'''
    html = '<meta charset="utf-8"><main><p>教师完整正文保持原样。</p><a href="/one">甲</a><a href="/two">乙</a><button onclick="this.dataset.clicked=1">继续</button></main><script>' + custom_map + '</script>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        raw = await browser.new_context()
        await raw.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html'))
        bad = await raw.new_page()
        await bad.goto('https://fixture.example.edu/roster')
        # Playwright 1.47 reports 'extraEngines is not iterable'; 1.56 reports
        # 'this._engines.keys ... is not iterable'. Either way the legacy Map breaks it.
        with pytest.raises(Error, match='is not iterable'):
            await bad.locator('a').evaluate_all('nodes => nodes.map(n => ({url:n.href}))')
        await raw.close()

        context = await new_browser_context(browser)
        await context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html'))
        page = await context.new_page()
        await page.goto('https://fixture.example.edu/roster')
        assert await page.locator('a').count() == 2
        assert await page.locator('a').evaluate_all('nodes => nodes.map(n => ({url:n.href}))') == [
            {'url':'https://fixture.example.edu/one'}, {'url':'https://fixture.example.edu/two'}]
        assert await page.evaluate('() => [...document.querySelectorAll("a")].map(n => ({url:n.href}))') == [
            {'url':'https://fixture.example.edu/one'}, {'url':'https://fixture.example.edu/two'}]
        await page.locator('button').click()
        assert await page.locator('button').evaluate('n => n.dataset.clicked') == '1'
        assert await page.evaluate('() => document.querySelector("p").textContent') == '教师完整正文保持原样。'
        # Main realm is deliberately accessible here only to inspect site compatibility.
        assert await page.raw_page.evaluate('() => {const m=new Map();m.set("hello",7);return JSON.stringify({value:m.get("hello"),legacy:Map.toString().includes("this.keys")})}') == '{"value":7,"legacy":true}'
        probe = await page.evaluate('() => __facultyRecon.check("a", null)')
        assert probe['cardCount'] == 2
        from server.recon_browser_mcp import BrowserScout
        scout = BrowserScout(page, tmp_path / 'scout', phase='branch')
        verified = json.loads((await scout.call('browser_verify', {'card_selector':'a'}))['content'][0]['text'])
        assert verified['cardCount'] == 2
        await page.goto('https://fixture.example.edu/changed')
        assert (await page.evaluate('() => __facultyRecon.check("a", null)'))['url'] == 'https://fixture.example.edu/changed'
        with pytest.raises(Error):
            await page.locator('a[').evaluate_all('nodes => nodes.length')
        with pytest.raises(Error, match='exactly one'):
            await page.locator('a').evaluate('node => node.href')
        await browser.close()


@pytest.mark.asyncio
async def test_poster_capture_scroll_screenshot_and_local_engine(tmp_path):
    from server.browser_poster_tool import capture_poster
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await new_browser_context(browser)
        await context.route('**/*', lambda route: route.fulfill(
            body='<meta charset="utf-8"><div style="height:1000px">完整原文</div>', content_type='text/html'))
        engine_calls = []
        def engine(image):
            engine_calls.append(image)
            return [], None
        result = await capture_poster('https://fixture.example.edu/poster', context, tmp_path, ocr_engine=engine)
        assert result['status'] == 'agent_review_required'
        assert result['image_count'] == 0
        assert len(engine_calls) == 1 and engine_calls[0].startswith(b'\x89PNG')
        await browser.close()


@pytest.mark.asyncio
async def test_isolation_preserves_request_tracking_and_empty_dom_proof():
    from server.browser_readiness import install_request_tracking
    from server.empty_roster import verify_empty_roster
    html = '<meta charset="utf-8"><div id="roster"><p class="empty">暂无教师</p></div>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await new_browser_context(browser)
        await context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html'))
        page = await context.new_page()
        tracker = install_request_tracking(page)
        await page.goto('https://fixture.example.edu/empty')
        branch = {'url':page.url, 'card_selector':'#roster > .card', 'single_page':True,
            'empty_state_selector':'#roster > .empty', 'roster_container_selector':'#roster'}
        proof = await verify_empty_roster(page, branch, await page.content())
        assert tracker.navigation_observed and not tracker.pending
        assert proof['confirmed'] is True
        await browser.close()
