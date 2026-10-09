"""A word in brackets after a name annotates that person; it is never a second teacher.

集美大学诚毅学院 cards read '钟俊华 讲师 (金融学)': '金' is a surname, so '金融学' was
taken as another person on the same link, sharing the page and email with the real
teacher. 池州学院 '杨利（高级实验师）' gave '高级' the same way.
"""
from __future__ import annotations

from server.faculty_extractor import FacultyDirectoryExtractor
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters

ROSTER_URL = "https://cyjj.x.edu.cn/xbgk/szjs/2.htm"


def _roster(rows):
    cards = "".join(
        f'<li><a href="../../info/{i}.htm"><div><p>05/06</p><span>2025</span></div>'
        f'<div><h3>{head}</h3><p>{summary}</p></div></a></li>'
        for i, (head, summary) in enumerate(rows))
    return f"<html><body><ul class='list'>{cards}</ul></body></html>"


ROWS = [
    ("林  静 副教授 (财政学)", "姓名林静职称副教授电子邮箱a@x.edu.cn学习工作经历厦门大学财政系"),
    ("钟俊华 讲师 (金融学)", "姓名钟俊华职称讲师电子邮箱b@x.edu.cn学习工作经历中国人民大学金融系"),
    ("严玉华 副教授 （金融学）", "姓名严玉华职称副教授学习工作经历长春税务学院金融系金融学专业"),
    ("黄晓虹 副教授 (金融学)", "姓名黄晓虹职称副教授学习工作经历"),
]


def _read(html, selector="ul.list > li"):
    found, review = FacultyDirectoryExtractor._verified_card_candidates(html, ROSTER_URL, selector)
    return [person.name for person in found], [person.name for person in review]


def test_bracketed_subject_is_not_a_second_person_on_the_card():
    # The summary repeats '金融学' outside brackets; the token was still read
    # from the bracketed annotation, which is what decides.
    found, review = _read(_roster(ROWS))
    assert found == ["林静", "钟俊华", "严玉华", "黄晓虹"]
    assert review == []


def test_bracketed_title_after_a_dated_name_is_not_a_person():
    html = ("<ul class='list'>" + "".join(
        f'<li><a href="/info/{i}.htm">2023-12-29 {name}（高级实验师）</a></li>'
        for i, name in enumerate(["杨利", "王巍", "陈念", "刘妍"])) + "</ul>")
    found, _ = _read(html)
    assert found == ["杨利", "王巍", "陈念", "刘妍"]


def test_cards_and_candidates_now_reconcile():
    page = CapturedRosterPage(ROSTER_URL, _roster(ROWS), {"card_selector": "ul.list > li"})
    result = reconcile_rosters([page], {"source_kind": "university_faculty_roster"})
    assert not result.unresolved
    assert result.observations[0]["visible_cards"] == result.observations[0]["extracted_candidates"] == 4


def test_two_linked_people_on_one_card_are_still_both_read():
    html = ('<table><tr><td><a href="/p/1.htm">王建国</a>（兼任）'
            '<a href="/p/2.htm">李明</a></td></tr></table>')
    found, _ = _read(html, "td")
    assert sorted(found) == ["李明", "王建国"]
