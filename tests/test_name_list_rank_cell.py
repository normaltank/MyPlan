"""A rank cell beside a names cell is a separate clause, not the chain's first item.

Real case: 玉林师范学院教育科学学院师资队伍 (captured 2026-10-01) is a table whose rows are
'正高级 | 崔海波、陈庆文……'. Joined with a space, '正高级' ran into the first name and
became a rare-surname review item; '副高级' and '中级及以下' became unrecognized items.
"""
from server.name_list import is_name_list_page, reconcile_name_list

URL = 'https://fixture.example.edu/roster'
CARDS = 'table > tbody > tr:not(.firstRow)'


def table(*rows):
    return ('<table><tbody><tr class="firstRow"><td>职 称</td><td>姓 名</td></tr>'
            + ''.join(f'<tr><td><strong>&nbsp; {rank}</strong></td><td><p>{names}</p></td></tr>'
                      for rank, names in rows)
            + '</tbody></table>')


def test_rank_cells_are_not_list_items():
    html = table(('正高级', '王芳、李明、张伟'), ('副高级', '刘洋、陈静'), ('中级及以下', '赵磊、孙丽、周强'))
    assert is_name_list_page(html, CARDS)
    result = reconcile_name_list(html, URL, CARDS)
    assert sorted(person.name for person in result.found) == sorted(
        ['王芳', '李明', '张伟', '刘洋', '陈静', '赵磊', '孙丽', '周强'])
    assert not result.review
    assert not result.unresolved


def test_one_cell_row_keeps_its_text():
    html = '<table><tbody><tr><td>王芳、李明、正高级</td></tr></tbody></table>'
    result = reconcile_name_list(html, URL, 'table > tbody > tr')
    assert sorted(person.name for person in result.found) == ['李明', '王芳']
