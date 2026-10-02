"""Blank rows between records of a segmented article are not unnamed people.

Real case: 黄山学院数学与统计学院 教研室介绍 (captured 2026-10-01) lays each teacher
as one table row (photo cell + biography cell) and leaves fully empty rows
between some of them; each empty row was reported as segment_name_missing.
"""
from server.segmented_roster import reconcile_page, verification_summary

URL = "https://sx.example.edu.cn/bb/84/c2706a179076/page.htm"
BRANCH = {"kind": "segmented", "container_selector": "div.wp_articlecontent",
          "record_start_selector": "div.wp_articlecontent table > tbody > tr", "single_page": True}


def row(cells):
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def article(*rows):
    return ('<html><body><div class="wp_articlecontent"><table><tbody>'
            + "".join(rows) + "</tbody></table></div></body></html>")


def test_blank_rows_are_counted_apart_from_records():
    html = article(row(['<p><img src="/a.jpg"></p>', "<p>张伟，男，理学博士，教授。</p>"]),
                   row(["<p><br></p>", "<p>&nbsp;</p>"]),
                   row(['<p><img src="/b.jpg"></p>', "<p>李娜，女，讲师，主讲高等数学。</p>"]))
    result = reconcile_page(html, URL, BRANCH)
    assert [record.name for record in result.records] == ["张伟", "李娜"]
    assert result.unresolved == []
    assert result.observation["blank_segments"] == 1
    summary = verification_summary(html, URL, BRANCH["container_selector"], BRANCH["record_start_selector"])
    assert summary["structure_ok"]


def test_a_row_with_only_a_photo_still_needs_a_name():
    html = article(row(['<p><img src="/a.jpg"></p>', "<p>张伟，男，理学博士，教授。</p>"]),
                   row(['<p><img src="/c.jpg"></p>', "<p></p>"]))
    result = reconcile_page(html, URL, BRANCH)
    assert result.observation["blank_segments"] == 0
    assert len(result.pending_ocr) == 1
