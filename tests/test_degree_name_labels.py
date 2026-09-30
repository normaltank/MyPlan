"""Name slots can carry an academic degree without changing the person's name."""
import pytest

from server.faculty_extractor import FacultyDirectoryExtractor
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters


@pytest.mark.parametrize('label,name', [('张三博士', '张三'), ('王建国博士', '王建国'),
                                       ('职占江博士', '职占江'), ('李博士', '李博士')])
def test_degree_is_separate_from_the_name_and_not_a_job_title(label, name):
    assert FacultyDirectoryExtractor._clean_name(label) == (name, '教师')


def test_degree_labels_do_not_hide_linked_roster_cards_behind_navigation():
    url = 'https://college.example.edu/faculty.htm'
    names = ['王建国', '李明华', '张国平', '陈学文', '赵晓明', '刘志强']
    html = ('<html><body><nav><ul><li><a href="/about">学院概况</a></li>'
            '<li><a href="/faculty">师资队伍</a></li></ul></nav><main><ul id="cards">'
            + ''.join(f'<li><a href="/person/{i}.htm">{name}博士</a></li>'
                      for i, name in enumerate(names)) + '</ul></main></body></html>')
    result = reconcile_rosters([CapturedRosterPage(url, html, {'card_selector': '#cards > li'})],
                              {'source_kind': 'university_faculty_roster'})
    assert {c.name for c in result.candidates} == set(names)
    assert result.unresolved == []
    assert result.observations[0]['unaccounted_cards'] == []


def test_degree_labels_keep_teachers_without_personal_urls():
    url = 'https://college.example.edu/faculty.htm'
    names = ['王建国', '李明华', '张国平', '陈学文']
    html = '<main><ul id="cards">' + ''.join(f'<li>{n}博士</li>' for n in names) + '</ul></main>'
    result = reconcile_rosters([CapturedRosterPage(url, html, {'card_selector': '#cards > li'})],
                              {'source_kind': 'university_faculty_roster'})
    assert {c.name for c in result.candidates} == set(names)
    assert all(c.url == url for c in result.candidates)
    assert result.unresolved == []


def test_job_title_suffixes_are_normalized_before_roster_group_scoring():
    url = 'https://college.example.edu/faculty.htm'
    names = ['王建国', '李明华', '张国平', '陈学文', '赵晓明', '刘志强']
    html = ('<nav><ul><li><a href="/about">学院概况</a></li>'
            '<li><a href="/faculty">师资队伍</a></li></ul></nav><ul id="cards">'
            + ''.join(f'<li><a href="/person/{i}.htm">{name}{"教授" if i < 3 else "博士"}</a></li>'
                      for i, name in enumerate(names)) + '</ul>')
    result = reconcile_rosters([CapturedRosterPage(url, html, {'card_selector': '#cards > li'})],
                              {'source_kind': 'university_faculty_roster'})
    assert {c.name for c in result.candidates} == set(names)
    assert {c.name for c in result.candidates if c.title == '教授'} == set(names[:3])
    assert result.unresolved == []


def test_padded_name_before_a_title_is_one_complete_name_slot():
    url = 'https://college.example.edu/faculty.htm'
    html = ('<ul id="cards"><li><a href="/person/1">王 奔 副教授</a></li>'
            '<li><a href="/person/2">张 国 平 教授</a></li>'
            '<li><a href="/person/3">李明博士</a></li>'
            '<li><a href="/person/4">赵晓明博士</a></li></ul>')
    result = reconcile_rosters([CapturedRosterPage(url, html, {'card_selector': '#cards > li'})], {})
    assert {c.name for c in result.candidates} == {'王奔', '张国平', '李明', '赵晓明'}
    assert result.unresolved == []
