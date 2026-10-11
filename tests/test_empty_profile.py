import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from server.browser_batch_tool import run_plan
from server.empty_profile import EmptyProfileEvidence, heading_digest
from server.faculty_extractor import FacultyCandidate
from server.profile_content import scope_profile_html

ROOT = 'https://empty.example.edu'
PROFILE = ROOT + '/person'


async def harvest(tmp_path, body, *, redirect=False):
    plan = {'school': '样本大学', 'college': '样本学院',
            'source_kind': 'university_faculty_roster',
            'branches': [{'url': ROOT + '/list', 'single_page': True,
                          'card_selector': 'main li', 'profile_content_selector': '#profile'}]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def route(request):
            url = request.request.url
            if url == ROOT + '/list':
                await request.fulfill(body='<main><li><a href="/person">张富有</a>教授</li></main>',
                                      content_type='text/html; charset=utf-8')
            elif redirect and url == PROFILE:
                await request.fulfill(status=302, headers={'location': ROOT + '/other'})
            elif url == ROOT + '/unread':
                await request.fulfill(body='<p>未读嵌入内容</p>', content_type='text/html; charset=utf-8')
            else:
                await request.fulfill(body=body, content_type='text/html; charset=utf-8')
        await context.route('**/*', route)
        report = await run_plan(plan, context, tmp_path)
        await browser.close()
    records = json.loads(Path(report['records_path']).read_text(encoding='utf-8'))
    return report, records


EMPTY = '<nav>师资队伍 张富有 联系我们 college@example.edu</nav><main><h1>张富有</h1><article id="profile" style="min-height:1px"></article></main>'


@pytest.mark.parametrize('change', ['stale_html', 'stale_scope', 'redirect', 'no_sources', 'shared', 'wrong_name'])
def test_empty_evidence_refuses_changed_capture_or_ambiguous_identity(change):
    _, evidence, _ = scope_profile_html(EMPTY, '#profile', PROFILE, allow_empty=True)
    proof = {'confirmed': True, 'url': PROFILE, 'profile_content_selector': '#profile',
             'profile_content_start_selector': None, 'empty_container': True,
             'html_sha256': evidence['original_html_sha256'],
             'selected_html_sha256': evidence['selected_html_sha256'],
             'container_count': 1, 'container_visible': True, 'dom_stable': True, 'network_idle': True,
             'hidden_content_count': 0, 'active_animation_count': 0, 'document_complete': True,
             'script_error_count': 0, 'visible_identity_heading_sha256': [heading_digest('张富有')]}
    candidates = [FacultyCandidate('张富有', PROFILE)]
    sources = [{'roster_url': ROOT + '/list'}]
    html = EMPTY
    assert EmptyProfileEvidence.verify(PROFILE, html, '#profile', proof, candidates, sources)
    if change == 'stale_html':
        html += '<footer>capture changed</footer>'
    elif change == 'stale_scope':
        proof['selected_html_sha256'] = '0' * 64
    elif change == 'redirect':
        proof['url'] = ROOT + '/other'
    elif change == 'no_sources':
        sources = []
    elif change == 'shared':
        candidates.append(FacultyCandidate('李星', PROFILE))
    elif change == 'wrong_name':
        candidates = [FacultyCandidate('李星', PROFILE)]
    assert EmptyProfileEvidence.verify(PROFILE, html, '#profile', proof, candidates, sources) is None


def test_empty_suffix_cannot_authorize_missing_whole_biography():
    _, evidence, issue = scope_profile_html('<article id="p">已发表完整履历<hr></article>',
        '#p', PROFILE, start_selector='#p>hr', allow_empty=True)
    assert evidence is None and issue['status'] == 'profile_content_start_selector_empty'


@pytest.mark.asyncio
@pytest.mark.browser
async def test_verified_empty_profile_delivers_blank_bio_with_bound_missing_evidence(tmp_path):
    report, records = await harvest(tmp_path, EMPTY)
    assert report['delivery_ready'] and len(records) == 1
    assert records[0]['简介'] == '' and records[0]['邮箱'] == ''
    assert records[0]['职称'] == '教授'
    source = report['coverage']['field_sources'][0]
    proof = source['empty_profile_evidence']
    assert proof['requested_url'] == proof['final_url'] == PROFILE
    assert proof['name'] == '张富有' and proof['status'] == 'browser_verified_empty_profile'
    assert proof['html_sha256'] == source['original_html_sha256']
    assert source['profile_content_browser_evidence']['confirmed']
    assert report['missing_fields'] == [{'url': PROFILE, 'name': '张富有', 'field': '简介',
                                        'status': 'browser_verified_empty_profile'}]
    assert 'college@example.edu' in Path(source['profile_snapshot']).read_text(encoding='utf-8')


@pytest.mark.parametrize('body,redirect', [
    (EMPTY.replace('<h1>张富有</h1>', ''), False),
    (EMPTY.replace('<h1>张富有</h1>', '<h1>李星</h1>'), False),
    (EMPTY.replace('<h1>', '<h1 style="display:none">'), False),
    (EMPTY, True),
])
@pytest.mark.asyncio
@pytest.mark.browser
async def test_unverified_empty_profile_keeps_blank_observation_and_blocks(tmp_path, body, redirect):
    report, records = await harvest(tmp_path, body, redirect=redirect)
    assert not report['delivery_ready'] and report['excel_path'] is None
    assert report['missing_fields'] == []
    if records:
        assert records[0]['简介'] == '' and records[0]['邮箱'] == ''
    else:
        assert redirect
    assert any(issue['status'] in {'profile_content_selector_empty', 'profile_navigation_failed'}
               for issue in report['unresolved'])


@pytest.mark.asyncio
@pytest.mark.browser
async def test_unread_frame_is_not_confirmed_as_empty_profile(tmp_path):
    body = EMPTY.replace('</article>', '<iframe src="/unread"></iframe></article>')
    report, _ = await harvest(tmp_path, body)
    assert not report['delivery_ready'] and not report['missing_fields']


@pytest.mark.asyncio
@pytest.mark.browser
async def test_pageerror_preserved_without_overriding_stable_complete_scope(tmp_path):
    report, records = await harvest(tmp_path, EMPTY + '<script>throw new Error("unrelated site error")</script>')
    assert report['delivery_ready'] and records[0]['简介'] == ''
    proof = report['coverage']['field_sources'][0]['profile_content_browser_evidence']
    assert proof['script_error_count'] == 1
    assert proof['page_errors'][0]['message'] == 'unrelated site error'


@pytest.mark.asyncio
@pytest.mark.browser
async def test_dynamic_profile_finishes_loading_before_empty_verdict():
    import asyncio
    from server.harvest_workflow import capture_profiles
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def route(request):
            if request.request.url == PROFILE:
                await request.fulfill(body=EMPTY + '<script>fetch("/bio").then(r=>r.text()).then(t=>document.querySelector("#profile").textContent=t)</script>',
                                      content_type='text/html; charset=utf-8')
            else:
                await asyncio.sleep(0.6)
                await request.fulfill(body='张富有的完整动态履历，不能提前声明简介缺失。',
                                      content_type='text/plain; charset=utf-8')
        await context.route('**/*', route)
        evidence = {}
        pages, issues = await capture_profiles(context, [PROFILE], 1,
            profile_content_selectors={PROFILE: {('#profile', None)}},
            profile_content_evidence=evidence)
        assert not issues and evidence[PROFILE]['confirmed']
        assert evidence[PROFILE]['empty_container'] is False
        assert '完整动态履历' in pages[PROFILE]
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_empty_body_with_outstanding_content_request_is_blocked(tmp_path):
    from server.harvest_workflow import capture_profiles
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def route(request):
            if request.request.url == PROFILE:
                await request.fulfill(body=EMPTY + '<script>fetch("/still-loading")</script>',
                                      content_type='text/html; charset=utf-8')
            # Deliberately leave the content fetch outstanding.
        await context.route('**/*', route)
        evidence = {}
        _, issues = await capture_profiles(context, [PROFILE], 1,
            profile_content_selectors={PROFILE: {('#profile', None)}},
            profile_content_evidence=evidence)
        assert not evidence
        assert issues[0]['status'] == 'profile_content_browser_unverified'
        assert issues[0]['browser_evidence']['reason'] == 'network_not_idle'
        await browser.close()

# Source purpose is a trusted component boundary; the profile/empty proof remains real Chromium.
pytestmark = pytest.mark.verified_source_boundary
