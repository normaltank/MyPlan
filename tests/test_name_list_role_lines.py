"""A teacher named only on a role line ('主  任：李 炜') is never silently dropped.

Real cases (case library, docs/research/case-library/): 齐鲁师范学院音乐学院
师资队伍 (captured 2026-10-01) lists each 教研室 as '主任：X' then '成员：…';
the six 主任/副主任 appear in no member chain, and the college passed the
delivery gate 53 people short of 59. 宜春学院人工智能与信息工程学院 教师名单
(captured 2026-10-01) drops six 教研室主任 the same way. Role lines are not
member chains, so the people on them go to the existing name review and block
delivery until a person decides; a role-holder also listed as a member needs
nothing.
"""
from server import name_review
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
from server.name_list import ROLE_LINE_REASON, is_name_list_page, reconcile_name_list

QLNU_URL = "http://yyxyn.qlnu.edu.cn/szdw.htm"
# Condensed from the saved page: two 教研室, nbsp padding as captured.
QLNU = ('<div id="vsb_content">'
        '<p>声乐教研室：</p>'
        '<p>主&nbsp;&nbsp;任：李 炜</p>'
        '<p>副主任：徐定兴</p>'
        '<p><span>成&nbsp;&nbsp;员：桑&nbsp;&nbsp;潇 、 刘艳芳、迟</span><span>英娟、 徐 倩、孙彦</span><span>峰、刘 爽</span></p>'
        '<p>键盘教研室：</p>'
        '<p>主&nbsp;&nbsp;任：刘&nbsp; 源</p>'
        '<p>成&nbsp;&nbsp;员：窦曼莉、徐 荃、 王燕鸣、张晓蕾、杜 喆 、王紫薇</p>'
        '</div>')
QLNU_BRANCH = {"url": QLNU_URL, "single_page": True, "card_selector": "#vsb_content > p"}

JXYCU_URL = "https://sjxy.jxycu.edu.cn/jsmd/list.htm"
# Condensed from the saved page: a 系主任 who is also a member, and a 教研室主任 who is not.
JXYCU = ('<div class="wp_articlecontent">'
         '<p>一、电子系</p>'
         '<p>系主任：文汝红</p>'
         '<p>1. 人工智能教研室</p>'
         '<p>主任：王&nbsp; 玮</p>'
         '<p>成员： 方光伟 巢时刚 龙启福</p>'
         '<p>2. 电子信息教研室</p>'
         '<p>主任：李鑫洪</p>'
         '<p>成员：廖建庆 文汝红 付小平 李鑫洪 刘习杰</p>'
         '</div>')


def _pending(result):
    return [item for item in result.unresolved if item["status"] == name_review.STATUS]


def test_role_line_people_are_reviewed_not_dropped():
    assert is_name_list_page(QLNU, "#vsb_content > p")
    result = reconcile_name_list(QLNU, QLNU_URL, "#vsb_content > p")
    assert {person.name for person in result.found} == {
        "桑潇", "刘艳芳", "迟英娟", "徐倩", "孙彦峰", "刘爽",
        "窦曼莉", "徐荃", "王燕鸣", "张晓蕾", "杜喆", "王紫薇"}
    assert [(person.name, person.name_review_reason) for person in result.review] == [
        ("李炜", ROLE_LINE_REASON), ("徐定兴", ROLE_LINE_REASON), ("刘源", ROLE_LINE_REASON)]
    assert result.review[0].card_context == "主  任：李 炜"


def test_role_holder_listed_as_member_needs_no_review():
    result = reconcile_name_list(JXYCU, JXYCU_URL, "div.wp_articlecontent p")
    assert {"文汝红", "李鑫洪"} <= {person.name for person in result.found}
    # 巢 is a rare surname: that review is the existing one, not a role line.
    assert [person.name for person in result.review
            if person.name_review_reason == ROLE_LINE_REASON] == ["王玮"]


def test_role_line_only_teacher_blocks_delivery_until_reviewed():
    page = CapturedRosterPage(QLNU_URL, QLNU, QLNU_BRANCH)
    plan = {"school": "齐鲁师范学院", "college": "音乐学院", "branches": [QLNU_BRANCH]}
    initial = reconcile_rosters([page], plan)
    pending = _pending(initial)
    assert [(item["name"], item["reason"]) for item in pending] == [
        ("李炜", ROLE_LINE_REASON), ("徐定兴", ROLE_LINE_REASON), ("刘源", ROLE_LINE_REASON)]
    assert len(initial.candidates) == 12

    decisions = {item["id"]: {"id": item["id"], "verdict": "person", "verified_name": item["name"]}
                 for item in pending if item["name"] != "徐定兴"}
    pending_xu = next(item for item in pending if item["name"] == "徐定兴")
    decisions[pending_xu["id"]] = {"id": pending_xu["id"], "verdict": "not_person"}
    reviewed = reconcile_rosters([page], plan, name_decisions=decisions)
    assert not _pending(reviewed)
    names = {person.name for person in reviewed.candidates}
    assert {"李炜", "刘源"} <= names and "徐定兴" not in names
    assert len(reviewed.candidates) == 14


def test_role_holder_collected_on_another_page_of_the_college_needs_no_review():
    other_url = "http://yyxyn.qlnu.edu.cn/zrjs.htm"
    other = "<div id='r'><p>李炜、徐定兴、刘源、张三丰</p></div>"
    branches = [QLNU_BRANCH, {"url": other_url, "single_page": True, "card_selector": "#r > p"}]
    pages = [CapturedRosterPage(QLNU_URL, QLNU, branches[0]),
             CapturedRosterPage(other_url, other, branches[1])]
    result = reconcile_rosters(pages, {"school": "齐鲁师范学院", "college": "音乐学院",
                                       "branches": branches})
    assert not _pending(result)
