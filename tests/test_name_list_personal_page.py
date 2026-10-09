"""A person's biography is not a roster merely because its prose has name-like words."""
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
from server.name_list import is_name_list_page, reconcile_name_list

URL = 'https://faculty.example.edu/person.htm'
BIOGRAPHY = (
    '<html><head><title>张伟教授简介</title></head><body>'
    '<div class="Article_Content">'
    '<p><span>张伟，男，中共党员，教授。先后毕业于安徽师范大学、复旦大学、南开大学。</span></p>'
    '<p><span>讲授多门课程，研究方向为宪法学、政治学、高教管理。</span></p>'
    '</div></body></html>'
)


def test_named_personal_article_does_not_become_a_name_list():
    assert not is_name_list_page(BIOGRAPHY, 'div.Article_Content')


def test_biography_follows_the_single_card_reader_without_false_list_issues():
    branch = {'url': URL, 'card_selector': 'div.Article_Content', 'single_page': True}
    result = reconcile_rosters([CapturedRosterPage(URL, BIOGRAPHY, branch)], {})
    assert [person.name for person in result.candidates] == ['张伟']
    assert result.unresolved == []
    assert result.observations[0]['visible_cards'] == 1
    assert result.observations[0]['extracted_candidates'] == 1


def test_a_name_in_the_page_title_does_not_override_separate_roster_cards():
    html = ('<title>张伟教授简介</title><main>'
            '<p>张伟、李明、王奔</p><p>赵强、刘文伟、孙小明</p></main>')
    assert is_name_list_page(html, 'main > p')
    result = reconcile_name_list(html, URL, 'main > p')
    assert len(result.found) == 6
    assert result.unresolved == []


def test_an_unknown_person_in_a_real_name_list_still_blocks():
    html = '<title>教师名单</title><p id="staff">张伟、李明、TBD、王奔</p>'
    assert is_name_list_page(html, '#staff')
    result = reconcile_name_list(html, URL, '#staff')
    assert {person.name for person in result.found} == {'张伟', '李明', '王奔'}
    assert [issue['item'] for issue in result.unresolved] == ['TBD']


def test_person_title_alone_does_not_override_a_single_roster_card():
    html = '<title>张伟教授简介</title><p id="staff">张伟、李明、TBD、王奔</p>'
    assert is_name_list_page(html, '#staff')
    result = reconcile_name_list(html, URL, '#staff')
    assert [issue['item'] for issue in result.unresolved] == ['TBD']


def test_a_different_opening_subject_does_not_match_the_page_title():
    html = BIOGRAPHY.replace('张伟，男', '李明，男')
    assert is_name_list_page(html, 'div.Article_Content')
