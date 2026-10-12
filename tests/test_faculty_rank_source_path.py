"""A rank is a faculty category only when the current path proves its parent."""
from pathlib import Path

import pytest

from server.source_qualification import witness

TARGET = {'school': '三明学院', 'college': '马克思主义学院'}
URL = 'https://www.fjsmu.edu.cn/smxyszb/js/list.htm'
BRANCH = {'url': URL, 'source_role': 'faculty_roster',
    'source_container_selector': 'ul.wp_article_list', 'role_evidence_selector': 'ul.col_metas',
    'source_context_selector': '#footer'}


def test_real_rank_category_uses_its_current_faculty_parent():
    html = (Path(__file__).parent / 'fixtures/source_rank_path/sanming_professors.html').read_text(encoding='utf8')
    proof = witness(html, URL, BRANCH, TARGET)
    assert proof['title_bound'] and proof['college_bound']
    assert proof['role_supported']


def test_real_teaching_unit_under_current_fulltime_faculty_path():
    url = 'https://www.fjsmu.edu.cn/smxyszb/mkszyjbyljys/list.htm'
    html = (Path(__file__).parent / 'fixtures/source_rank_path/sanming_teaching_unit.html').read_text(encoding='utf8')
    branch = {**BRANCH, 'url': url, 'source_container_selector': '#wp_news_w6',
              'role_evidence_selector': 'li.col_path', 'source_context_selector': 'div.foot.clearfix'}
    assert witness(html, url, branch, TARGET)['role_supported']
    # The same teaching-unit name under institutional organization is not a faculty proof.
    assert not witness(html.replace('>专任教师<', '>组织机构<'), url, branch, TARGET)['role_supported']


@pytest.mark.parametrize('label,parent,href,accepted', [
    ('思想政治教育系', '专任教师', '/group', True),
    ('基础理论教研室', '教师名单', '/group', True),
    ('基础理论教研室', '组织机构', '/group', False),
    ('基础理论教研室', '学院新闻', '/group', False),
    ('基础理论教研室活动', '专任教师', '/group', False),
    ('教授讲座', '师资队伍', '/group', False),
    ('行政人员', '师资队伍', '/group', False),
    ('思想政治教育系', '专任教师', '/other-group', False),
])
def test_faculty_unit_path_requires_current_group_and_immediate_faculty_parent(label, parent, href, accepted):
    url = 'https://example.edu/group'
    html = (f'<title>{label}</title><footer id="footer">三明学院马克思主义学院</footer>'
        f'<ul class="col_metas"><span>当前位置：</span><a href="/faculty">{parent}</a>'
        f'<a href="{href}">{label}</a></ul><ul class="wp_article_list"><li>官方人员</li></ul>')
    assert witness(html, url, {**BRANCH, 'url': url}, TARGET)['role_supported'] is accepted


@pytest.mark.parametrize('path', [
    '<span>当前位置：</span><a href="/faculty">师资队伍</a><a href="/news">新闻</a><a href="/ranks">教授</a>',
    '<a href="/faculty">师资队伍</a><a href="/ranks">教授</a>',
    '<span>当前位置：</span><a href="/committee">组织机构</a><a href="/ranks">教授</a>',
    '<span>当前位置：</span><a href="/faculty">师资队伍</a><a href="/events">教授讲座</a>',
    '<span>当前位置：</span><a href="https://other.edu.cn/faculty">师资队伍</a><a href="/ranks">教授</a>',
    '<span>当前位置：</span><a href="/faculty">师资队伍</a><a href="/ranks">教授</a>',
])
def test_rank_word_and_sibling_menu_do_not_supply_faculty_membership(path):
    html = ('<title>教授</title><footer id="footer">三明学院马克思主义学院</footer>'
            f'<ul class="col_metas">{path}</ul><ul class="wp_article_list"><li>官方人员</li></ul>')
    assert not witness(html, URL, BRANCH, TARGET)['role_supported']


def test_current_rank_path_still_requires_independent_target_branding():
    html = (Path(__file__).parent / 'fixtures/source_rank_path/sanming_professors.html').read_text(encoding='utf8')
    assert not witness(html, URL, BRANCH, {'school': '三明学院', 'college': '外国语学院'})['role_supported']


@pytest.mark.parametrize('title,accepted', [
    ('教授-马克思主义学院-三明学院', True),
    ('教授-三明学院-马克思主义学院', True),
    ('教授-外国语学院-三明学院', False),
    ('教授-马克思主义学院-曾就读三明学院', False),
    ('三明学院马克思主义学院教授讲座', False),
])
def test_document_signature_is_exact_branding_not_an_arbitrary_university_mention(title, accepted):
    html = (f'<title>{title}</title><footer id="footer"><img src="/logo.png"></footer>'
        '<ul class="col_metas"><span>当前位置：</span><a href="/faculty">师资队伍</a>'
        '<a href="/smxyszb/js/list.htm">教授</a></ul><ul class="wp_article_list"><li>官方人员</li></ul>')
    assert witness(html, URL, BRANCH, TARGET)['role_supported'] is accepted


