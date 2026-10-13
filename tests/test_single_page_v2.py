"""A scoped terminal mechanism must survive the real scout and harvest gates."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from server.page_types import binding_for, requires_stage_evidence, resolve_type

URL = 'https://faculty.example.edu/list.htm'
SELECTOR = '#wp_news_w7 ul > li'
SOURCE = {'source_role': 'faculty_roster', 'source_container_selector': '#wp_news_w7',
          'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
BRANCH = {'url': URL, **SOURCE, 'card_selector': SELECTOR, 'single_page': True,
          'profile_content_selector': 'article', 'page_types': {'pagination': {
              'type_id': 'pagination.single_page', 'implementation_version': 2}}}
PAGE = '''<meta charset="utf-8"><title>师资队伍</title><h1>师资队伍</h1>
<header>样本大学 样本学院</header><div id="wp_news_w7"><ul>
<li><a href="/p/a">张伟</a></li><li><a href="/p/b">李明</a></li></ul></div>
<div id="wp_paging_w7"><ul class="wp_paging">
<li class="pages_count"><em class="per_count">14</em><em class="all_count">2</em></li>
<li class="page_nav"><a class="next" href="javascript:void(0)">下一页</a></li>
<li class="page_jump"><em class="curr_page">1</em><em class="all_pages">1</em></li>
</ul></div><div class="banner"><img src="/pending.png"></div>'''


def test_real_sanming_original_preserves_roster_pager_and_outside_banner():
    from bs4 import BeautifulSoup
    raw = Path('tests/fixtures/pager_scope/sanming_professor.html').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == '3254e87febbc2f103c71c9a2bb3c389358851e4f0ce6ecaf2568b2135503b9f9'
    soup = BeautifulSoup(raw.decode('utf-8'), 'html.parser')
    pairs = [(a.get_text(strip=True), a['href']) for a in soup.select('#wp_news_w6 li.list_item .Article_Title a')]
    assert pairs == [
        ('徐桂兰', '/smxyszb/2023/0307/c4775a134520/page.htm'),
        ('邹晓芟', '/smxyszb/2019/0315/c4775a144827/page.htm'),
        ('欧阳秀敏', '/smxyszb/2025/0108/c4775a163695/page.htm'),
        ('范连生', '/smxyszb/2020/0718/c4775a144828/page.htm'),
        ('钟卫华', '/smxyszb/2019/0409/c4775a78094/page.htm'),
        ('许勋恩', '/smxyszb/2017/0707/c4775a183107/page.htm')]
    pager = soup.select_one('#wp_paging_w6 .wp_paging')
    assert pager.select_one('.all_pages').get_text(strip=True) == '1'
    assert pager.select_one('.pages_count em.all_count').get_text(strip=True) == '6'
    pending = soup.select_one('#featuredw99 img[src$="bdace7db-f76c-41bd-a6a8-c79f74068def.jpeg"]')
    assert pending is not None
    assert soup.select_one('#wp_news_w6') not in pending.parents and pager not in pending.parents


def test_v2_has_no_legacy_exemption_or_implicit_plan_upgrade():
    legacy = {k: v for k, v in BRANCH.items() if k != 'page_types'}
    assert binding_for(legacy, 'pagination').implementation_version == 1
    assert not requires_stage_evidence('pagination', legacy)
    assert requires_stage_evidence('pagination', BRANCH)
    definition = resolve_type(BRANCH['page_types']['pagination'], 'pagination')
    for extra in ({'initial_click_selector': '.tab'}, {'kind': 'image_table'},
                  {'empty_state_selector': '.empty'}, {'next_selector': '.next'}):
        assert not definition.applicability({**BRANCH, **extra})


def test_stage_digest_binds_terminal_and_capture_dependencies(tmp_path):
    from server import page_types
    ref = BRANCH['page_types']['pagination']
    definition = resolve_type(ref, 'pagination')
    sources = page_types._definition_sources(definition)
    assert set(definition.source_dependencies) <= set(sources)
    for name, path in sources.items():
        destination = tmp_path / name
        destination.parent.mkdir(exist_ok=True, parents=True)
        destination.write_bytes(path.read_bytes())
    expected = page_types.implementation_digest(ref, 'pagination')
    assert page_types.implementation_digest(ref, 'pagination', root=tmp_path / 'server') == expected
    for dependency in definition.source_dependencies:
        destination = tmp_path / dependency
        original = destination.read_bytes()
        destination.write_bytes(original + b'\n# independently changed dependency\n')
        assert page_types.implementation_digest(ref, 'pagination', root=tmp_path / 'server') != expected
        destination.write_bytes(original)
    (tmp_path / definition.source_dependencies[0]).unlink()
    with pytest.raises(FileNotFoundError):
        page_types.implementation_digest(ref, 'pagination', root=tmp_path / 'server')


@pytest.mark.browser
@pytest.mark.asyncio
async def test_pending_banner_passes_actual_stage_submit_harvest_and_cache(tmp_path):
    import asyncio
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import run_plan
    from server.recon_browser_mcp import BrowserScout
    from server.recon_plan_cache import validate_cached_plan
    release = asyncio.Event()
    people = {'a': '张伟', 'b': '李明'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def serve(route):
            if route.request.url.endswith('/pending.png'):
                await release.wait()
                await route.fulfill(body='', content_type='image/png')
            elif '/p/' in route.request.url:
                name = people[route.request.url.rsplit('/', 1)[1]]
                await route.fulfill(body=f'<article><h1>{name}</h1><p>职称：教授</p>'
                    '<p>工作单位：样本大学样本学院</p><p>教学研究和完整任职履历。</p></article>',
                    content_type='text/html; charset=utf-8')
            else:
                await route.fulfill(body=PAGE, content_type='text/html; charset=utf-8')
        await context.route('**/*', serve)
        try:
            await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf-8'))
            scout = BrowserScout(await context.new_page(), tmp_path / 'scout', 'branch',
                {'school': '样本大学', 'college': '样本学院', 'url': URL})
            await scout.call('browser_navigate', {'url': URL})
            source = json.loads((await scout.call('browser_verify_source',
                {**SOURCE, 'unit_selector': SELECTOR}))['content'][0]['text'])
            assert source['confirmed'], source
            roster = json.loads((await scout.call('browser_verify',
                {'card_selector': SELECTOR}))['content'][0]['text'])
            assert not roster['pagination_termination']['confirmed']  # @1 still refuses.
            refused = json.loads((await scout.call('submit_plan', {'plan': BRANCH}))['content'][0]['text'])
            assert not refused['accepted']  # No upgrade by copying the version label.
            await scout.call('browser_verify_profile', {'profile_content_selector': 'article',
                'urls': [URL.rsplit('/', 1)[0] + '/p/' + key for key in people]})
            stage = await scout.call('browser_verify_stage', {'role': 'pagination', 'branch': BRANCH})
            assert not stage.get('isError'), stage
            accepted = json.loads((await scout.call('submit_plan', {'plan': BRANCH}))['content'][0]['text'])
            assert accepted['accepted'], accepted
            plan = {'school': '样本大学', 'college': '样本学院', 'branches': [copy.deepcopy(BRANCH)]}
            result = await run_plan(plan, context, tmp_path / 'capture', task_id='scoped-pager')
            assert result['delivery_ready'], result['unresolved']
            assert result['total'] == 2
            stage_proof = result['coverage']['branches'][0]['stage_evidence']
            assert stage_proof['implementation_version'] == 2
            rows = json.loads(Path(result['records_path']).read_text(encoding='utf-8'))
            assert {row['姓名'] for row in rows} == set(people.values())
            assert all(row['职称'] == '教授' and '完整任职履历' in row['简介'] for row in rows)
            fresh = await validate_cached_plan({'plan': plan}, context, tmp_path / 'cache', verify_discovery=False)
            assert fresh['valid'] and fresh['model_calls'] == 0, fresh
        finally:
            release.set()
            await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['inside', 'widget', 'nested', 'pager', 'unbound', 'fetch', 'click'])
async def test_scoped_terminal_rejects_related_or_unbound_pending_and_actual_change(change):
    import asyncio
    from playwright.async_api import async_playwright
    from server.browser_readiness import install_request_tracking
    from server.pager_evidence import scoped_terminal_pagers
    html = PAGE
    image = '<img src="/pending.png">'
    if change in {'inside', 'widget', 'nested', 'pager'}:
        if change == 'nested':
            html = html.replace('<ul>\n<li>', '<div id="wp_news_nested"><ul>\n<li>')
            html = html.replace('</li></ul></div>', '</li></ul></div></div>')
        html = html.replace(image, '')
        anchor = {'inside': '张伟</a>', 'widget': '<div id="wp_news_w7">',
                  'nested': '<div id="wp_news_w7">', 'pager': '<ul class="wp_paging">'}[change]
        html = html.replace(anchor, anchor + image)
    elif change == 'unbound':
        html = html.replace(image, '<link rel="preload" as="image" href="/pending.png">')
    elif change == 'fetch':
        html += '<script>fetch("/pending-data")</script>'
    else:
        html += '<script>document.querySelector("a.next").onclick=()=>{' \
                'document.querySelector("#wp_news_w7 li").textContent="赵强"}</script>'
    release = asyncio.Event()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def serve(route):
            if 'pending' in route.request.url:
                await release.wait()
                await route.fulfill(body='', content_type='image/png')
            else:
                await route.fulfill(body=html, content_type='text/html; charset=utf-8')
        await context.route('**/*', serve)
        try:
            page = await context.new_page()
            install_request_tracking(page)
            await page.goto(URL, wait_until='domcontentloaded')
            [proof] = await scoped_terminal_pagers(page, SELECTOR)
            assert not proof['confirmed']
        finally:
            release.set()
            await browser.close()
