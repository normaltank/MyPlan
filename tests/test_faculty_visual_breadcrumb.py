"""A current faculty/group breadcrumb need not use the words 当前位置."""
import json
from pathlib import Path

import pytest

from server.source_qualification import witness

URL = 'https://wgy.czu.edu.cn/szdw/swyyx.htm'
TARGET = {'school': '池州学院', 'college': '外国语学院', 'url': URL}
CLAIM = {'url': URL, 'source_role': 'faculty_roster', 'source_container_selector': '.main_conR',
         'role_evidence_selector': '.main_titT', 'source_context_selector': '.logo, .footWrap'}
ORIGINAL = Path(__file__).parent / 'fixtures/source_rank_path/chizhou_business_english.html'


def test_saved_department_roster_uses_its_visible_current_breadcrumb():
    proof = witness(ORIGINAL.read_text(encoding='utf-8'), URL, CLAIM, TARGET)
    assert proof['college_bound'] and proof['title_bound'] and proof['current_link_bound']
    assert proof['role_supported']
    assert proof['faculty_group_path']['faculty_parent_url'] == 'https://wgy.czu.edu.cn/szdw/yyx1.htm'
    assert proof['faculty_group_path']['root_url'] == URL


PATH = '<a href="/index.htm">首页</a>&gt;<a href="/faculty">师资队伍</a>&gt;<a href="/group">商务英语系</a>'


@pytest.mark.parametrize('path,title,wrapper,accepted', [
    (PATH, '商务英语系', 'div', True),
    (PATH.replace('&gt;', ''), '商务英语系', 'div', False),
    (PATH.replace('师资队伍', '组织机构'), '商务英语系', 'div', False),
    (PATH.replace('/faculty', 'https://other.edu/faculty'), '商务英语系', 'div', False),
    (PATH.replace('/group', '/other-group'), '商务英语系', 'div', False),
    (PATH, '学院新闻', 'div', False),
    (PATH, '商务英语系', 'aside', False),
    (PATH, '商务英语系', 'div class="sidebar"', False),
    (PATH, '商务英语系', 'div class="menu"', False),
    (PATH, '商务英语系', 'nav role="menu"', False),
    (PATH, '商务英语系', 'nav aria-label="面包屑"', True),
    (PATH.replace('/index.htm', 'https://@wgy.czu.edu.cn/index.htm'), '商务英语系', 'div', False),
    (PATH.replace('/index.htm', 'https://wgy.czu.edu.cn:bad/index.htm'), '商务英语系', 'div', False),
    (PATH.replace('/index.htm', 'https://wgy.czu.edu.cn:99999/index.htm'), '商务英语系', 'div', False),
    (PATH.replace('首页', '学院栏目'), '商务英语系', 'div', False),
    (PATH.replace('商务英语系', '教授讲座'), '教授讲座', 'div', False),
    (PATH.replace('商务英语系', '行政人员'), '行政人员', 'div', False),
    (PATH.replace('</a>&gt;<a href="/group">', '</a><a href="/news">新闻</a>&gt;<a href="/group">'),
     '商务英语系', 'div', False),
])
def test_visual_path_requires_ordered_current_links_and_faculty_parent(path, title, wrapper, accepted):
    url = 'https://wgy.czu.edu.cn/group'
    html = (f'<title>{title}-外国语学院</title><footer>池州学院外国语学院</footer>'
            f'<{wrapper}><div class="path">{path}</div></{wrapper.split()[0]}><main><li>官方名单</li></main>')
    claim = {**CLAIM, 'url': url, 'source_container_selector': 'main',
             'role_evidence_selector': '.path', 'source_context_selector': 'footer'}
    assert witness(html, url, claim, TARGET)['role_supported'] is accepted


@pytest.mark.parametrize('layout_class', ['has-sidebar', 'menu-enabled', 'sidebar-layout'])
def test_page_layout_with_a_sibling_sidebar_does_not_retire_its_current_path(layout_class):
    url = 'https://wgy.czu.edu.cn/group'
    html = (f'<html><head><title>商务英语系-外国语学院</title></head><body class="{layout_class}">'
            '<aside>侧栏菜单</aside><section>'
            f'<div class="path">{PATH}</div><main><li>官方名单</li></main></section>'
            '<footer>池州学院外国语学院</footer></body></html>')
    claim = {**CLAIM, 'url': url, 'source_container_selector': 'main',
             'role_evidence_selector': '.path', 'source_context_selector': 'footer'}
    assert witness(html, url, claim, TARGET)['role_supported']


@pytest.mark.browser
@pytest.mark.asyncio
async def test_saved_visual_breadcrumb_passes_actual_source_and_submission(tmp_path):
    from playwright.async_api import async_playwright
    from server.recon_browser_mcp import BrowserScout

    html = ORIGINAL.read_text(encoding='utf-8')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def serve(route):
            if route.request.url == URL:
                await route.fulfill(content_type='text/html; charset=utf-8', body=html)
            elif route.request.resource_type == 'image':
                await route.fulfill(content_type='image/svg+xml', body=(
                    '<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20"/></svg>'))
            else:
                await route.fulfill(content_type='text/css' if route.request.resource_type == 'stylesheet'
                                    else 'application/javascript', body='')
        await context.route('**/*', serve)
        try:
            page = await context.new_page()
            await page.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf-8'))
            scout = BrowserScout(page, tmp_path, 'discovery', TARGET)
            await scout.call('browser_navigate', {'url': URL})
            result = await scout.call('browser_verify_source', {k: v for k, v in CLAIM.items() if k != 'url'})
            proof = json.loads(result['content'][0]['text'])
            assert proof['confirmed'], {k: proof.get(k) for k in (
                'reason', 'role_supported', 'college_bound', 'witness_visible', 'network_idle',
                'source_request_evidence')}
            assert proof['role_supported']
            result = await scout.call('submit_plan', {'plan': {
                'school': TARGET['school'], 'college': TARGET['college'], 'branches': [CLAIM]}})
            assert json.loads(result['content'][0]['text'])['accepted'] is True
        finally:
            await browser.close()
