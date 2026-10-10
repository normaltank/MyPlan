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
        tmp_path, {url: html}, [FacultyCandidate('王芳', url)], verified_profile_urls={url})
    assert not issues and row['职称'] == '教授\n/研究员'
    assert row['简介'] == detail.full_bio + '\n\n更多原始履历。'


def test_unverified_capture_cannot_donate_sidebar_title_during_media_enrichment(tmp_path):
    from server.browser_batch_tool import _enrich_extracted_records
    url = 'https://faculty.example.edu/p/1'
    raw = '<aside><p>职称：讲师</p></aside><article><p>职称：教授</p></article>'
    row = {'姓名': '王芳', '主页': url, '职称': '教授', '简介': '职称：教授', '邮箱': ''}
    issues = _enrich_extracted_records(
        [row], {}, {'assigned_biographies': {(url, '王芳'): '更多原始履历。'}, 'evidence': []},
        tmp_path, {url: raw}, [FacultyCandidate('王芳', url)], verified_profile_urls=set())
    assert not issues and row['职称'] == '教授'


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


@pytest.mark.parametrize(('body', 'expected'), [
    ('<p>职称：</p><p>教授/研究员</p>', '教授/研究员'),
    ('职称：教授/研究员<br>完整科研履历。', '教授/研究员'),
])
def test_separate_dom_label_and_direct_article_text_keep_authoritative_fields(body, expected):
    url = 'https://faculty.example.edu/p/1'
    html = f'<article><h1>王芳</h1>{body}</article>'
    detail = DetailMECCExtractor.extract_single(html, url, '王芳', preserve_content=True)
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate('王芳', url, '讲师')], profile_fetcher=lambda _: html,
        base_url='https://faculty.example.edu/list', school='样本大学', college='样本学院',
        verified_profile_urls={url})
    assert detail.title_fields and detail.title == expected
    assert result['delivery_ready'] and not result['unresolved']
    row, = result['sheet1_with_email'] + result['sheet2_no_email']
    assert row['职称'] == expected and row['简介'] == detail.full_bio


def test_separate_label_paragraphs_preserve_distinct_field_conflicts():
    url = 'https://faculty.example.edu/p/1'
    html = ('<article><h1>王芳</h1><p>职称：</p><p>教授</p>'
            '<p>专业技术职务：</p><p>研究员</p></article>')
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate('王芳', url, '讲师')], profile_fetcher=lambda _: html,
        base_url='https://faculty.example.edu/list', school='样本大学', college='样本学院',
        verified_profile_urls={url})
    assert not result['delivery_ready']
    issue, = [item for item in result['unresolved'] if item['status'] == 'title_field_conflict']
    assert set(issue['matches']) == {'教授', '研究员'}


@pytest.mark.parametrize('intervening', ['邮箱：wf@example.edu', '<h2>李华</h2>'])
def test_label_value_pair_cannot_cross_an_intervening_field_or_person(intervening):
    url = 'https://faculty.example.edu/p/1'
    html = f'<article><h1>王芳</h1><p>职称：</p>{intervening}<p>教授</p></article>'
    detail = DetailMECCExtractor.extract_single(html, url, '王芳', preserve_content=True)
    assert not detail.title_fields


@pytest.mark.parametrize('separator', ['；', '。', '！', '?', '—', ''])
def test_another_top_level_field_label_is_a_boundary_without_punctuation_rules(separator):
    value = f'教授{separator}专业技术职务 副教授'
    detail = DetailMECCExtractor.extract_single(
        f'<article><h1>王芳</h1><p>职称：{value}</p></article>',
        'https://faculty.example.edu/p/1', '王芳', preserve_content=True)
    assert detail.status == 'title_field_conflict'


def test_direct_article_multiline_title_keeps_flat_boundary_doubt():
    detail = DetailMECCExtractor.extract_single(
        '<article><h1>王芳</h1>职称：教授<br>/研究员</article>',
        'https://faculty.example.edu/p/1', '王芳', preserve_content=True)
    assert detail.status == 'title_field_conflict'
    assert detail.title_fields[0].raw_value == '教授\n/研究员'


@pytest.mark.parametrize('tail', ['讲师（内聘副教授）', '（校内聘任副教授）'])
def test_flat_qualified_title_cannot_silently_disappear_after_a_field(tail):
    detail = DetailMECCExtractor.extract_single(
        f'<article><h1>王芳</h1>职称：教授<br>{tail}</article>',
        'https://faculty.example.edu/p/1', '王芳', preserve_content=True)
    assert detail.status == 'title_field_conflict'
    assert detail.title_fields[0].raw_value == '教授\n' + tail


@pytest.mark.parametrize('own_fields', [False, True])
def test_multiple_people_in_one_card_cannot_share_an_unassigned_title_field(own_fields):
    from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
    url = 'https://faculty.example.edu/list'
    html = ('<ul id=cards><li><a href=/p/1>王芳</a><a href=/p/2>李华</a>'
            '<p>职称：教授/研究员</p></li></ul>')
    roster = reconcile_rosters([CapturedRosterPage(url, html, {'card_selector': '#cards > li'})], {})
    assert {person.name for person in roster.candidates} == {'王芳', '李华'}
    assert all(person.title_fields and person.title_fields[0].ambiguous for person in roster.candidates)
    pages = {person.url: f'<article><h1>{person.name}</h1><p>完整本人履历。</p>'
             + ('<p>职称：讲师</p>' if own_fields else '') + '</article>' for person in roster.candidates}
    result = FacultyHarvestPipeline.harvest_candidates(roster.candidates, profile_fetcher=pages.get,
        base_url=url, school='样本大学', college='样本学院', verified_profile_urls=set(pages))
    rows = result['sheet1_with_email'] + result['sheet2_no_email']
    assert len(rows) == 2 and all('完整本人履历。' in row['简介'] for row in rows)
    assert result['delivery_ready'] == own_fields
    if own_fields:
        assert all(row['职称'] == '讲师' for row in rows) and not result['unresolved']
    else:
        assert sum(issue['status'] == 'title_field_conflict' for issue in result['unresolved']) == 2
