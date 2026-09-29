import asyncio
import hashlib
import json

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from server.browser_readiness import install_request_tracking
from server.profile_structure import verify_profile_container
from server.harvest_workflow import capture_profiles

pytestmark = pytest.mark.browser

URL = 'https://scope.example.edu/p'
BODY = '<aside id="outside">页面轮播</aside><article id="text"><p id="bio">完整正文只能保留在本地。</p></article>'
OUTSIDE = '<script>let n=1;setInterval(()=>document.querySelector("#outside").style.opacity=String(++n/100000),5)</script>'


async def setup_page(browser, html):
    context = await browser.new_context()
    await context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
    page = await context.new_page()
    install_request_tracking(page)
    await page.goto(URL)
    return context, page


@pytest.mark.asyncio
async def test_external_carousel_mutations_do_not_reject_unchanged_complete_profile():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        _, page = await setup_page(browser, BODY + OUTSIDE)
        proof = await verify_profile_container(page, '#text')
        assert proof['confirmed'] and proof['dom_stable'] and proof['network_idle']
        assert '完整正文' not in json.dumps(proof, ensure_ascii=False)
        assert 'html' not in proof
        await browser.close()


@pytest.mark.asyncio
async def test_selected_content_mutations_remain_blocked():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        script = '<script>let n=0;setInterval(()=>document.querySelector("#bio").textContent="正文变化"+(++n),5)</script>'
        _, page = await setup_page(browser, BODY + script)
        proof = await verify_profile_container(page, '#text')
        assert not proof['confirmed'] and not proof['dom_stable']
        await browser.close()


@pytest.mark.asyncio
async def test_selected_css_animation_is_blocked_even_when_markup_is_unchanged():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        css = '<style>@keyframes pulse{from{opacity:0.9}to{opacity:0.6}} #bio{animation:pulse 1s linear infinite}</style>'
        _, page = await setup_page(browser, css + BODY)
        proof = await verify_profile_container(page, '#text')
        assert not proof['confirmed']
        assert proof['selected_dom_stable'] and not proof['visibility_stable']
        await browser.close()


@pytest.mark.asyncio
async def test_parent_opacity_animation_is_blocked_while_sibling_animation_is_allowed():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        css = '<style>@keyframes fade{0%,100%{opacity:1}50%{opacity:0}} .animated{animation:fade 0.3s linear infinite}</style>'
        _, page = await setup_page(browser, css + '<main class="animated">' + BODY + '</main>')
        proof = await verify_profile_container(page, '#text')
        assert not proof['confirmed'] and proof['active_animation_count'] > 0
        _, page = await setup_page(browser, css + BODY.replace('id="outside"', 'id="outside" class="animated"'))
        proof = await verify_profile_container(page, '#text')
        assert proof['confirmed'] and proof['active_animation_count'] == 0
        await browser.close()


@pytest.mark.asyncio
async def test_capture_profiles_retains_exact_local_proof_snapshot_despite_external_animation():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context, _ = await setup_page(browser, BODY + OUTSIDE)
        evidence = {}
        pages, issues = await capture_profiles(context, [URL], 1,
            profile_content_selectors={URL: {('#text', None)}}, profile_content_evidence=evidence)
        assert not issues and evidence[URL]['confirmed']
        assert evidence[URL]['html_sha256'] == hashlib.sha256(pages[URL].encode()).hexdigest()
        assert '完整正文' not in json.dumps(evidence, ensure_ascii=False)
        await browser.close()


@pytest.mark.asyncio
async def test_failed_proof_retains_compact_negative_diagnostics_and_bound_local_html():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context, _ = await setup_page(browser, '<style>#bio{display:none}</style>' + BODY)
        evidence = {}
        pages, issues = await capture_profiles(context, [URL], 1,
            profile_content_selectors={URL: {('#text', None)}}, profile_content_evidence=evidence)
        assert not evidence and pages[URL]
        issue = next(item for item in issues if item['status'] == 'profile_content_browser_unverified')
        negative = issue['browser_evidence']
        assert not negative['confirmed'] and negative['hidden_content_count'] > 0
        assert negative['html_sha256'] == hashlib.sha256(pages[URL].encode()).hexdigest()
        assert '完整正文' not in json.dumps(negative, ensure_ascii=False)
        await browser.close()


