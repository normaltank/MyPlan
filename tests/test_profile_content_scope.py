import hashlib
import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from server.profile_content import scope_profile_html
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters


HTML = '<html><body><header>模板联系方式 template@example.edu</header><main id="profile"><h1>张富有</h1><div>教授</div><section><p>第一段完整履历。</p><p>第二段科研成果。</p><p>邮箱 zhang@example.edu</p></section></main><footer>模板页脚</footer></body></html>'


def test_verified_scope_preserves_every_selected_node_and_excludes_template():
    scoped, evidence, issue = scope_profile_html(HTML, '#profile', 'https://example.edu/p')
    assert issue is None
    expected = str(BeautifulSoup(HTML, 'html.parser').select_one('#profile'))
    assert expected in scoped
    assert '模板' not in scoped and '第二段科研成果' in scoped
    assert evidence['profile_content_selector'] == '#profile'
    assert evidence['original_html_sha256'] == hashlib.sha256(HTML.encode()).hexdigest()
    assert evidence['selected_html_sha256'] == hashlib.sha256(expected.encode()).hexdigest()


def test_image_only_profile_scope_preserves_original_image_for_local_ocr():
    html = '<html><body><article id="profile"><img src="/cv.jpg" alt=""></article></body></html>'
    scoped, evidence, issue = scope_profile_html(html, '#profile', 'https://example.edu/p')
    assert issue is None
    assert 'src="/cv.jpg"' in scoped
    assert evidence['image_only_candidate'] is True


def test_embedded_pdf_only_profile_scope_is_preserved_for_local_reader():
    html = '<html><body><article id="profile"><object data="/cv.pdf" type="application/pdf"></object></article></body></html>'
    scoped, evidence, issue = scope_profile_html(html, '#profile', 'https://example.edu/p')
    assert issue is None
    assert 'cv.pdf' in scoped
    assert evidence['image_only_candidate'] is True


def test_non_pdf_iframe_does_not_count_as_biography_media():
    html = '<article id="profile"><iframe src="/unrelated-widget"></iframe></article>'
    _, evidence, issue = scope_profile_html(html, '#profile', 'https://example.edu/p')
    assert evidence is None
    assert issue['status'] == 'profile_content_selector_empty'


@pytest.mark.parametrize('selector,html,status', [
    ('#absent', HTML, 'profile_content_selector_missing'),
    ('p', HTML, 'profile_content_selector_multiple'),
    ('[', HTML, 'profile_content_selector_invalid'),
    ('body', HTML, 'profile_content_selector_unsafe'),
    ('header', HTML, 'profile_content_selector_unsafe'),
    ('#profile', '<div id="profile"></div>', 'profile_content_selector_empty'),
    ('#profile', '<div hidden><div id="profile">正文</div></div>', 'profile_content_selector_hidden'),
    ('#profile', '<div id="profile" style="display: none">正文</div>', 'profile_content_selector_hidden'),
    ('#profile', '<div id="profile" style="opacity: 0.00">正文</div>', 'profile_content_selector_hidden'),
    ('#profile', '<div id="profile"><span aria-hidden="true">正文</span></div>', 'profile_content_selector_hidden'),
])
def test_invalid_scope_keeps_original_observation_but_reports_blocking_issue(selector, html, status):
    scoped, evidence, issue = scope_profile_html(html, selector, 'https://example.edu/p')
    assert scoped == html and evidence is None and issue['status'] == status


def test_roster_reconciliation_keeps_conflicting_scopes_for_same_profile():
    roster = '<main><li><a href="/p">张富有</a>教授</li></main>'
    pages = [CapturedRosterPage('https://example.edu/list', roster,
             {'card_selector': 'main li', 'profile_content_selector': selector})
             for selector in ('#profile', '.body')]
    result = reconcile_rosters(pages, {})
    assert result.profile_content_selectors['https://example.edu/p'] == {('#profile', None), ('.body', None)}
    assert len(result.candidates) == 1
    assert any(item['status'] == 'profile_content_selector_conflict' for item in result.unresolved)


