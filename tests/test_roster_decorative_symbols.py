"""Decorative symbols on otherwise identical linked cards must not hide people."""
from server.faculty_extractor import FacultyDirectoryExtractor
from server.harvest_workflow import CapturedRosterPage, observe_roster, reconcile_rosters
from iso_mining import url_evidence


def test_verified_roster_counts_linked_cards_with_trailing_unicode_symbols():
    url = 'https://example.edu/roster'
    groups = [
        [('张三', False), ('李四', True), ('王五', False), ('陈六', True)],
        [('刘七', False), ('赵八', False), ('周九', False), ('吴十', False)],
    ]
    html = '<main id="roster">' + ''.join(
        '<h3>分类</h3><ul>' + ''.join(
            f'<li><a href="/person/{group_idx}-{i}.htm">{name}{"★" if marked else ""}</a></li>'
            for i, (name, marked) in enumerate(group)
        ) + '</ul>'
        for group_idx, group in enumerate(groups)
    ) + '</main>'
    found = FacultyDirectoryExtractor.extract(html, url, executor=None)
    assert len(found) == 8
    assert all('★' not in candidate.name for candidate in found)
    result = reconcile_rosters([CapturedRosterPage(url, html, {'card_selector': '#roster li'})], {})
    assert not result.unresolved
    assert len(result.candidates) == 8


def test_profile_links_are_not_self_references_when_cms_paths_share_a_template():
    page = 'https://example.edu/info/1105/2545.htm'
    profiles = ['https://example.edu/info/1011/1433.htm',
                'https://example.edu/info/1012/2537.htm']
    assert url_evidence(profiles, page)['self_ref'] == 0
    assert url_evidence([page, profiles[0]], page)['self_ref'] == 0.5


def test_linked_cards_win_over_text_list_with_extra_unlinked_tokens():
    url = 'https://example.edu/info/1106/2546.htm'
    names = ['张三', '李四', '王五', '赵六', '陈七', '刘八', '周九', '吴十',
             '孙甲', '郑乙', '马丙', '朱丁', '胡戊', '郭己', '何庚', '高辛', '林壬']
    html = '<article><p>师资简介<br>' + ''.join(
        f'<a href="/info/1011/{index}.htm">{name}</a> '
        for index, name in enumerate(names)
    ) + '学院概况</p></article>'
    found = FacultyDirectoryExtractor.extract(html, url)
    assert len(found) == len(names)
    observation = observe_roster(html, url, 'article p a', found)
    assert observation['visible_cards'] == len(names)
    assert not observation['unaccounted_cards']


def test_anchor_card_is_its_own_link_for_reconciliation():
    url = 'https://example.edu/roster'
    html = '<p><a href="/profile/1">张 三</a></p>'
    found = FacultyDirectoryExtractor.extract(html, url)
    assert len(found) == 1
    observation = observe_roster(html, url, 'p a', found)
    assert not observation['unaccounted_cards']