@pytest.mark.asyncio
async def test_missing_image_outside_profile_does_not_block_complete_text():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def route(request):
            if request.request.url.endswith('missing.png'):
                await request.fulfill(status=404, body='')
            else:
                await request.fulfill(body=BODY + '<img src="/missing.png">', content_type='text/html; charset=utf-8')
        await context.route('**/*', route)
        evidence = {}
        _, issues = await capture_profiles(context, [URL], 1,
            profile_content_selectors={URL: {('#text', None)}}, profile_content_evidence=evidence)
        assert not issues and evidence[URL]['confirmed'] and evidence[URL]['network_idle']
        assert evidence[URL]['failed_request_count'] > 0
        assert evidence[URL]['ignored_external_image_failures'] > 0
        await browser.close()


@pytest.mark.asyncio
async def test_missing_image_inside_profile_still_blocks_delivery():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def route(request):
            if request.request.url.endswith('missing.png'):
                await request.fulfill(status=404, body='')
            else:
                html = BODY.replace('</article>', '<img src="/missing.png"></article>')
                await request.fulfill(body=html, content_type='text/html; charset=utf-8')
        await context.route('**/*', route)
        evidence = {}
        _, issues = await capture_profiles(context, [URL], 1,
            profile_content_selectors={URL: {('#text', None)}}, profile_content_evidence=evidence)
        negative = next(item['browser_evidence'] for item in issues if item['status'] == 'profile_content_browser_unverified')
        assert not evidence and not negative['network_idle'] and negative['failed_request_count'] > 0
        await browser.close()


@pytest.mark.asyncio
async def test_pending_external_image_does_not_block_complete_profile():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def route(request):
            if request.request.url.endswith('hanging.png'):
                await asyncio.sleep(30)
            else:
                await request.fulfill(body=BODY + '<img src="/hanging.png">',
                                       content_type='text/html; charset=utf-8')
        await context.route('**/*', route)
        evidence = {}
        _, issues = await capture_profiles(context, [URL], 1,
            profile_content_selectors={URL: {('#text', None)}},
            profile_content_evidence=evidence)
        assert not issues and evidence[URL]['confirmed'] and evidence[URL]['network_idle']
        assert evidence[URL]['ignored_external_image_pending'] > 0
        await browser.close()


@pytest.mark.asyncio
async def test_networkidle_heuristic_timeout_still_collects_independent_profile_proof(monkeypatch):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        _, page = await setup_page(browser, BODY)

        async def timed_out(*args, **kwargs):
            raise PlaywrightTimeoutError('networkidle timeout')

        monkeypatch.setattr(page, 'wait_for_load_state', timed_out)
        proof = await verify_profile_container(page, '#text')
        assert proof['container_count'] == 1
        assert proof['confirmed'] and proof['network_idle'] and proof['dom_stable']
        assert 'html_sha256' in proof and 'error_type' not in proof
        await browser.close()


@pytest.mark.asyncio
async def test_nested_single_child_profile_selectors_resolve_to_same_content():
    html = '<article id="outer"><div id="inner"><p>完整正文</p></div></article>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context, _ = await setup_page(browser, html)
        evidence = {}
        pages, issues = await capture_profiles(context, [URL], 1,
            profile_content_selectors={URL: {('#outer', None), ('#outer > #inner', None)}},
            profile_content_evidence=evidence)
        assert pages[URL] and not issues
        assert evidence[URL]['confirmed']
        assert evidence[URL]['profile_content_selector'] == '#outer > #inner'
        assert len(evidence[URL]['equivalent_profile_content_selectors']) == 2
        await browser.close()


@pytest.mark.asyncio
async def test_conflicting_profile_selectors_with_extra_sibling_remain_blocked():
    html = '<article id="outer"><div id="inner"><p>正文</p></div><p>额外内容</p></article>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context, _ = await setup_page(browser, html)
        evidence = {}
        _, issues = await capture_profiles(context, [URL], 1,
            profile_content_selectors={URL: {('#outer', None), ('#inner', None)}},
            profile_content_evidence=evidence)
        assert not evidence
        assert any(item['status'] == 'profile_content_selector_conflict' for item in issues)
        await browser.close()


@pytest.mark.asyncio
async def test_scoped_compound_selectors_for_same_node_are_accepted():
    html = '<div id="root"><div class="content"><p>正文</p></div></div>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context, _ = await setup_page(browser, html)
        evidence = {}
        pages, issues = await capture_profiles(context, [URL], 1,
            profile_content_selectors={URL: {('#root > .content', None), ('.content', None)}},
            profile_content_evidence=evidence)
        assert pages[URL] and not issues and evidence[URL]['confirmed']
        assert evidence[URL]['profile_content_selector'] == '#root > .content'
        await browser.close()