@pytest.mark.parametrize('filename,selector', [
    ('nxmu-0.html', '.v_news_content'),
    ('nxmu-1.html', '.v_news_content'),
    ('cuc-1.html', '.wp_articlecontent'),
    ('cuc-2.html', '.wp_articlecontent'),
])
def test_independent_live_saved_page_scope_preserves_complete_dom(filename, selector):
    path = Path(__file__).parent / 'fixtures/profile_content_scope' / filename
    html = path.read_text(encoding='utf-8')
    scoped, evidence, issue = scope_profile_html(html, selector, 'https://example.edu/real-capture')
    assert issue is None
    original = BeautifulSoup(html, 'html.parser').select_one(selector)
    selected = BeautifulSoup(scoped, 'html.parser').select_one(selector)
    assert str(selected) == str(original)
    assert selected.get_text(' ', strip=True) == original.get_text(' ', strip=True)
    assert evidence['attribution_scope'] == 'verified_profile_container'


def test_start_boundary_preserves_all_sibling_nodes_including_direct_text():
    html = '<div class="content"><div>元数据甲</div><div>元数据乙</div><hr id="start">直接正文<p>履历第一段</p>结尾原始文字<section>全部成果</section></div>'
    scoped, evidence, issue = scope_profile_html(html, '.content', 'https://example.edu/p', start_selector='#start')
    assert issue is None
    assert '元数据' not in scoped and '<hr' not in scoped
    assert '直接正文' in scoped and '履历第一段' in scoped and '结尾原始文字' in scoped and '全部成果' in scoped
    assert evidence['profile_content_start_selector'] == '#start'


@pytest.mark.parametrize('html,selector,status', [
    ('<div id="p">正文</div>', '#absent', 'profile_content_start_selector_missing'),
    ('<div id="p"><hr><hr>正文</div>', 'hr', 'profile_content_start_selector_multiple'),
    ('<div id="p"><section><hr>正文</section></div>', 'hr', 'profile_content_start_selector_not_direct_child'),
    ('<div id="p">元数据<hr></div>', 'hr', 'profile_content_start_selector_empty'),
    ('<div id="p">正文</div>', '[', 'profile_content_start_selector_invalid'),
])
def test_invalid_start_boundary_blocks_without_truncating_observation(html, selector, status):
    scoped, evidence, issue = scope_profile_html(html, '#p', 'https://example.edu/p', start_selector=selector)
    assert scoped == html and evidence is None and issue['status'] == status


def test_same_container_different_start_boundaries_conflict():
    roster = '<main><li><a href="/p">张富有</a>教授</li></main>'
    result = reconcile_rosters([CapturedRosterPage('https://example.edu/list', roster,
        {'card_selector': 'main li', 'profile_content_selector': '#profile',
         **({'profile_content_start_selector': start} if start else {})})
        for start in (None, 'hr')], {})
    assert result.profile_content_selectors['https://example.edu/p'] == {('#profile', None), ('#profile', 'hr')}
    assert any(item['status'] == 'profile_content_selector_conflict' for item in result.unresolved)


def test_start_without_container_is_a_blocking_reconciliation_issue():
    roster = '<main><li><a href="/p">张富有</a>教授</li></main>'
    result = reconcile_rosters([CapturedRosterPage('https://example.edu/list', roster,
        {'card_selector': 'main li', 'profile_content_start_selector': 'hr'})], {})
    assert result.candidates
    assert any(item['status'] == 'profile_content_start_selector_without_container' for item in result.unresolved)


def test_hidden_metadata_before_verified_boundary_is_outside_selected_scope():
    html = '<div id="p"><div hidden>前置模板</div><hr>直接正文<p>完整履历</p></div>'
    scoped, evidence, issue = scope_profile_html(html, '#p', 'https://example.edu/p', start_selector='hr')
    assert issue is None and '前置模板' not in scoped and '完整履历' in scoped


@pytest.mark.asyncio
async def test_navigation_children_inherit_the_complete_selector_pair():
    from server.harvest_workflow import capture_sources
    branches = []
    async def discover(_context, _branch):
        return '<a href="/child">入口</a>', ['https://example.edu/child'], [], {}
    async def capture(_context, branch):
        branches.append(branch)
        return [], [], {}
    await capture_sources({'branches': [{'url': 'https://example.edu/parent', 'kind': 'navigation',
        'profile_content_selector': '.content', 'profile_content_start_selector': '.content > hr',
        'child_branch': {'single_page': True, 'card_selector': 'li'}}]}, None,
        capture_branch=capture, discover_children=discover)
    assert branches[0]['profile_content_selector'] == '.content'
    assert branches[0]['profile_content_start_selector'] == '.content > hr'


