"""A name list whose editor split names across inline runs keeps each name whole.

Real case: 齐鲁师范学院音乐学院 师资队伍 (captured 2026-10-01) writes
'迟</span><span>英娟、' and '孙彦</span></span><span><span>峰、'. Joining runs with a
space cut them into '迟' + '英娟' and '孙彦' + '峰'.
"""
from server.name_list import is_name_list_page, reconcile_name_list

URL = 'https://fixture.example.edu/szdw.htm'


def test_inline_runs_do_not_split_names():
    html = ('<div id="c"><p><span><span>成&nbsp;&nbsp;员：王&nbsp;&nbsp;芳<span>、</span>刘艳芳、陈</span>'
            '<span>晓燕、</span> <span>徐</span> <span>倩、李建</span></span>'
            '<span><span>国、张</span> &nbsp;<span>帅、赵明</span></span></p></div>')
    assert is_name_list_page(html, '#c > p')
    result = reconcile_name_list(html, URL, '#c > p')
    assert sorted(person.name for person in result.found) == sorted(
        ['王芳', '刘艳芳', '陈晓燕', '徐倩', '李建国', '张帅', '赵明'])
    assert not result.review
    assert not result.unresolved


def test_block_elements_still_separate_text():
    html = '<div id="c"><div>王芳、李明<br>张伟、赵强</div></div>'
    result = reconcile_name_list(html, URL, '#c > div')
    assert sorted(person.name for person in result.found) == ['张伟', '李明', '王芳', '赵强']
