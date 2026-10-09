"""A directory of personal introductions must not be captured as child rosters."""
import json

import pytest

from server.agent_recon import selector_evidence_issues
from server.harvest_workflow import capture_sources
from server.faculty_extractor import FacultyDirectoryExtractor

URL = 'https://faculty.example.edu/staff'
SELECTOR = '#staff a'
PERSONAL_LINKS = ('<ul id="staff"><li><a href="/p/1">张伟教授简介</a></li>'
                  '<li><a href="/p/2"><span>职占江</span>副教授介绍</a></li></ul>')
NAVIGATION = {'url': URL, 'kind': 'navigation', 'child_link_selector': SELECTOR,
              'child_branch': {'single_page': True, 'card_selector': 'article'}}


@pytest.mark.asyncio
async def test_capture_rejects_personal_introductions_before_reading_children():
    visited = []

    async def discover(_context, branch):
        return PERSONAL_LINKS, ['https://faculty.example.edu/p/1', 'https://faculty.example.edu/p/2'], [], {'url': URL}

    async def capture(_context, branch):
        visited.append(branch['url'])
        # This callback may otherwise restore an old cached branch.
        return [(branch['url'], '<article>简介</article>')], [], {'restored': True}

    result = await capture_sources({'branches': [NAVIGATION]}, object(),
                                   capture_branch=capture, discover_children=discover)
    assert result.navigation_pages == [(URL, PERSONAL_LINKS)]
    assert result.roster_pages == [] and visited == []
    [issue] = result.unresolved
    assert issue['status'] == 'child_roster_not_verified'
    assert issue['reason'] == 'navigation_links_are_profiles'
    assert issue['profile_intro_link_count'] == 2


def events(observations):
    result = []
    for index, (url, selector, extra) in enumerate(observations):
        call_id = str(index)
        result.extend([
            {'type': 'tool/call', 'data': {'callId': call_id,
             'name': 'mcp__playwright__browser_verify',
             'arguments': json.dumps({'card_selector': selector})}},
            {'type': 'tool/result', 'data': {'message': {'content': [
                {'toolCallId': call_id, 'content': [{'type': 'text', 'text': json.dumps({
                    'url': url, 'cardCount': 2, 'pagination': {'total': 0}, **extra})}]}]}}},
        ])
    return result


def test_recon_gate_does_not_accept_personal_links_as_a_directory():
    children = ['https://faculty.example.edu/p/1', 'https://faculty.example.edu/p/2']
    observed = events([(URL, SELECTOR, {'linkedSamples': children, 'profile_intro_link_count': 2}),
                       *[(url, 'article', {}) for url in children]])
    issues = selector_evidence_issues({'branches': [NAVIGATION]}, observed)
    assert any(issue['status'] == 'child_roster_not_verified'
               and issue['failed_condition'] == 'navigation_links_are_profiles' for issue in issues)


def test_role_evidence_is_scoped_to_selected_introduction_links():
    html = ('<header><a href="/other">张伟教授简介</a></header><div id="staff">'
            '<a href="/school">南开大学</a><a href="/subject">政治学</a>'
            '<a href="/name">张伟</a><a href="/detail">查看详情</a></div>')
    assert FacultyDirectoryExtractor.profile_introduction_links(html, URL, SELECTOR) == []
    assert FacultyDirectoryExtractor.profile_introduction_links(PERSONAL_LINKS, URL, '#staff li') == [
        'https://faculty.example.edu/p/1', 'https://faculty.example.edu/p/2']


@pytest.mark.asyncio
async def test_mixed_roles_block_without_dropping_other_directory_links():
    html = PERSONAL_LINKS.replace('</ul>', '<li><a href="/dept">教研室名录</a></li></ul>')
    children = ['https://faculty.example.edu/p/1', 'https://faculty.example.edu/p/2',
                'https://faculty.example.edu/dept']

    async def discover(_context, _branch):
        return html, children, [], {'url': URL, 'children': children}

    async def capture(*_args):
        raise AssertionError('An unverified mixed directory must not be captured as rosters')

    result = await capture_sources({'branches': [NAVIGATION]}, object(),
                                   capture_branch=capture, discover_children=discover)
    assert result.unresolved and result.branches[0]['children'] == children
    assert not result.roster_pages


def test_introduction_link_evidence_does_not_reject_a_normal_roster():
    plan = {'branches': [{'url': URL, 'card_selector': SELECTOR, 'single_page': True,
                         'profile_content_selector': 'article'}]}
    # Missing personal-page proofs are still required; the role conflict itself
    # applies only to navigation, not to normal name-labelled teacher cards.
    issues = selector_evidence_issues(plan, events([
        (URL, SELECTOR, {'linkedSamples': ['https://faculty.example.edu/p/1'],
                         'profile_intro_link_count': 2})]))
    assert not any(i.get('failed_condition') == 'navigation_links_are_profiles' for i in issues)
    assert any(i['status'] == 'profile_content_not_verified' for i in issues)


def test_rejected_title_shortcut_does_not_hide_real_members_or_unknown_item():
    from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
    html = ('<title>教师名单：张伟</title><div id="staff">'
            '<p>张伟，教授。</p><p>李明、TBD、王奔、赵强</p></div>')
    result = reconcile_rosters([CapturedRosterPage(URL, html, {'card_selector': '#staff'})], {})
    assert {p.name for p in result.candidates} == {'李明', '王奔', '赵强'}
    assert any(i.get('item') == 'TBD' for i in result.unresolved)


@pytest.mark.asyncio
@pytest.mark.browser
async def test_real_browser_verify_returns_role_count_without_teacher_fields(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.recon_browser_mcp import BrowserScout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser, polite=False)
            await context.route('**/*', lambda route: route.fulfill(
                body=f'<html><body>{PERSONAL_LINKS}</body></html>', content_type='text/html; charset=utf-8'))
            scout = BrowserScout(await context.new_page(), tmp_path, 'branch',
                                 {'school': '测试大学', 'college': '测试学院', 'url': URL})
            await scout.call('browser_navigate', {'url': URL})
            reply = json.loads((await scout.call('browser_verify', {'card_selector': SELECTOR}))['content'][0]['text'])
            assert reply['profile_intro_link_count'] == 2
            assert '张伟' not in json.dumps(reply, ensure_ascii=False)
            assert '职占江' not in json.dumps(reply, ensure_ascii=False)
        finally:
            await browser.close()