def test_real_stbu_container_boundary_keeps_complete_suffix():
    html = (Path(__file__).parent / 'fixtures/profile_content_scope/stbu-1.html').read_text(encoding='utf-8')
    scoped, evidence, issue = scope_profile_html(html, '.content', 'https://example.edu/p', start_selector='.content > hr')
    assert issue is None
    original = BeautifulSoup(html, 'html.parser').select_one('.content')
    marker = original.select_one(':scope > hr')
    expected = ''.join(str(node) for node in marker.next_siblings)
    selected = BeautifulSoup(scoped, 'html.parser').select_one('.content')
    assert ''.join(str(node) for node in selected.contents) == expected


async def _run_browser_plan(tmp_path, *, selectors=('#profile',), shared=False, missing=False, stylesheet=''):
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import run_plan
    root = 'https://scope.example.edu'
    names = ('张富有', '刘圣洁') if shared else ('张富有',)
    sources = {}
    branches = []
    for index, selector in enumerate(selectors):
        url = f'{root}/list{index}'
        sources[url] = '<main><ul>' + ''.join(f'<li><a href="/p">{name}</a>教授</li>' for name in names) + '</ul></main>'
        branches.append({'url': url, 'single_page': True, 'card_selector': 'main li',
                         'profile_content_selector': selector})
    if shared:
        body = ''.join(f'<section><h2>{name}</h2><p>{name}教授独立完整履历，包括教学与科研成果。</p></section>' for name in names)
    else:
        body = '<h1>张富有</h1><div>副教授</div><section><p>张富有的完整学习经历与工作经历。</p><p>科研成果与论文完整末段。</p><div>邮箱 zhang@example.edu</div></section>'
    sources[root + '/p'] = stylesheet + '<header>机构模板 institution@example.edu</header><article id="' + ('other' if missing else 'profile') + '">' + body + '</article><footer>模板页脚</footer>'
    plan = {'school': '样本大学', 'college': '样本学院', 'source_kind': 'university_faculty_roster', 'branches': branches}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        async def route(request):
            html = sources.get(request.request.url)
            if html is None:
                await request.abort()
            else:
                await request.fulfill(body=html, content_type='text/html; charset=utf-8')
        await context.route('**/*', route)
        report = await run_plan(plan, context, tmp_path)
        await browser.close()
    records = json.loads(Path(report['records_path']).read_text(encoding='utf-8'))
    return report, records


@pytest.mark.asyncio
@pytest.mark.browser
async def test_run_plan_scopes_all_field_extraction_and_preserves_raw_snapshot(tmp_path):
    report, records = await _run_browser_plan(tmp_path)
    assert report['delivery_ready'] and len(records) == 1
    row = records[0]
    assert row['职称'] == '副教授' and row['邮箱'] == 'zhang@example.edu'
    assert '完整学习经历与工作经历' in row['简介'] and '科研成果与论文完整末段' in row['简介']
    assert '模板' not in row['简介']
    source = report['coverage']['field_sources'][0]
    assert source['attribution_scope'] == 'verified_profile_container' and source['profile_content_selector'] == '#profile'
    assert source['profile_content_browser_evidence']['confirmed'] is True
    assert source['profile_content_browser_evidence']['html_sha256'] == source['original_html_sha256']
    assert '机构模板' in Path(source['profile_snapshot']).read_text(encoding='utf-8')


@pytest.mark.asyncio
@pytest.mark.browser
async def test_run_plan_missing_scope_keeps_records_but_blocks_delivery(tmp_path):
    report, records = await _run_browser_plan(tmp_path, missing=True)
    assert records and not report['delivery_ready'] and report['excel_path'] is None
    assert any(issue['status'] == 'profile_content_selector_missing' for issue in report['unresolved'])


@pytest.mark.asyncio
@pytest.mark.browser
async def test_run_plan_conflicting_scopes_keeps_records_but_blocks_delivery(tmp_path):
    report, records = await _run_browser_plan(tmp_path, selectors=('#profile', 'article'))
    assert records and not report['delivery_ready'] and report['excel_path'] is None
    assert any(issue['status'] == 'profile_content_selector_conflict' for issue in report['unresolved'])


@pytest.mark.asyncio
@pytest.mark.browser
async def test_run_plan_shared_container_uses_existing_person_attribution(tmp_path):
    report, records = await _run_browser_plan(tmp_path, shared=True)
    assert len(records) == 2
    for row in records:
        other = '刘圣洁' if row['姓名'] == '张富有' else '张富有'
        assert row['姓名'] in row['简介'] and other not in row['简介']
    assert not any(issue['status'] == 'profile_body_disagreement' for issue in report['unresolved'])