def test_title_signature_does_not_qualify_an_unrelated_source_or_a_body_context():
    html = ('<title>教授-三明学院-马克思主义学院</title><footer id="footer">学校联系信息</footer>'
        '<ul class="col_metas"><span>当前位置：</span><a href="/news">学院新闻</a>'
        '<a href="/smxyszb/js/list.htm">教授</a></ul><ul class="wp_article_list"><li>三明学院马克思主义学院</li></ul>')
    assert not witness(html, URL, BRANCH, TARGET)['role_supported']
    html = html.replace('学院新闻', '师资队伍')
    assert not witness(html, URL, {**BRANCH, 'source_context_selector': 'ul.wp_article_list'}, TARGET)['role_supported']


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('inside_source,accepted', [(False, True), (True, False)])
async def test_live_source_checks_selected_witnesses_and_binds_final_original(inside_source, accepted):
    from playwright.async_api import async_playwright
    from server.browser_readiness import install_request_tracking
    from server.source_qualification import captured_source_evidence, verify_source, digest

    html = ('<title>教师名单</title><header>三明学院马克思主义学院</header><h1>教师名单</h1>'
        '<main><p>正式教师名单与本人介绍可供读取。</p></main><aside id="clock"></aside>'
        '<script>setInterval(()=>document.querySelector("#clock").textContent=Date.now(),1)</script>')
    if inside_source:
        html = html.replace('</main><aside id="clock"></aside>', '<p id="clock"></p></main>')
    branch = {**BRANCH, 'source_container_selector': 'main', 'role_evidence_selector': 'h1',
              'source_context_selector': 'header'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        install_request_tracking(page)
        await page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        await page.goto(URL)
        captured = {}
        proof = await verify_source(page, branch, TARGET, local_capture=captured)
        assert proof['confirmed'] is accepted
        assert proof['source_html_sha256'] == digest(captured['html'])
        assert (captured_source_evidence(captured['html'], URL, branch, TARGET, proof).status == 'verified') is accepted
        await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['hide_source', 'change_purpose', 'pending_request'])
async def test_final_capture_rejects_external_visibility_purpose_or_request_changes(monkeypatch, change):
    import asyncio
    from playwright.async_api import async_playwright
    from server.browser_readiness import install_request_tracking
    from server.source_qualification import verify_source

    organization = change == 'change_purpose'
    purpose = '组织机构' if organization else '教师名单'
    html = (f'<title>{purpose}</title><header>三明学院马克思主义学院</header><h1>{purpose}</h1>'
        f'<h2>{purpose}</h2><main><p>完整官方人员名单。</p></main>')
    branch = {**BRANCH, 'source_container_selector': 'main', 'role_evidence_selector': 'h2',
        'source_context_selector': 'header', 'source_role': 'organizational_membership' if organization else 'faculty_roster'}
    release = asyncio.Event()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        install_request_tracking(page)
        async def serve(route):
            if route.request.url.endswith('/hold'):
                await release.wait()
            await route.fulfill(body=html, content_type='text/html; charset=utf-8')
        await page.route('**/*', serve)
        await page.goto(URL)
        title = page.title
        async def change_after_initial_checks():
            current = await title()
            scripts = {'hide_source': 'const style=document.createElement("style");style.textContent="main{display:none}";document.head.append(style)',
                'change_purpose': 'document.querySelector("h1").textContent="师资队伍"',
                'pending_request': 'fetch("/hold")'}
            await page.evaluate('() => {' + scripts[change] + '; return true;}')
            if change == 'pending_request':
                await asyncio.sleep(.05)
            return current
        monkeypatch.setattr(page, 'title', change_after_initial_checks)
        try:
            proof = await verify_source(page, branch, TARGET)
            assert not proof['confirmed']
            if organization:
                assert not proof['non_faculty_purpose']
            if change == 'pending_request':
                receipt = proof['source_request_evidence']
                assert receipt['final_non_image_activity_delta'] > 0
                assert (URL.split('/smxyszb')[0] + '/hold', 'fetch') in receipt['final_pending_requests']
                assert not receipt['final_network_idle']
        finally:
            release.set()
            await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_repeated_navigation_retains_the_real_failed_request_receipt():
    from playwright.async_api import async_playwright
    from server.browser_readiness import install_request_tracking
    from server.source_qualification import verify_source

    html = ('<title>教师名单</title><header>三明学院马克思主义学院</header><h1>教师名单</h1>'
        '<main><p>完整官方人员名单。</p></main><script src="/missing.js"></script>')
    branch = {**BRANCH, 'source_container_selector': 'main', 'role_evidence_selector': 'h1',
              'source_context_selector': 'header'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        install_request_tracking(page)
        async def serve(route):
            if route.request.url.endswith('/missing.js'):
                await route.abort('failed')
            else:
                await route.fulfill(body=html, content_type='text/html; charset=utf-8')
        await page.route('**/*', serve)
        await page.goto(URL)
        first = await verify_source(page, branch, TARGET)
        assert not first['confirmed']
        await page.goto(URL)
        second = await verify_source(page, branch, TARGET)
        assert second['confirmed']
        receipt = second['source_request_evidence']
        assert receipt['current_failures'] and receipt['previous_navigation_failures']
        assert receipt['server_rendered_content'] is True
        assert receipt['main_document_url'] == URL and receipt['main_document_status'] == 200
        await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('hidden,accepted', [('invisible', True), ('王芳', False)])
async def test_final_source_visibility_preserves_trivial_placeholder_boundary(hidden, accepted):
    from playwright.async_api import async_playwright
    from server.browser_readiness import install_request_tracking
    from server.source_qualification import verify_source

    html = ('<title>教师名单</title><header>三明学院马克思主义学院</header><h1>教师名单</h1>'
        f'<main><p>完整官方人员名单。</p><span style="display:none">{hidden}</span></main>')
    branch = {**BRANCH, 'source_container_selector': 'main', 'role_evidence_selector': 'h1',
              'source_context_selector': 'header'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        install_request_tracking(page)
        await page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        await page.goto(URL)
        proof = await verify_source(page, branch, TARGET)
        assert proof['confirmed'] is accepted
        await browser.close()
