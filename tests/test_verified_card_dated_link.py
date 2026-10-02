"""A news-list roster may put the publish date inside the name link.

Real case: 池州学院大数据与人工智能学院教授名录 (captured 2026-10-02) writes each entry as
'<a><span>2026-05-14</span>班爱玲（副教授）</a>', so the link text begins with the date.
"""
from server.faculty_extractor import FacultyDirectoryExtractor

URL = 'https://fixture.example.edu/roster'


def item(href, date, label):
    return f'<li><a href="{href}"><span>{date}</span>{label}</a></li>'


def test_publish_date_inside_the_link_is_not_part_of_the_name():
    html = ('<ul class="list">'
            + item('/info/1.htm', '2026-05-14', '班爱玲（副教授）')
            + item('/info/2.htm', '2023-12-28', '张 永(教授/博士)')
            + item('/info/3.htm', '2023-12-29', '王巍（副教授）')
            + item('/info/4.htm', '2023-12-29', '陈念（教授）')
            + '</ul>')
    found, review = FacultyDirectoryExtractor.extract_with_review(
        html, URL, card_selector='ul.list > li')
    assert not review
    assert [(person.name, person.title) for person in found] == [
        ('班爱玲', '副教授'), ('张永', '教授'), ('王巍', '副教授'), ('陈念', '教授')]


def test_a_date_alone_is_not_a_name():
    html = '<ul class="list">' + item('/info/1.htm', '2026-05-14', '') + '</ul>'
    found, review = FacultyDirectoryExtractor.extract_with_review(
        html, URL, card_selector='ul.list > li')
    assert not found