@pytest.mark.asyncio
@pytest.mark.browser
async def test_stylesheet_hidden_profile_scope_is_blocked_in_real_browser(tmp_path):
    report, records = await _run_browser_plan(tmp_path, stylesheet='<style>#profile {display:none}</style>')
    assert records
    assert not report['delivery_ready'] and report['excel_path'] is None
    assert any(issue['status'] == 'profile_content_browser_unverified' for issue in report['unresolved'])
    assert report['coverage']['field_sources'][0]['attribution_scope'] == 'unverified_profile_container'


@pytest.mark.asyncio
@pytest.mark.browser
async def test_stylesheet_hidden_descendant_is_blocked_in_real_browser(tmp_path):
    report, records = await _run_browser_plan(tmp_path, stylesheet='<style>#profile section {visibility:hidden}</style>')
    assert records
    assert not report['delivery_ready'] and report['excel_path'] is None
    assert any(issue['status'] == 'profile_content_browser_unverified' for issue in report['unresolved'])


@pytest.mark.asyncio
@pytest.mark.browser
async def test_scoped_checkpoint_is_reopened_and_original_time_only_preserved_if_unchanged(tmp_path):
    from playwright.async_api import async_playwright
    from server.harvest_workflow import capture_profiles
    from server.harvest_state import HarvestState
    url = 'https://scope.example.edu/p'
    body = [HTML]
    state = HarvestState(tmp_path, {'branches': [{'url': url}]})
    visits = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        async def route(request):
            visits.append(request.request.url)
            await request.fulfill(body=body[0], content_type='text/html; charset=utf-8')
        await context.route('**/*', route)
        options = {'state': state, 'profile_content_selectors': {url: {('#profile', None)}}}
        first_proofs = {}
        first, issues = await capture_profiles(context, [url], 1,
            profile_content_evidence=first_proofs, **options)
        assert not issues and first_proofs[url]['confirmed']
        original_time = state.load_page_record('profile', url)['captured_at']
        visits.clear()
        second_proofs = {}
        second, issues = await capture_profiles(context, [url], 1,
            profile_content_evidence=second_proofs, **options)
        assert visits == [url] and first == second and not issues
        assert second_proofs[url]['capture_origin'] == 'checkpoint_revalidated'
        assert state.load_page_record('profile', url)['captured_at'] == original_time
        body[0] = '<style>#profile{display:none}</style>' + HTML
        visits.clear()
        third_proofs = {}
        third, issues = await capture_profiles(context, [url], 1,
            profile_content_evidence=third_proofs, **options)
        assert visits == [url] and third[url] != first[url] and not third_proofs
        assert any(issue['status'] == 'profile_content_browser_unverified' for issue in issues)
        assert state.load_page_record('profile', url)['captured_at'] > original_time
        await browser.close()


@pytest.mark.asyncio
async def test_unscoped_checkpoint_retains_zero_browser_visit_recovery(tmp_path):
    from server.harvest_workflow import capture_profiles
    from server.harvest_state import HarvestState
    url = 'https://scope.example.edu/p'
    state = HarvestState(tmp_path, {'branches': [{'url': url}]})
    state.save_page('profile', url, HTML)
    pages, issues = await capture_profiles(object(), [url], 1, state=state)
    assert pages[url] == HTML and issues == []


@pytest.mark.asyncio
@pytest.mark.browser
async def test_native_fallback_profile_cannot_authorize_scoped_delivery(tmp_path, monkeypatch):
    from server import browser_batch_tool
    async def failed_capture(_context, urls, _concurrency, _ready, **_kwargs):
        return {url: None for url in urls}, [
            {'url': url, 'status': 'profile_navigation_failed'} for url in urls]
    monkeypatch.setattr(browser_batch_tool, 'capture_profiles', failed_capture)
    monkeypatch.setattr(browser_batch_tool, '_batch_fetch_profiles_via_native_chrome',
                        lambda urls: {url: HTML for url in urls})
    report, records = await _run_browser_plan(tmp_path)
    assert records and not report['delivery_ready'] and report['excel_path'] is None
    assert any(issue['status'] == 'profile_content_browser_unverified' for issue in report['unresolved'])
    assert report['coverage']['field_sources'][0]['attribution_scope'] == 'unverified_profile_container'
