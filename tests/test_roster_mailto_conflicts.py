"""Conflicting mailto targets remain audit evidence through roster extraction."""
import pytest

from server.faculty_extractor import FacultyDirectoryExtractor, FacultyHarvestPipeline


URL = 'https://fixture.example.edu/roster'
CARD = ('<li><span>王芳</span><p>教授</p><p>邮箱：'
        '<a href="mailto:wrong@example.edu">wangfang@example.edu</a></p></li>')


def harvest(candidates, pages=None):
    pages = pages or {}
    return FacultyHarvestPipeline.harvest_candidates(
        candidates, pages.get, base_url=URL, school='Fixture', college='College',
        verified_profile_urls=set(pages))


def assert_conflict(result):
    assert not result['delivery_ready']
    issues = [issue for issue in result['unresolved'] if issue['status'] == 'mailto_address_conflict']
    assert len(issues) == 1 and issues[0]['姓名'] == '王芳'
    assert set(issues[0]['matches']) == {'wrong@example.edu', 'wangfang@example.edu'}
    rows = result['sheet1_with_email'] + result['sheet2_no_email']
    assert next(row for row in rows if row['姓名'] == '王芳')['邮箱'] == ''


def test_profileless_verified_card_keeps_the_mailto_disagreement():
    candidates, review = FacultyDirectoryExtractor.extract_with_review(
        '<main><ul>' + CARD + '</ul></main>', URL, card_selector='main li')
    assert not review and candidates[0].url == URL
    assert_conflict(harvest(candidates))


@pytest.mark.parametrize('fallback', [False, True])
def test_unscoped_directory_paths_keep_their_own_card_conflict(monkeypatch, fallback):
    from server import faculty_extractor
    if fallback:
        monkeypatch.setattr(faculty_extractor, 'L2_AVAILABLE', False)
    names = ['王芳', '李华', '张三', '赵敏']
    html = '<main><ul>' + ''.join(
        f'<li><a href="/p/{index}">{name}</a><p>教授</p>'
        + ('<p>邮箱：<a href="mailto:wrong@example.edu">wangfang@example.edu</a></p>' if index == 0
           else f'<p>邮箱：<a href="mailto:teacher{index}@example.edu">teacher{index}@example.edu</a></p>')
        + '</li>' for index, name in enumerate(names)) + '</ul></main>'
    candidates = FacultyDirectoryExtractor.extract(html, URL)
    assert {candidate.name for candidate in candidates} == set(names)
    pages = {f'https://fixture.example.edu/p/{index}': f'<article><h1>{name}</h1><p>完整履历。</p></article>'
             for index, name in enumerate(names)}
    assert_conflict(harvest(candidates, pages))


@pytest.mark.parametrize('shared', [False, True])
def test_card_conflict_remains_blocking_with_a_valid_personal_or_shared_profile(shared):
    path = '/team' if shared else '/p/1'
    card = CARD.replace('<span>王芳</span>', f'<a href="{path}">王芳</a>')
    if shared:
        card += '<li><a href="/team">李华</a><p>讲师</p></li>'
    candidates, review = FacultyDirectoryExtractor.extract_with_review(
        '<main><ul>' + card + '</ul></main>', URL, card_selector='main li')
    assert not review
    body = '<article><h1>王芳</h1><p>完整独立履历。</p></article>'
    if shared:
        body += '<article><h1>李华</h1><p>完整同事履历。</p></article>'
    assert_conflict(harvest(candidates, {'https://fixture.example.edu' + path: '<main>' + body + '</main>'}))


def test_duplicate_profile_candidate_cannot_drop_later_card_evidence():
    card = CARD.replace('<span>王芳</span>', '<a href="/p/1">王芳</a>')
    conflicting, _ = FacultyDirectoryExtractor.extract_with_review(
        '<main><ul>' + card + '</ul></main>', URL, card_selector='main li')
    healthy, _ = FacultyDirectoryExtractor.extract_with_review(
        '<main><ul><li><a href="/p/1">王芳</a><p>教授</p></li></ul></main>',
        URL, card_selector='main li')
    pages = {'https://fixture.example.edu/p/1': '<article><h1>王芳</h1><p>完整履历。</p></article>'}
    result = harvest([*healthy, *conflicting], pages)
    assert_conflict(result)
    assert len(result['sheet1_with_email'] + result['sheet2_no_email']) == 1


def test_matching_card_mailto_remains_usable_without_adding_evidence_to_fields():
    card = CARD.replace('wrong@example.edu', 'wangfang@example.edu')
    candidates, review = FacultyDirectoryExtractor.extract_with_review(
        '<main><ul>' + card + '</ul></main>', URL, card_selector='main li')
    result = harvest(candidates)
    assert not review and result['delivery_ready'] and not result['unresolved']
    [row] = result['sheet1_with_email']
    assert row['邮箱'] == 'wangfang@example.edu' and row['简介'] == ''
    assert {'序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页'} <= set(row)
    assert 'mailto_conflicts' not in row
    assert candidates[0].card_context == '王芳\n教授\n邮箱：wangfang@example.edu'
