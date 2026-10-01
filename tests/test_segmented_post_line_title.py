"""A '职务：' line between a record's name and its biography does not hide the title.

Real case: 曲靖师范学院数学与经济学学院 副教授 article (captured 2026-10-01) writes each
record as '姓名：X / 职务：学院党委书记 / 简介：副教授，硕士……'. The title search
looked at the first two lines only, so every record with a post line got '教师'.
"""
from server.segmented_roster import reconcile_page

URL = "https://stxy.example.edu.cn/contents/12648/28440.html"
BRANCH = {"kind": "segmented", "container_selector": "div.infodetail",
          "record_start_selector": "div.infodetail > p:has(img)", "single_page": True}


def record(photo, name, post, bio):
    lines = [f'<p><strong><img src="/{photo}.jpg">姓名：{name}</strong></p>']
    if post:
        lines.append(f"<p>职务：{post}</p>")
    lines.append(f"<p>简介：{bio}</p>")
    return "".join(lines)


def test_title_after_post_line():
    html = ('<html><body><div class="infodetail">'
            + record("a", "张伟", "学院党委书记", "副教授，硕士，主要承担微观经济学等课程的教学工作。")
            + record("b", "李娜", "", "副教授，博士，主要承担高等代数等课程的教学工作。")
            + record("c", "王强", "经济统计系主任", "讲师，硕士，主要从事统计学研究。")
            + "</div></body></html>")
    result = reconcile_page(html, URL, BRANCH)
    assert [(record.name, record.title) for record in result.records] == [
        ("张伟", "副教授"), ("李娜", "副教授"), ("王强", "讲师")]
    assert result.unresolved == []
