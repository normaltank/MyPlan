"""Public extraction paths share the approved title evidence precedence."""
import json
from pathlib import Path

import pytest

from server import detail_mecc, faculty_extractor as faculty
from server.titles import resolve_title


def profile(name, body):
    return f'<html><body><article><h1>{name}</h1>{body}</article></body></html>'


def harvest(pages, candidates, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr(faculty, 'DETAIL_MECC_AVAILABLE', False)
    return faculty.FacultyHarvestPipeline.harvest_candidates(
        candidates, profile_fetcher=pages.get, base_url='https://faculty.example.edu/list',
        school='样本大学', college='样本学院', verified_profile_urls=set(pages))


def rows(result):
    return result['sheet1_with_email'] + result['sheet2_no_email']


@pytest.mark.parametrize('label', [
    '<p>职称：教授</p>', '<p>专业技术职务：教授</p>',
    '<table><tr><th>职称</th><td>教授</td></tr></table>',
    '<p>职 称：教授</p>', '<p><span>职 称：</span><span>教授</span></p>',
])
def test_explicit_title_at_the_end_of_the_full_profile_wins(label):
    body = '<p>曾任讲师。</p><p>' + '完整教育与科研经历。' * 80 + '</p>' + label
    html = profile('王芳', body)
    actual = detail_mecc.DetailMECCExtractor.extract_single(html, 'https://faculty.example.edu/p/1',
                                                          '王芳', preserve_content=True)
    assert actual.title == '教授'
    assert '完整教育与科研经历。' * 80 in actual.full_bio
    assert actual.full_bio.endswith('教授')


def test_cluster_explicit_fields_override_old_biographical_titles():
    pages = [(f'https://faculty.example.edu/p/{i}', profile(name, '<p>曾任讲师。</p><p>职称：教授</p>'), name)
             for i, name in enumerate(['王芳', '李华'])]
    actual = detail_mecc.DetailMECCExtractor.extract_cluster(pages, scoped_urls={url for url, _, _ in pages})
    assert [row.title for row in actual] == ['教授', '教授']
    assert [row.full_bio for row in actual] == ['王芳\n曾任讲师。\n职称：教授', '李华\n曾任讲师。\n职称：教授']


def test_specific_roster_title_overrides_an_old_biographical_title():
    url = 'https://faculty.example.edu/p/1'
    actual = harvest({url: profile('王芳', '<p>曾任副教授，后晋升教授。</p>')},
                     [faculty.FacultyCandidate('王芳', url, '教授')])
    assert actual['delivery_ready']
    assert rows(actual)[0]['职称'] == '教授'
    assert rows(actual)[0]['简介'] == '王芳\n曾任副教授，后晋升教授。'


def test_explicit_profile_field_overrides_the_specific_roster_title():
    url = 'https://faculty.example.edu/p/1'
    actual = harvest({url: profile('王芳', '<p>曾任讲师。</p><p>职称：教授</p>')},
                     [faculty.FacultyCandidate('王芳', url, '副教授')])
    assert actual['delivery_ready'] and rows(actual)[0]['职称'] == '教授'


def test_shared_profiles_use_own_explicit_fields_then_own_roster_titles():
    url = 'https://faculty.example.edu/team'
    html = ('<main><article><h2>王芳</h2><p>曾任副教授。</p><p>职称：教授</p></article>'
            '<article><h2>李华</h2><p>现任教授，曾任副教授。</p></article></main>')
    actual = harvest({url: html}, [faculty.FacultyCandidate('王芳', url, '讲师'),
                                  faculty.FacultyCandidate('李华', url, '教授')])
    assert actual['delivery_ready']
    assert [(row['姓名'], row['职称']) for row in rows(actual)] == [('王芳', '教授'), ('李华', '教授')]
    assert rows(actual)[1]['简介'] == '李华\n现任教授，曾任副教授。'


def test_fallback_profile_and_harvest_use_the_same_explicit_field(monkeypatch):
    url = 'https://faculty.example.edu/p/1'
    html = profile('王芳', '<p>曾任副教授。</p><p>职称：教授</p>')
    monkeypatch.setattr(faculty, 'DETAIL_MECC_AVAILABLE', False)
    single = faculty.FacultyProfileExtractor.extract(html, url, '王芳', preserve_content=True)
    assert single.title == '教授'
    actual = harvest({url: html}, [faculty.FacultyCandidate('王芳', url, '讲师')])
    assert actual['delivery_ready'] and rows(actual)[0]['职称'] == '教授'
    assert rows(actual)[0]['简介'] == single.full_bio


@pytest.mark.parametrize('shared', [False, True])
def test_conflicting_explicit_fields_block_delivery_without_arbitrary_selection(shared):
    url = 'https://faculty.example.edu/p/1'
    html = profile('王芳', '<p>职称：教授</p><p>职称：副教授</p>')
    candidates = [faculty.FacultyCandidate('王芳', url, '讲师')]
    if shared:
        html = html.replace('</article>', '</article><article><h2>李华</h2><p>职称：讲师</p></article>')
        candidates.append(faculty.FacultyCandidate('李华', url, '教师'))
    actual = harvest({url: html}, candidates)
    assert not actual['delivery_ready']
    issues = [issue for issue in actual['unresolved'] if issue['status'] == 'title_field_conflict']
    assert len(issues) == 1 and issues[0]['姓名'] == '王芳'
    assert set(issues[0]['matches']) == {'教授', '副教授'}
    assert next(row for row in rows(actual) if row['姓名'] == '王芳')['职称'] == '教师'


def test_duplicate_explicit_fields_agree_without_conflict():
    url = 'https://faculty.example.edu/p/1'
    actual = harvest({url: profile('王芳', '<p>职称：教授</p><p>职称：教授</p>')},
                     [faculty.FacultyCandidate('王芳', url, '讲师')])
    assert actual['delivery_ready'] and rows(actual)[0]['职称'] == '教授'


def test_fallback_conflicting_fields_also_require_review(monkeypatch):
    url = 'https://faculty.example.edu/p/1'
    html = profile('王芳', '<p>职称：教授</p><p>职称：副教授</p>')
    actual = harvest({url: html}, [faculty.FacultyCandidate('王芳', url, '讲师')], monkeypatch)
    assert not actual['delivery_ready']
    assert [issue['status'] for issue in actual['unresolved']] == ['title_field_conflict']
    assert rows(actual)[0]['职称'] == '教师'


def test_late_unlabelled_first_title_is_read_without_truncating_the_profile():
    url = 'https://faculty.example.edu/p/1'
    html = profile('王芳', '<p>' + '完整教育经历。' * 100 + '</p><p>教授，从事教学科研。</p>')
    actual = harvest({url: html}, [faculty.FacultyCandidate('王芳', url)])
    assert actual['delivery_ready'] and rows(actual)[0]['职称'] == '教授'


def test_a_biographical_mention_of_the_field_label_is_not_an_explicit_field():
    url = 'https://faculty.example.edu/p/1'
    html = profile('王芳', '<p>此前专业技术职务：副教授，现在主持多个科研项目。</p>')
    actual = harvest({url: html}, [faculty.FacultyCandidate('王芳', url, '教授')])
    assert actual['delivery_ready'] and rows(actual)[0]['职称'] == '教授'


def test_title_conflict_does_not_change_the_independent_contact_evidence():
    url = 'https://faculty.example.edu/p/1'
    html = profile('王芳', '<p>职称：教授</p><p>职称：副教授</p><p>邮箱：1234567890@example.edu</p>')
    [actual] = detail_mecc.DetailMECCExtractor.extract_cluster([(url, html, '王芳')], scoped_urls={url})
    assert actual.status == 'title_field_conflict'
    assert actual.email == '1234567890@example.edu'


@pytest.mark.asyncio
async def test_segmented_pipeline_explicit_field_overrides_its_old_title(tmp_path):
    from server.browser_batch_tool import run_plan
    from test_browser_batch_tool import FakeContext
    url = 'https://faculty.example.edu/roster'
    html = ('<html><body><div class="roster"><h2 class="person">王芳</h2>'
            '<p>曾任副教授。</p><p>职称：教授</p></div></body></html>')
    plan = {'school': '样本大学', 'college': '样本学院', 'branches': [{
        'url': url, 'kind': 'segmented', 'single_page': True,
        'container_selector': 'div.roster', 'record_start_selector': 'h2.person'}]}
    report = await run_plan(plan, FakeContext({url: html}), tmp_path)
    assert report['delivery_ready'], report['unresolved']
    [row] = json.loads(Path(report['records_path']).read_text(encoding='utf-8'))
    assert row['职称'] == '教授'
    assert row['简介'] == '王芳\n曾任副教授。\n职称：教授'


@pytest.mark.parametrize('text, expected', [
    ('Assistant Professor，之后任讲师', '助理教授'),
    ('Associate Professor，之后任教授', '副教授'),
    ('讲师，之后任Assistant Professor', '讲师'),
])
def test_english_and_chinese_titles_are_compared_in_source_order(text, expected):
    assert resolve_title(text) == expected


@pytest.mark.parametrize('ocr_named', [False, True])
def test_image_and_text_explicit_fields_are_checked_together(tmp_path, ocr_named):
    from types import SimpleNamespace
    from server.browser_batch_tool import _enrich_extracted_records

    url = 'https://faculty.example.edu/p/1'
    text_bio = '王芳\n职称：副教授\n完整文字履历。'
    image_bio = '职称：教授\n完整图片履历。'
    record = {'姓名': '王芳', '主页': url, '职称': '讲师', '简介': text_bio, '邮箱': ''}
    segments = {(url, '王芳'): SimpleNamespace(
        segment=SimpleNamespace(text=text_bio), name_source='ocr', title='副教授', email='')
    } if ocr_named else {}
    media = {'assigned_biographies': {(url, '王芳'): image_bio}, 'evidence': []}
    issues = _enrich_extracted_records([record], segments, media, tmp_path, {}, [])
    assert record['简介'] == (image_bio + '\n\n' + text_bio if ocr_named
                            else text_bio + '\n\n' + image_bio)
    assert record['职称'] == '教师'
    assert any(issue['status'] == 'title_field_conflict'
               and set(issue['matches']) == {'教授', '副教授'} for issue in issues)


@pytest.mark.parametrize('mode', ['l2', 'fallback', 'verified'])
@pytest.mark.parametrize('card_body', [
    '<p>现任教授，曾任副教授。</p>',
    '<p>曾任副教授。</p><p><span>职 称：</span><span>教授</span></p>',
])
def test_reconciled_roster_cards_keep_the_approved_title_through_harvest(monkeypatch, mode, card_body):
    from server.harvest_workflow import CapturedRosterPage, reconcile_rosters

    monkeypatch.setattr(faculty, 'get_default_l1_executor', lambda: None)
    if mode == 'fallback':
        monkeypatch.setattr(faculty, 'L2_AVAILABLE', False)
    url = 'https://faculty.example.edu/list'
    names = ['王芳', '李华', '张明', '陈刚']
    html = '<main><ul id="cards">' + ''.join(
        f'<li><a href="/p/{i}">{name}</a>{card_body}</li>'
        for i, name in enumerate(names)) + '</ul></main>'
    branch = {'card_selector': '#cards > li'} if mode == 'verified' else {}
    roster = reconcile_rosters([CapturedRosterPage(url, html, branch)], {})
    assert roster.unresolved == ([] if mode == 'verified' else [
        {'url': url, 'status': 'card_selector_missing'}])
    assert {person.name for person in roster.candidates} == set(names)
    assert all(person.title == '教授' for person in roster.candidates)
    pages = {person.url: profile(person.name, '<p>曾任讲师，完整科研履历。</p>')
             for person in roster.candidates}
    result = harvest(pages, roster.candidates)
    assert result['delivery_ready']
    assert all(row['职称'] == '教授' and row['简介'].endswith('曾任讲师，完整科研履历。')
               for row in rows(result))


@pytest.mark.parametrize('raw', ['王芳（教授，副教授）', '教授、副教授：王芳'])
def test_name_annotation_titles_follow_original_order(raw):
    assert faculty.FacultyDirectoryExtractor._clean_name(raw) == ('王芳', '教授')


@pytest.mark.parametrize('own_field', ['', '<p>职称：讲师</p>'])
def test_conflicting_card_fields_require_review_unless_own_profile_field_is_authoritative(own_field):
    from server.harvest_workflow import CapturedRosterPage, reconcile_rosters

    url = 'https://faculty.example.edu/list'
    html = ('<ul id="cards"><li><a href="/p/1">王芳</a>'
            '<p>职称：教授</p><p>职称：副教授</p></li></ul>')
    roster = reconcile_rosters([CapturedRosterPage(url, html, {'card_selector': '#cards > li'})], {})
    assert not roster.unresolved
    actual = harvest({'https://faculty.example.edu/p/1': profile('王芳', '<p>曾任讲师。</p>' + own_field)},
                     roster.candidates)
    if own_field:
        assert actual['delivery_ready'] and rows(actual)[0]['职称'] == '讲师'
    else:
        assert not actual['delivery_ready'] and rows(actual)[0]['职称'] == '教师'
        assert actual['unresolved'][0]['status'] == 'title_field_conflict'
        assert set(actual['unresolved'][0]['matches']) == {'教授', '副教授'}
