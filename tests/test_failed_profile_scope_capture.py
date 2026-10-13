"""A rejected profile scope remains original evidence, not teacher data input."""
import pytest
from pathlib import Path

from server.browser_batch_tool import _scope_captured_profiles
from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline


@pytest.mark.parametrize('selector,html,status', [
    ('div.wp_articlecontent', '<title>统一身份认证</title><main><p>登录验证码</p><img src="/captcha"></main>',
     'profile_content_selector_missing'),
    ('article', '<article hidden>错误的老师履历和模板邮箱 wrong@example.edu</article>',
     'profile_content_selector_hidden'),
    ('article', '<article>甲老师资料</article><article>乙老师资料</article>',
     'profile_content_selector_multiple'),
])
def test_failed_scope_cannot_donate_page_text_or_media(selector, html, status):
    url = 'https://example.edu/p'
    originals = {url: html}
    scoped, evidence, issues = _scope_captured_profiles(originals, {url: {(selector, None)}}, {}, {})
    assert originals[url] == html
    assert not scoped[url] and not evidence
    assert issues[0]['status'] == status
    result = FacultyHarvestPipeline.harvest_candidates([FacultyCandidate('王芳', url)],
        lambda target: scoped.get(target), base_url='https://example.edu/list', school='U', college='C',
        unverified_profile_urls={url})
    assert len(result['source_records']) == 1 and result['source_records'][0]['姓名'] == '王芳'
    assert not any(row['简介'] or row['邮箱'] or row['职称'] for row in result['source_records'])
    assert any(item['status'] == 'profile_content_not_verified' for item in result['unresolved'])
    assert not any(item['status'].startswith('fetch_failed') for item in result['unresolved'])
    assert not result['delivery_ready']


def test_valid_profile_subtree_still_keeps_complete_biography():
    url = 'https://example.edu/p'
    html = '<header>模板资料</header><article><h1>王芳</h1><p>完整教师履历。</p><p>科研工作。</p></article>'
    scoped, evidence, issues = _scope_captured_profiles({url: html}, {url: {('article', None)}}, {}, {})
    assert not issues and '完整教师履历' in scoped[url] and '科研工作' in scoped[url]
    assert '模板资料' not in scoped[url] and evidence[url]


@pytest.mark.parametrize('selectors,status', [
    ({('article', None), ('#profile', None)}, 'profile_content_selector_conflict'),
    (set(), 'profile_content_selector_invalid'),
])
def test_conflicting_or_missing_bindings_do_not_fall_back_to_entire_page(selectors, status):
    url = 'https://example.edu/p'
    html = '<body><main>王芳\n登录模板文本被当作简介。<img src="/captcha"></main></body>'
    original = {url: html}
    scoped, evidence, issues = _scope_captured_profiles(original, {url: selectors}, {}, {})
    assert original[url] == html and not scoped[url] and not evidence
    assert issues[0]['status'] == status
    result = FacultyHarvestPipeline.harvest_candidates([FacultyCandidate('王芳', url)],
        lambda target: scoped.get(target), base_url='https://example.edu/list',
        unverified_profile_urls={issue['url'] for issue in issues})
    assert len(result['source_records']) == 1 and result['source_records'][0]['简介'] == ''
    assert not result['delivery_ready']


@pytest.mark.browser
@pytest.mark.asyncio
async def test_browser_rejected_hidden_body_never_becomes_biography(tmp_path):
    import json
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import run_plan
    url = 'https://example.edu/list'
    roster = ('<title>教师名单</title><header>样本大学样本学院</header><h1>教师名单</h1>'
              '<main><ul><li><a href="/p">王芳</a>教授</li></ul></main>')
    profile = ('<title>王芳</title><style>#profile{display:none}</style><h1>王芳</h1>'
               '<article id="profile">浏览器隐藏的模板内容不应作为简介。</article>')
    plan = {'school': '样本大学', 'college': '样本学院', 'branches': [{
        'url': url, 'source_role': 'faculty_roster', 'source_container_selector': 'main',
        'role_evidence_selector': 'h1', 'source_context_selector': 'header',
        'card_selector': 'main li', 'single_page': True, 'profile_content_selector': '#profile'}]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        await context.route('**/*', lambda route: route.fulfill(body=(
            roster if route.request.url == url else profile), content_type='text/html; charset=utf-8'))
        report = await run_plan(plan, context, tmp_path / 'out')
        await browser.close()
    rows = json.loads(Path(report['records_path']).read_text(encoding='utf8'))
    assert len(rows) == 1 and rows[0]['姓名'] == '王芳' and rows[0]['职称'] == '教授'
    assert rows[0]['简介'] == '' and rows[0]['邮箱'] == ''
    assert not report['delivery_ready']
    assert any(issue['status'] == 'profile_content_browser_unverified' for issue in report['unresolved'])
    assert report['coverage']['field_sources'][0]['profile_fields_unread'] is True
