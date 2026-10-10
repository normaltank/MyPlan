"""A single declared title field preserves its compound or qualified value."""
import pytest

from server.detail_mecc import DetailMECCExtractor
from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline


VALUES = ['讲师（内聘副教授）', '教授/研究员', '高级工程师(教授级)', '研究员/正高级工程师']


@pytest.mark.parametrize('value', VALUES)
def test_one_profile_field_is_not_multiple_conflicting_fields(value):
    url = 'https://faculty.example.edu/p/1'
    html = f'<article><h1>王芳</h1><p>职 称：{value}</p><p>完整科研履历。</p></article>'
    detail = DetailMECCExtractor.extract_single(html, url, '王芳', preserve_content=True)
    assert detail.title == value and detail.status == 'ok'
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate('王芳', url, '教授')], profile_fetcher=lambda _: html,
        base_url='https://faculty.example.edu/list', school='样本大学', college='样本学院',
        verified_profile_urls={url})
    assert result['delivery_ready'] and not result['unresolved']
    row, = result['sheet1_with_email'] + result['sheet2_no_email']
    assert row['职称'] == value and row['简介'] == detail.full_bio


def test_distinct_declared_fields_still_require_review():
    url = 'https://faculty.example.edu/p/1'
    html = '<article><h1>王芳</h1><p>职称：讲师（内聘副教授）</p><p>职称：教授</p></article>'
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate('王芳', url)], profile_fetcher=lambda _: html,
        base_url='https://faculty.example.edu/list', school='样本大学', college='样本学院',
        verified_profile_urls={url})
    assert not result['delivery_ready']
    issue, = [x for x in result['unresolved'] if x['status'] == 'title_field_conflict']
    assert set(issue['matches']) == {'讲师（内聘副教授）', '教授'}


@pytest.mark.parametrize('body', [
    '<p>职称：教授/<br>研究员</p>',
    '<table><tr><td>职称</td><td><p>教授/</p><p>研究员</p></td></tr></table>',
])
def test_continued_compound_field_does_not_export_a_truncated_prefix(body):
    url = 'https://faculty.example.edu/p/1'
    html = f'<article><h1>王芳</h1>{body}<p>完整科研履历。</p></article>'
    detail = DetailMECCExtractor.extract_single(html, url, '王芳', preserve_content=True)
    assert detail.title == '教授/\n研究员'
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate('王芳', url)], profile_fetcher=lambda _: html,
        base_url='https://faculty.example.edu/list', school='样本大学', college='样本学院',
        verified_profile_urls={url})
    assert result['delivery_ready'] and not result['unresolved']
    row, = result['sheet1_with_email'] + result['sheet2_no_email']
    assert row['职称'] == detail.title and row['简介'] == detail.full_bio


@pytest.mark.parametrize(('body', 'expected'), [
    ('<p>职称：教授，<br>研究员</p>', '教授，\n研究员'),
    ('<p>职称：教授<br>/研究员</p>', '教授\n/研究员'),
    ('<table><tr><td>职称</td><td><p>教授</p><p>/研究员</p></td></tr></table>',
     '教授\n/研究员'),
])
def test_declared_dom_unit_survives_profile_and_harvest(body, expected):
    url = 'https://faculty.example.edu/p/1'
    html = f'<article><h1>王芳</h1>{body}<p>完整科研履历。</p></article>'
    detail = DetailMECCExtractor.extract_single(html, url, '王芳', preserve_content=True)
    assert detail.title == expected and detail.status == 'ok'
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate('王芳', url)], profile_fetcher=lambda _: html,
        base_url='https://faculty.example.edu/list', school='样本大学', college='样本学院',
        verified_profile_urls={url})
    assert result['delivery_ready'] and not result['unresolved']
    row, = result['sheet1_with_email'] + result['sheet2_no_email']
    assert row['职称'] == expected and row['简介'] == detail.full_bio


def test_a_sibling_paragraph_is_not_a_field_continuation():
    html = '<article><p>职称：教授</p><p>研究员</p><p>完整履历。</p></article>'
    detail = DetailMECCExtractor.extract_single(html, 'https://faculty.example.edu/p/1',
                                              '王芳', preserve_content=True)
    assert detail.title == '教授' and detail.status == 'ok'
    assert detail.title_fields[0].raw_value == '教授'


@pytest.mark.parametrize('value', ['教授，\n研究员', '教授\n/研究员'])
def test_flat_text_without_dom_boundary_cannot_certify_a_prefix(value):
    from server.detail_mecc import explicit_title_fields
    from server.titles import select_title
    choice = select_title(explicit_fields=explicit_title_fields('职称：' + value))
    assert choice.conflicts and choice.title == '教师'


def test_media_enrichment_preserves_the_scoped_html_field_boundary(tmp_path):
    from server.browser_batch_tool import _enrich_extracted_records
    url = 'https://faculty.example.edu/p/1'
    html = '<article><p>职称：教授<br>/研究员</p><p>完整履历。</p></article>'
    detail = DetailMECCExtractor.extract_single(html, url, '王芳', preserve_content=True)
    row = {'姓名': '王芳', '主页': url, '职称': detail.title, '简介': detail.full_bio, '邮箱': ''}
    issues = _enrich_extracted_records(
        [row], {}, {'assigned_biographies': {(url, '王芳'): '更多原始履历。'}, 'evidence': []},
        tmp_path, {url: html}, [FacultyCandidate('王芳', url)])
    assert not issues and row['职称'] == '教授\n/研究员'
    assert row['简介'] == detail.full_bio + '\n\n更多原始履历。'


@pytest.mark.parametrize('value', ['教授；专业技术职务 副教授', '教授/'])
def test_incomplete_or_mixed_boundary_cannot_become_a_successful_field(value):
    html = f'<article><h1>王芳</h1><p>职称：{value}</p><p>完整科研履历。</p></article>'
    detail = DetailMECCExtractor.extract_single(html, 'https://faculty.example.edu/p/1',
                                              '王芳', preserve_content=True)
    assert detail.status == 'title_field_conflict'


def test_parenthetical_qualification_colon_is_not_another_field():
    value = '讲师（校内聘任：副教授）'
    detail = DetailMECCExtractor.extract_single(
        f'<article><h1>王芳</h1><p>职称：{value}</p></article>',
        'https://faculty.example.edu/p/1', '王芳', preserve_content=True)
    assert detail.title == value and detail.status == 'ok'


def test_a_biographical_paragraph_cannot_complete_a_dangling_title_field():
    detail = DetailMECCExtractor.extract_single(
        '<article><h1>王芳</h1><p>职称：教授/</p><p>2017年曾任研究员，现在负责科研。</p></article>',
        'https://faculty.example.edu/p/1', '王芳', preserve_content=True)
    assert detail.status == 'title_field_conflict'
    assert '2017年曾任研究员，现在负责科研。' in detail.full_bio
