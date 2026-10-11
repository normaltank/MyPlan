"""Missing published fields stay blank without losing source observations."""
import json
from pathlib import Path

import openpyxl
import pytest
from playwright.async_api import async_playwright

from server.browser_batch_tool import run_plan
from server.browser_context import new_browser_context
from server.excel_export import export_dual_sheet_excel
from server.faculty_extractor import FacultyDirectoryExtractor, FacultyHarvestPipeline

URL = 'https://fixture.example.edu/faculty'
PERSON = 'https://fixture.example.edu/person/1'
SOURCE = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
          'role_evidence_selector': 'h1', 'source_context_selector': 'header'}


def roster(value='', linked=False):
    name = '<a href="/person/1">王芳</a>' if linked else (
        '<a href="/faculty">王芳</a>' if value else '王芳')
    return ('<meta charset="utf-8"><title>师资队伍</title><header>样本大学 样本学院</header>'
            '<h1>师资队伍</h1><main><ul class="roster"><li>' + name + value + '</li></ul></main>')


@pytest.mark.parametrize('field,expected', [('', ''), ('<p>职称：教师</p>', '教师'), ('<span>教授</span>', '教授')])
def test_deterministic_extraction_to_workbook_keeps_missing_fields_blank(tmp_path, field, expected):
    html = roster(field)
    candidates, pending = FacultyDirectoryExtractor._verified_card_candidates(html, URL, '.roster > li')
    assert not pending and len(candidates) == 1
    original_url = candidates[0].url
    fetched = []
    result = FacultyHarvestPipeline.harvest_candidates(candidates, lambda url: fetched.append(url),
        base_url=URL, school='样本大学', college='样本学院')
    assert result['delivery_ready'] and result['total_count'] == 1 and not fetched
    records = result['sheet1_with_email'] + result['sheet2_no_email']
    assert records[0]['职称'] == expected and records[0]['主页'] == ''
    assert candidates[0].url == original_url == URL and candidates[0].card_context
    assert result['source_records'][0]['主页'] == URL
    target = tmp_path / 'published.xlsx'
    export_dual_sheet_excel(records, str(target))
    workbook = openpyxl.load_workbook(target)
    values = list(workbook.worksheets[1].values)
    actual = dict(zip(values[0], values[1]))
    assert (actual['职称'] or '') == expected and not actual['主页']


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('field,linked,expected', [
    ('', False, ''), ('<p>职称：教师</p>', False, '教师'), ('<span>教授</span>', True, '教授'),
    ('', True, ''), ('<p>职称：教师</p>', True, '教师')])
async def test_actual_browser_plan_preserves_source_and_personal_field_semantics(tmp_path, field, linked, expected):
    original = roster(field, linked)
    profile = '<meta charset="utf-8"><article><h1>王芳</h1><p>王芳从事教学研究。</p></article>'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await new_browser_context(browser, polite=False)
        async def route(request):
            await request.fulfill(body=profile if request.request.url == PERSON else original,
                                  content_type='text/html; charset=utf-8')
        await context.route('**/*', route)
        branch = {**SOURCE, 'url': URL, 'card_selector': '.roster > li', 'single_page': True}
        if linked:
            branch['profile_content_selector'] = 'article'
        try:
            result = await run_plan({'school': '样本大学', 'college': '样本学院', 'branches': [branch]}, context, tmp_path)
            assert result['delivery_ready'], result['unresolved']
            [row] = json.loads(Path(result['records_path']).read_text(encoding='utf-8'))
            assert row['职称'] == expected and row['主页'] == (PERSON if linked else '')
            audit = json.loads(Path(result['audit_path']).read_text(encoding='utf-8'))
            assert audit['source_qualification_evidence'][0]['browser_proof']['confirmed'] is True
            assert audit['coverage']['field_sources'][0]['profile_url'] == (PERSON if linked else URL)
            originals = [item for item in audit['snapshots'] if item['url'] == URL]
            assert originals
            assert '王芳' in Path(originals[0]['path']).read_text(encoding='utf-8')
            if linked:
                assert '王芳从事教学研究。' in row['简介']
        finally:
            await browser.close()
