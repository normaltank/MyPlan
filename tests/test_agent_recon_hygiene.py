"""红线 1 门禁测试：模型产出物只允许含 URL 与 CSS 选择器。

`server/agent_recon.py` 是模型与数据之间唯一的闸门，`check_plan_hygiene()`
是它的机械判据——CSS 选择器不可能含 CJK 字符，故任何含 CJK 的字符串
（除目标学校/学院名外）都是数据泄漏。
"""

from __future__ import annotations

import pytest

from server.agent_recon import (
    PlanHygieneError,
    check_plan_hygiene,
    extract_plan_json,
    validate_model_plan,
)

SCHOOL = "湖南师范大学"
COLLEGE = "国际汉语文化学院"

CLEAN_PLAN = {
    "school": SCHOOL,
    "college": COLLEGE,
    "source_kind": "university_faculty_roster",
    "branches": [
        {
            "url": "https://icclc.hunnu.edu.cn/szdw/js.htm",
            "card_selector": "div.list_container ul.teacher > li",
            "single_page": True,
        },
        {
            "url": "https://icclc.hunnu.edu.cn/szdw/fjs.htm",
            "card_selector": ".teacher-list > .teacher-card",
            "next_selector": ".pagination a.p-next",
            "max_pages": 5,
        },
    ],
}


# ── 应放行 ────────────────────────────────────────────────────────────────
def test_clean_plan_passes():
    check_plan_hygiene(CLEAN_PLAN, school=SCHOOL, college=COLLEGE)


def test_school_and_college_names_are_allowed():
    """目标学校/学院名是 schema 值，不是数据字段。"""
    plan = {**CLEAN_PLAN}
    check_plan_hygiene(plan, school=SCHOOL, college=COLLEGE)


def test_urls_with_chinese_path_segments_pass():
    """URL 里出现中文（部分高校站点如此）不算数据泄漏——它是地址，不是字段。"""
    plan = {
        "school": SCHOOL, "college": COLLEGE,
        "source_kind": "university_faculty_roster",
        "branches": [{"url": "https://x.edu/师资队伍/教授.htm",
                      "card_selector": "ul.list > li", "single_page": True}],
    }
    check_plan_hygiene(plan, school=SCHOOL, college=COLLEGE)


# ── 应拦截 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("leak,label", [
    ("张伟", "教师姓名"),
    ("傅新球", "教师姓名（罕见姓氏）"),
    ("zhangwei@hunnu.edu.cn", "邮箱"),
    ("教授", "职称"),
    ("主要研究方向为计算语言学", "简介片段"),
])
def test_data_fields_are_rejected(leak: str, label: str):
    """任何非 URL、非选择器的中文内容都必须被拦下。"""
    plan = {**CLEAN_PLAN}
    plan["branches"] = [
        {**CLEAN_PLAN["branches"][0], "note": leak},
    ]
    with pytest.raises(PlanHygieneError):
        check_plan_hygiene(plan, school=SCHOOL, college=COLLEGE)


def test_leak_deep_in_nested_structure_is_caught():
    """深藏在嵌套结构里的泄漏同样要被抓到。"""
    plan = {
        "school": SCHOOL, "college": COLLEGE,
        "source_kind": "university_faculty_roster",
        "branches": [{
            "url": "https://x.edu/a.htm",
            "card_selector": "ul > li",
            "single_page": True,
            "poster_reviews": [{"source_url": "https://x.edu/a.htm",
                                "verified_candidates": [{"name": "李娜"}]}],
        }],
    }
    with pytest.raises(PlanHygieneError) as exc:
        check_plan_hygiene(plan, school=SCHOOL, college=COLLEGE)
    assert "李娜" in str(exc.value)


def test_hospital_roster_source_kind_is_an_allowed_literal():
    plan = {
        "school": SCHOOL, "college": COLLEGE,
        "source_kind": "hospital_doctor_roster",
        "branches": [{"url": "https://x.edu/a.htm",
                      "card_selector": "ul > li", "single_page": True}],
    }
    check_plan_hygiene(plan, school=SCHOOL, college=COLLEGE)


# ── JSON 抽取 ─────────────────────────────────────────────────────────────
def test_extract_from_fenced_json():
    text = f"说明文字\n```json\n{__import__('json').dumps(CLEAN_PLAN, ensure_ascii=False)}\n```\n尾注"
    assert extract_plan_json(text) == CLEAN_PLAN


def test_extract_bare_json_with_nested_braces():
    text = '前置噪声 {"a": {"b": 1}, "c": [{"d": 2}]} 后置噪声'
    assert extract_plan_json(text) == {"a": {"b": 1}, "c": [{"d": 2}]}


def test_extract_returns_none_when_absent():
    assert extract_plan_json("完全没有 JSON 的一段话") is None
    assert extract_plan_json("") is None


def test_model_plan_rejects_extra_person_fields_even_in_ascii():
    plan = {**CLEAN_PLAN, "branches": [{**CLEAN_PLAN["branches"][0],
                                         "name": "Alice"}]}
    with pytest.raises(PlanHygieneError, match="name"):
        validate_model_plan(plan, school=SCHOOL, college=COLLEGE)


def test_model_plan_must_match_queued_target():
    plan = {**CLEAN_PLAN, "school": COLLEGE}
    with pytest.raises(PlanHygieneError, match="school"):
        validate_model_plan(plan, school=SCHOOL, college=COLLEGE)


def test_model_plan_rejects_email_embedded_in_url():
    plan = {**CLEAN_PLAN, "branches": [{**CLEAN_PLAN["branches"][0],
        "url": "https://x.edu/roster?contact=alice%40x.edu"}]}
    with pytest.raises(PlanHygieneError, match="url"):
        validate_model_plan(plan, school=SCHOOL, college=COLLEGE)
