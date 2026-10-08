"""A name annotation may be outside its anchor; retain its original card in the audit."""
import json
from pathlib import Path

import pytest

from server.harvest_eligibility import classify_candidates
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters

URL = 'https://jyxy.jhun.edu.cn/roster'


@pytest.mark.parametrize('cell', [
    '<a href="/person">王芳（兼任）</a>',
    '<a href="/person">王芳</a>（兼任）',
    '<a href="/person">王芳（</a>兼任）',
])
def test_concurrent_annotation_survives_in_eligibility_evidence(cell):
    html = f'<table><tr><td>{cell}</td></tr></table>'
    result = reconcile_rosters([CapturedRosterPage(URL, html, {'card_selector': 'td'})],
                               {'source_kind': 'university_faculty_roster'})
    assert not result.unresolved
    [candidate] = result.candidates
    assert candidate.name == '王芳'
    _, decisions, _ = classify_candidates(result.candidates, result.candidate_sources, {}, {})
    assert '兼任' in decisions[0]['roster_context']
    assert '（' in decisions[0]['roster_context'] and '）' in decisions[0]['roster_context']


@pytest.mark.asyncio
@pytest.mark.browser
async def test_annotation_is_written_to_the_persistent_batch_audit(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import run_plan

    pages = {URL: '<table><tr><td><a href="/person">王芳（兼任</a>）</td></tr></table>',
             'https://jyxy.jhun.edu.cn/person': '<article><h1>王芳</h1><p>教授，从事教育学研究。</p></article>'}
    plan = {'school': '样本大学', 'college': '教育学院', 'source_kind': 'university_faculty_roster',
            'branches': [{'url': URL, 'single_page': True, 'card_selector': 'td',
                          'profile_content_selector': 'article'}]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context()

            async def serve(route):
                await route.fulfill(body=pages.get(route.request.url, ''),
                                    content_type='text/html; charset=utf-8')

            await context.route('**/*', serve)
            report = await run_plan(plan, context, tmp_path)
        finally:
            await browser.close()
    audit = json.loads(Path(report['audit_path']).read_text(encoding='utf-8'))
    assert audit['eligibility'][0]['roster_context'] == '王芳（兼任）'
    assert audit['eligibility'][0]['name'] == '王芳'
