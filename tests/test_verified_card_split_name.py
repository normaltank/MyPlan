"""A verified table row may split one name across inline runs inside a single cell.

Real case: 贵州师范学院教育科学学院专任教师表 (captured 2026-10-01) writes one row's
name cell as '<span>杨</span> <span>曼</span>'. Leaf strings alone expose only '杨'.
"""
from server.faculty_extractor import FacultyDirectoryExtractor

URL = 'https://fixture.example.edu/roster'


def row(index, name_cell, title):
    return (f'<tr><td>{index}</td><td>{name_cell}</td><td>女</td>'
            f'<td>教育学</td><td>{title}</td></tr>')


def test_name_split_across_inline_runs_in_one_cell_is_read_as_one_name():
    html = ('<table><tbody><tr class="firstRow"><td>序号</td><td>姓名</td><td>性别</td>'
            '<td>专业</td><td>职称</td></tr>'
            + row(1, '<span>王 芳</span>', '教授')
            + row(2, '<span><span>杨</span> <span>曼</span></span>', '讲师')
            + '</tbody></table>')
    found, review = FacultyDirectoryExtractor.extract_with_review(
        html, URL, card_selector='table > tbody > tr:not(.firstRow)')
    assert not review
    assert [(person.name, person.title) for person in found] == [('王芳', '教授'), ('杨曼', '讲师')]


def test_cell_text_does_not_join_separate_cells():
    html = '<table><tbody>' + row(1, '<span>杨</span>', '讲师') + '</tbody></table>'
    found, review = FacultyDirectoryExtractor.extract_with_review(
        html, URL, card_selector='tbody > tr')
    assert not found and [person.name for person in review] == ['杨']
