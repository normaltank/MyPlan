"""An address is the teacher's own only when it spells that teacher, not a namesake.

Before this fix any local part that merely started with the surname (``wang…``
for every 王) or with two initials (``zsb`` 招生办 for 张三, ``jwc`` 教务处 for
金伟, ``yjs`` 研究生院 for 杨杰) counted as the teacher's spelling. A profile page
without a labelled contact field then delivered a colleague's, a student's or an
office mailbox in Sheet 1, with no review item. A prefix extension is only
accepted once the full given name has been spelled, or after the three initials
of a three-character name; surname and two-initial forms must match exactly
(digits and separators aside).
"""
import pytest

from server.detail_mecc import is_personal_email_for_scholar as mecc_affinity
from server.faculty_extractor import (FacultyCandidate, FacultyHarvestPipeline,
                                      is_personal_email_for_scholar)


@pytest.mark.parametrize("address,name", [
    ("wanglei@example.edu.cn", "王芳"),     # a colleague sharing only the surname
    ("wangfei@example.edu.cn", "王芬"),     # another person's given name after 'wangf'
    ("zsb@example.edu.cn", "张三"),         # 招生办
    ("jwc@example.edu.cn", "金伟"),         # 教务处
    ("yjs@example.edu.cn", "杨杰"),         # 研究生院
    ("wangxiaoming@example.edu.cn", "王晓磊"),  # the same first syllable, another given name
])
def test_namesakes_and_office_mailboxes_are_not_the_teachers_spelling(address, name):
    assert not is_personal_email_for_scholar(address, name)
    assert not mecc_affinity(address, name)


@pytest.mark.parametrize("address,name", [
    ("wangfang@example.edu.cn", "王芳"), ("wang.fang@example.edu.cn", "王芳"),
    ("wangfang2019@example.edu.cn", "王芳"), ("fang.wang@example.edu.cn", "王芳"),
    ("fwang@example.edu.cn", "王芳"), ("wangf@example.edu.cn", "王芳"), ("wfang@example.edu.cn", "王芳"),
    ("wf@example.edu.cn", "王芳"), ("wf2020@example.edu.cn", "王芳"), ("wang@example.edu.cn", "王芳"),
    # The full given name spelled, then a lab or school suffix, stays accepted.
    ("wangfanghnu@example.edu.cn", "王芳"), ("wwang2026@example.edu.cn", "王婉婷"),
    ("wangwt@example.edu.cn", "王婉婷"), ("zengli@example.edu.cn", "曾力"),
    ("8843120002@example.edu.cn", "王芳"), ("john.smith@example.edu", "John Smith"),
    ("jsmith@example.edu", "John Smith"),
    ("lsjwork@126.com", "刘圣洁"),         # three initials may carry a suffix
    ("wangxiaof@example.edu.cn", "王小芳"),  # given name cut short after a full syllable
])
def test_genuine_spellings_are_still_the_teachers(address, name):
    assert is_personal_email_for_scholar(address, name)
    assert mecc_affinity(address, name)


def _profile(name, body):
    return (f"<html><body><div class='content'><h1>{name}</h1>{body}"
            f"<p>{name}长期从事教学科研工作，主持多项课题。</p></div></body></html>")


def test_unlabelled_namesake_address_on_a_profile_is_reviewed_not_delivered():
    pages = {
        "https://x.example.edu.cn/p/1.htm": _profile("王芳", "<p>课题组联系人：王磊 wanglei@example.edu.cn</p>"),
        "https://x.example.edu.cn/p/2.htm": _profile("金伟", "<p>本科教学咨询：jwc@example.edu.cn</p>"),
        "https://x.example.edu.cn/p/3.htm": _profile("李华", "<p>联系方式：lihua@example.edu.cn</p>"),
    }
    names = {"https://x.example.edu.cn/p/1.htm": "王芳", "https://x.example.edu.cn/p/2.htm": "金伟",
             "https://x.example.edu.cn/p/3.htm": "李华"}
    candidates = [FacultyCandidate(name, url, "教授") for url, name in names.items()]
    result = FacultyHarvestPipeline.harvest_candidates(
        candidates, profile_fetcher=pages.get, base_url="https://x.example.edu.cn/list.htm",
        school="样本大学", college="样本学院")

    emails = {row["姓名"]: row["邮箱"] for row in result["sheet1_with_email"] + result["sheet2_no_email"]}
    assert emails["王芳"] == "" and emails["金伟"] == ""
    assert emails["李华"] == "lihua@example.edu.cn"   # still spelled, still delivered
    flagged = {(item["姓名"], item["status"]) for item in result["unresolved"]}
    assert {("王芳", "ambiguous_email_assignment"), ("金伟", "ambiguous_email_assignment")} <= flagged
    assert result["delivery_ready"] is False
