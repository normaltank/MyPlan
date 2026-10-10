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
