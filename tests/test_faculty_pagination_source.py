"""A rank continuation needs live reciprocal pager and faculty-root evidence."""
import hashlib
import json
from pathlib import Path

import pytest

from server.source_qualification import SOURCE_FIELDS, captured_source_evidence, read_source_packet, verify_source

ROOT = 'https://example.edu/ranks'
URL = 'https://example.edu/ranks/2'
TARGET = {'school': '样本大学', 'college': '样本学院'}
BRANCH = {'url': URL, 'source_role': 'faculty_roster', 'source_container_selector': '.roster',
          'role_evidence_selector': '.position', 'source_context_selector': 'header'}


def page_html(url, link, label='教授', college='样本学院'):
    return (f'<title>{label}</title><header>样本大学{college}</header><section>'
        '<div class="position">当前位置：<a href="/faculty">师资队伍</a>'
        f'<a href="{ROOT}">{label}</a></div><div class="roster"><p>正式人员名单</p></div>'
        f'<nav class="pagination"><a href="{link}">下一页</a><span>{url}</span></nav></section>')


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('variant,accepted', [
    ('valid', True), ('no_root_link', False), ('no_child_link', False), ('other_college', False),
    ('sidebar_pager', False), ('hidden_parent_link', False), ('root_unreadable', False),
    ('news_parent', False), ('hidden_current_roster', False),
    ('adjacent_root_sidebar', False), ('adjacent_current_sidebar', False), ('adjacent_root_menu', False),
    ('root_menu_tag', False), ('current_menu_tag', False), ('root_menu_role', False),
    ('root_pager_menu_class', False), ('outside_clock', True),
])
async def test_bounded_native_pagination_relationship_preserves_source_boundaries(tmp_path, variant, accepted):
    from playwright.async_api import async_playwright
    from server.recon_browser_mcp import BrowserScout

    current, root = page_html(URL, ROOT), page_html(ROOT, URL)
    if variant == 'no_root_link':
        current = current.replace(f'<a href="{ROOT}">下一页', '<a href="/unrelated">下一页')
    if variant == 'no_child_link':
        root = root.replace(f'<a href="{URL}">下一页', '<a href="/unrelated">下一页')
    if variant == 'other_college':
        root = root.replace('样本大学样本学院', '样本大学另一学院')
    if variant == 'sidebar_pager':
        root = root.replace('<nav class="pagination">', '</section><aside><nav class="pagination">')
        root = root.replace('</nav></section>', '</nav></aside>')
    if variant == 'adjacent_root_sidebar':
        root = root.replace('<nav class="pagination">', '<aside><nav class="pagination">')
        root = root.replace('</nav></section>', '</nav></aside></section>')
    if variant == 'adjacent_current_sidebar':
        current = current.replace('<nav class="pagination">', '<aside><nav class="pagination">')
        current = current.replace('</nav></section>', '</nav></aside></section>')
    if variant == 'adjacent_root_menu':
        root = root.replace('<nav class="pagination">', '<div class="menu"><nav class="pagination">')
        root = root.replace('</nav></section>', '</nav></div></section>')
    if variant == 'root_menu_tag':
        root = root.replace('<nav ', '<menu ').replace('</nav>', '</menu>')
    if variant == 'current_menu_tag':
        current = current.replace('<nav ', '<menu ').replace('</nav>', '</menu>')
    if variant == 'root_menu_role':
        root = root.replace('<nav ', '<nav role="menu" ')
    if variant == 'root_pager_menu_class':
        root = root.replace('class="pagination"', 'class="pagination menu"')
    if variant == 'outside_clock':
        clock = ('<p id="clock"></p><script>setInterval(()=>document.querySelector("#clock")'
                 '.textContent=Date.now(),1)</script>')
        root += clock
        current += clock
    if variant == 'hidden_parent_link':
        root = root.replace(f'<a href="{URL}">下一页', f'<a style="display:none" href="{URL}">下一页')
    if variant == 'news_parent':
        root = root.replace('>师资队伍<', '>学院新闻<')
    if variant == 'hidden_current_roster':
        current = current.replace('class="roster"', 'class="roster" style="display:none"')
    urls = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(service_workers='block')
        async def serve(route):
            urls.append(route.request.url)
            await route.fulfill(body=root if route.request.url == ROOT else current,
                status=503 if variant == 'root_unreadable' and route.request.url == ROOT else 200,
                content_type='text/html; charset=utf-8')
        await context.route('**/*', serve)
        page = await context.new_page()
        scout = BrowserScout(page, tmp_path / 'proof', 'discovery', {**TARGET, 'url': URL})
        await page.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf8'))
        await scout.call('browser_navigate', {'url': URL})
        packet = json.loads((await scout.call('browser_verify_source', {
            key: BRANCH[key] for key in SOURCE_FIELDS}))['content'][0]['text'])
        proof = read_source_packet(packet)
        decision = json.loads((await scout.call('submit_plan', {
            'plan': {**TARGET, 'branches': [BRANCH]}}))['content'][0]['text'])
        assert proof['confirmed'] is accepted and decision['accepted'] is accepted
        assert urls.count(ROOT) <= 1
        original = Path(proof['original_path']).read_text(encoding='utf8')
        assert (captured_source_evidence(original, URL, BRANCH, TARGET, packet).status == 'verified') is accepted
        if accepted:
            relation = proof['pagination_relationship']
            root_path = Path(relation['root_original_path'])
            assert hashlib.sha256(root_path.read_bytes()).hexdigest() == relation['root_source_proof']['source_html_sha256']
            assert not captured_source_evidence(original, URL + '?other', BRANCH, TARGET, proof).status == 'verified'
            stale = {**proof, 'pagination_relationship': {**relation, 'root_url': 'https://other.edu/ranks'}}
            assert captured_source_evidence(original, URL, BRANCH, TARGET, stale).status == 'unknown'
        await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('hidden_page', [ROOT, URL])
