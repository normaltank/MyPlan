"""A name leaf inside a link cannot become a profile-less completed record."""
import json

import pytest

from server.faculty_extractor import FacultyDirectoryExtractor
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters, roster_card_reading
from server.agent_recon import selector_evidence_issues
from server.recovery_policy import recovery_action

# These card/link components stipulate source purpose, while retaining their own link proof.
pytestmark = pytest.mark.verified_source_boundary

URL = 'https://faculty.example.edu/list'
HTML = '<main><a href="/p/1"><span>张伟</span></a><a href="/p/2"><span>李明</span></a></main>'


def test_name_leaf_cannot_discard_ancestor_links_or_fall_back_to_roster_url():
    result = reconcile_rosters([CapturedRosterPage(URL, HTML, {'card_selector':'main > a > span'})],
                               {'source_kind':'university_faculty_roster'})
    assert result.candidates == []
    [issue] = result.unresolved
    assert issue['status'] == 'roster_profile_link_outside_card'
    assert issue['sample_urls'] == ['https://faculty.example.edu/p/1','https://faculty.example.edu/p/2']
    assert recovery_action(issue['status']) == 'structure'
    assert roster_card_reading(HTML,URL,'main > a > span') == {'outside_profile_links':2}
    fixed = reconcile_rosters([CapturedRosterPage(URL,HTML,{'card_selector':'main > a',
        'profile_content_selector':'article'})], {'source_kind':'university_faculty_roster'})
    assert not fixed.unresolved and {p.url for p in fixed.candidates} == set(issue['sample_urls'])


@pytest.mark.parametrize('html,selector', [
    ('<main><span>张伟</span></main>','main > span'),
    ('<main><a href="#tab"><span>张伟</span></a></main>','main > a > span'),
    ('<main><a href="https://faculty.example.edu/list#tab"><span>张伟</span></a></main>','main > a > span'),
    ('<main><a href="mailto:a@example.edu"><span>张伟</span></a></main>','main > a > span'),
    ('<main><a href="/poster.png"><img src="/small.png"></a></main>','main > a > img'),
    (HTML,'main > a'),
    (HTML,'main'),
])
def test_closed_cards_unlinked_names_and_media_keep_their_own_existing_paths(html,selector):
    assert FacultyDirectoryExtractor.links_outside_cards(html,URL,selector) == []


def test_gate_reports_the_same_boundary_failure_without_teacher_fields():
    selector = 'main > a > span'
    args = {'card_selector':selector}
    reply = {'url':URL,'cardCount':2,'pagination':{'total':0},'linkedSamples':[],
             'card_mismatch':{'outside_profile_links':2}}
    events = [{'type':'tool/call','data':{'callId':'v','name':'mcp__playwright__browser_verify',
        'arguments':json.dumps(args)}}, {'type':'tool/result','data':{'message':{'content':[
            {'toolCallId':'v','content':[{'type':'text','text':json.dumps(reply)}]}]}}}]
    [issue] = selector_evidence_issues({'school':'测试大学','college':'测试学院',
        'branches':[{'url':URL,'single_page':True,'card_selector':selector}]},events)
    assert issue['status'] == 'roster_profile_link_outside_card'
    assert issue['closest'] == {'outside_profile_links':2}
    assert '张伟' not in json.dumps(issue,ensure_ascii=False)


@pytest.mark.parametrize('tag',['object','iframe','picture'])
def test_media_tag_with_consumed_name_text_cannot_drop_its_enclosing_link(tag):
    html = f'<main><a href="/person"><{tag}>张伟</{tag}></a></main>'
    result = reconcile_rosters([CapturedRosterPage(URL,html,{'card_selector':tag})],{})
    assert not result.candidates
    assert result.unresolved[0]['status'] == 'roster_profile_link_outside_card'
    assert roster_card_reading(html,URL,tag) == {'outside_profile_links':1}


@pytest.mark.asyncio
@pytest.mark.browser
async def test_real_browser_verification_reports_leaf_link_boundary(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.recon_browser_mcp import BrowserScout
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser,polite=False)
            await context.route('**/*',lambda route:route.fulfill(body=HTML,
                content_type='text/html; charset=utf-8'))
            scout = BrowserScout(await context.new_page(),tmp_path)
            await scout.call('browser_navigate',{'url':URL})
            result = await scout.call('browser_verify',{'card_selector':'main > a > span'})
            reply = json.loads(result['content'][0]['text'])
            assert reply['card_mismatch'] == {'outside_profile_links':2}
        finally:
            await browser.close()
