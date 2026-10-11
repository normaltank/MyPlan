"""Branding identifier words are structural clues, never affiliation permission."""
import json

import pytest
from playwright.async_api import async_playwright

from server.browser_context import new_browser_context
from server.recon_browser_mcp import BrowserScout

from server.source_qualification import witness

TARGET = {'school': '样本大学', 'college': '样本学院'}
URL = 'https://college.example.edu/people'
CLAIM = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
         'role_evidence_selector': 'h1', 'source_context_selector': '#brand'}
PAGE = '<title>教师名单</title><h1>教师名单</h1><main><p>王芳教授</p></main>'


@pytest.mark.parametrize('identifier', ['footWrap', 'copyRight', 'FOOTPanel', 'siteFooter', 'brandHeader'])
@pytest.mark.parametrize('attribute', ['class', 'id'])
def test_camel_case_institution_identifier_words_support_independent_branding(identifier, attribute):
    html = PAGE + f'<div {attribute}="{identifier}"><a>样本大学样本学院</a></div>'
    selector = f'.{identifier}' if attribute == 'class' else f'#{identifier}'
    observed = witness(html, URL, {**CLAIM, 'source_context_selector': selector}, TARGET)
    assert observed['non_institutional_context_count'] == 0
    assert observed['college_bound'] and observed['role_supported']


@pytest.mark.parametrize('variant', ['article', 'body', 'overlap', 'outbound_nav', 'unseparated_word'])
def test_camel_case_identifier_does_not_replace_frame_and_affiliation_proof(variant):
    html = PAGE + '<div id="context" class="footWrap">样本大学样本学院</div>'
    claim = {**CLAIM, 'source_context_selector': '#context'}
    if variant == 'article':
        html = PAGE + '<article><div id="context" class="footWrap">样本大学样本学院</div></article>'
    elif variant == 'body':
        html = '<body class="footWrap">' + html + '</body>'
        claim['source_context_selector'] = 'body'
    elif variant == 'overlap':
        html = PAGE.replace('<main>', '<main id="context" class="footWrap"><p>样本大学样本学院</p>')
    elif variant == 'outbound_nav':
        html = PAGE + '<nav id="context" class="brandMenu"><a href="https://other.edu/">样本大学样本学院</a></nav>'
    else:
        html = html.replace('footWrap', 'footnote')
    observed = witness(html, URL, claim, TARGET)
    assert observed['college_bound'] is False and observed['role_supported'] is False


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('article,confirmed', [(False, True), (True, False)])
async def test_browser_brand_words_keep_article_boundary(tmp_path, article, confirmed):
    context_html = '<div id="context" class="copyRight"><a>样本大学样本学院</a></div>'
    if article:
        context_html = '<article>' + context_html + '</article>'
    html = PAGE + context_html
    claim = {**CLAIM, 'source_context_selector': '#context'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await new_browser_context(browser, polite=False)
        await context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        scout = BrowserScout(await context.new_page(), tmp_path, 'discovery', TARGET)
        await scout.call('browser_navigate', {'url': URL})
        proof = json.loads((await scout.call('browser_verify_source', claim))['content'][0]['text'])
        await browser.close()
    assert proof['college_bound'] is confirmed
    assert proof['confirmed'] is confirmed