async def test_late_cssom_link_hiding_is_in_the_final_source_snapshot(tmp_path, monkeypatch, hidden_page):
    from playwright.async_api import async_playwright
    from server.browser_readiness import install_request_tracking
    import server.source_qualification as qualification
    here = qualification._verify_source_here
    calls = {}
    async def late_hide(page, *args, **kwargs):
        calls[page.url] = calls.get(page.url, 0) + 1
        if page.url == hidden_page and calls[page.url] == 2:
            before = await page.content()
            await page.evaluate("document.styleSheets[0].insertRule('nav.pagination {display:none}',0)")
            assert await page.content() == before
        return await here(page, *args, **kwargs)
    monkeypatch.setattr(qualification, '_verify_source_here', late_hide)
    originals = {ROOT: page_html(ROOT, URL) + '<style></style>', URL: page_html(URL, ROOT) + '<style></style>'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        await context.route('**/*', lambda route: route.fulfill(body=originals[route.request.url],
            content_type='text/html; charset=utf-8'))
        page = await context.new_page()
        install_request_tracking(page)
        await page.goto(URL)
        captured = {}
        proof = await verify_source(page, BRANCH, TARGET, local_capture=captured)
        assert not proof['confirmed']
        assert captured_source_evidence(captured['html'], URL, BRANCH, TARGET, proof).status == 'unknown'
        await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_saved_hunan_professor_pagination_qualifies_with_full_bound_path(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_readiness import install_request_tracking
    root, url = 'http://ls.hunnu.edu.cn/szdw/js.htm', 'http://ls.hunnu.edu.cn/szdw/js/1.htm'
    fixtures = Path(__file__).parent / 'fixtures/source_rank_path/hunan'
    originals = {root: (fixtures / 'professors_first.html').read_bytes(),
                 url: (fixtures / 'professors_second.html').read_bytes()}
    assert hashlib.sha256(originals[root]).hexdigest() == 'eb91c247af2d64de454c0313aadf378f780865f57e46d21c24bc1288570ff8f3'
    assert hashlib.sha256(originals[url]).hexdigest() == '6fa8c62bc9a323859279a6182818dc8d6f9fdeb6142c8ed6cc7af8ce3a598d2f'
    target = {'school': '湖南师范大学', 'college': '历史文化学院'}
    branch = {**BRANCH, 'url': url, 'source_container_selector': 'div.dis-list',
        'card_selector': 'div.dis-list > a', 'role_evidence_selector': '.main-list-tille > div',
        'source_context_selector': '.header-wz, .footer-xz2'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(service_workers='block')
        await context.route('**/*', lambda route: route.fulfill(body=originals.get(route.request.url, b''),
            content_type='text/html; charset=utf-8' if route.request.url in originals else 'text/plain'))
        page = await context.new_page()
        install_request_tracking(page)
        await page.goto(url)
        captured = {}
        proof = await verify_source(page, branch, target, local_capture=captured)
        assert proof['confirmed'], proof
        assert captured_source_evidence(captured['html'], url, branch, target, proof).status == 'verified'
        assert len(captured['related_sources']) == 1 and captured['related_sources'][0]['url'] == root
        await browser.close()
