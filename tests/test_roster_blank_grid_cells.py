"""Blank filler cells of a name grid are not people the roster failed to account for.

Real case: 重庆电子科技职业大学人工智能与大数据学院教师名录 (captured 2026-10-01) lays
171 linked names into fixed-width tables whose last rows end in empty <td>&nbsp;</td>
cells; each blank cell was reported as an unaccounted card and blocked delivery.
"""
from server.harvest_workflow import CapturedRosterPage, observe_roster, reconcile_rosters

URL = 'https://example.edu/szdw/jsml.htm'
NAMES = ['张三', '李四', '王五', '赵六', '陈七']


def grid(*extra_cells):
    cells = ''.join(f'<td><a href="/info/{i}.htm">{name}</a></td>' for i, name in enumerate(NAMES))
    return (f'<div class="v_news_content"><table><tbody><tr>{cells}'
            + ''.join(extra_cells) + '</tr></tbody></table></div>')


def test_blank_grid_cells_are_counted_apart_and_do_not_block():
    html = grid('<td>&nbsp;</td>', '<td><p><br></p></td>', '<td></td>')
    result = reconcile_rosters([CapturedRosterPage(URL, html, {'card_selector': 'div.v_news_content td'})], {})
    assert not result.unresolved
    assert sorted(c.name for c in result.candidates) == sorted(NAMES)
    observation = result.observations[0]
    assert observation['visible_cards'] == len(NAMES) and observation['blank_cards'] == 3


def test_a_cell_with_only_an_image_or_a_link_is_not_blank():
    html = grid('<td><img src="/photo.jpg"></td>', '<td><a href="/info/99.htm"> </a></td>')
    observation = observe_roster(html, URL, 'div.v_news_content td', [])
    assert observation['blank_cards'] == 0
    assert len(observation['unaccounted_cards']) == len(NAMES) + 2
