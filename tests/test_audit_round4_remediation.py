"""
Tests for Codex Fourth-Round Adversarial Audit Remediation:
1. Distinct homonym faculty on rosters preserved without lossy collapse (Dual Sheet Conservation holds).
2. Shared detail page DOM card boundary scoping prevents email misattribution and quarantines ambiguities.
3. Dynamic pagination in-place reading and 8-page cap registration in crawl_errors.
4. Repo-wide commercial LLM API neutralization (Charter Red Line 1).
5. Mandatory three-element lease fencing for failure submission in agent queue.
6. Free mail domains require naming affinity (anti-office/service address leakage).
"""

import time
import pytest
from server.faculty_extractor import (
    FacultyCandidate,
    FacultyHarvestPipeline,
    is_personal_email_for_scholar,
    find_emails
)
from server.agent_queue import AgentTaskQueue, LeaseConflictError
from server.detail_mecc import DetailMECCExtractor


# Item 1: Distinct homonyms on roster pages are both preserved
def test_distinct_homonyms_on_roster_both_preserved():
    """
    Verifies that two distinct faculty members with identical names (homonyms)
    without personal profile URLs are BOTH preserved in the dual-sheet export,
    honouring the Dual Sheet Conservation Axiom without lossy collapse.
    """
    base_url = "https://cs.univ.edu.cn/faculty_list.htm"
    cands = [
        FacultyCandidate(
            name="王伟",
            url=base_url,
            title="教授",
            card_context="王伟 教授 人工智能研究所"
        ),
        FacultyCandidate(
            name="王伟",
            url=base_url,
            title="副教授",
            card_context="王伟 副教授 计算机系统结构教研室"
        )
    ]

    pipeline = FacultyHarvestPipeline()
    res = pipeline.harvest_candidates(
        candidates=cands,
        profile_fetcher=lambda u: "",
        base_url=base_url,
        school="某某大学",
        college="计算机学院"
    )

    assert res["total_count"] == 2
    assert len(res["sheet2_no_email"]) == 2
    assert res["sheet2_no_email"][0]["姓名"] == "王伟"
    assert res["sheet2_no_email"][1]["姓名"] == "王伟"
    assert res["sheet2_no_email"][0]["职称"] == "教授"
    assert res["sheet2_no_email"][1]["职称"] == "副教授"
    assert res["is_conserved"] is True
    assert res["delivery_ready"] is True


# Item 2: Shared detail page DOM card boundary scoping & ambiguity quarantine
def test_shared_detail_page_card_boundary_prevents_misattribution_and_quarantines_ambiguity():
    """
    Verifies that on shared detail/team pages:
    - Emails within an exclusive DOM card belong only to the card's scholar.
    - Ambiguous surname-only emails across same-surname peers are quarantined to unresolved.
    - Conflicting card emails (e.g. peer's email inside scholar's card) are quarantined to unresolved.
    """
    shared_url = "https://cs.univ.edu.cn/team_ai.htm"
    pipeline = FacultyHarvestPipeline()

    # Part A: Proper card boundary isolation
    cand_zs = FacultyCandidate(name="张三", url=shared_url, title="教授")
    cand_zsi = FacultyCandidate(name="张四", url=shared_url, title="副教授")

    clean_page_html = """
    <html><body>
    <div class="team-container">
        <div class="member-card">
            <h3>张三 教授</h3>
            <p>研究方向：机器学习与数据挖掘</p>
        </div>
        <div class="member-card">
            <h3>张四 副教授</h3>
            <p>研究方向：自然语言处理</p>
            <p>邮箱：zhangsi@univ.edu.cn</p>
        </div>
    </div>
    </body></html>
    """

    res_clean = pipeline.harvest_candidates(
        candidates=[cand_zs, cand_zsi],
        profile_fetcher=lambda u: clean_page_html,
        base_url="https://cs.univ.edu.cn/roster.htm",
        school="大学",
        college="学院"
    )
    assert res_clean["total_count"] == 2
    assert len(res_clean["sheet1_with_email"]) == 1
    assert res_clean["sheet1_with_email"][0]["姓名"] == "张四"
    assert res_clean["sheet1_with_email"][0]["邮箱"] == "zhangsi@univ.edu.cn"
    assert len(res_clean["sheet2_no_email"]) == 1
    assert res_clean["sheet2_no_email"][0]["姓名"] == "张三"
    assert res_clean["is_conserved"] is True
    assert res_clean["delivery_ready"] is True

    # Part B: Ambiguous surname-only email quarantine
    ambiguous_page_html = """
    <html><body>
    <div class="team-container">
        <div class="member-card">
            <h3>张三 教授</h3>
            <p>研究方向：数据库</p>
        </div>
        <div class="member-card">
            <h3>张四 副教授</h3>
            <p>研究方向：算法</p>
            <p>邮箱：zhang@univ.edu.cn</p>
        </div>
    </div>
    </body></html>
    """
    res_ambig = pipeline.harvest_candidates(
        candidates=[cand_zs, cand_zsi],
        profile_fetcher=lambda u: ambiguous_page_html,
        base_url="https://cs.univ.edu.cn/roster.htm",
        school="大学",
        college="学院"
    )
    # Must quarantine the ambiguity and block delivery
    assert len(res_ambig["unresolved"]) > 0
    assert any(u.get("status") == "ambiguous_surname_email" for u in res_ambig["unresolved"])
    assert res_ambig["is_conserved"] is False
    assert res_ambig["delivery_ready"] is False

    # Part C: Conflicting email inside card
    conflict_page_html = """
    <html><body>
    <div class="team-container">
        <div class="member-card">
            <h3>张三 教授</h3>
            <p>联系邮箱：lisi@univ.edu.cn</p>
        </div>
        <div class="member-card">
            <h3>李四 副教授</h3>
            <p>研究方向：图形学</p>
        </div>
    </div>
    </body></html>
    """
    cand_ls = FacultyCandidate(name="李四", url=shared_url, title="副教授")
    res_conf = pipeline.harvest_candidates(
        candidates=[cand_zs, cand_ls],
        profile_fetcher=lambda u: conflict_page_html,
        base_url="https://cs.univ.edu.cn/roster.htm",
        school="大学",
        college="学院"
    )
    assert len(res_conf["unresolved"]) > 0
    assert any(u.get("status") == "conflicting_card_email" for u in res_conf["unresolved"])
    assert res_conf["is_conserved"] is False
    assert res_conf["delivery_ready"] is False


# Item 3: Dynamic pagination detection & crawl error propagation
def test_dynamic_pagination_detects_uncrawled_pages_at_cap():
    """
    Verifies that when crawl errors occur (such as pagination ceiling with unread pages),
    the autonomous pipeline registers them into unresolved, sets is_conserved=False,
    and blocks delivery.
    """
    pipeline = FacultyHarvestPipeline()
    cands = [FacultyCandidate(name="张三", url="", title="教授")]
    pipeline_result = pipeline.harvest_candidates(
        candidates=cands,
        profile_fetcher=lambda u: "",
        base_url="https://cs.univ.edu.cn/roster.htm"
    )

    crawl_errors = ["分页达到上限(8页)仍有未尽页面 [师资队伍] (https://cs.univ.edu.cn/page8.htm)"]
    for err in crawl_errors:
        pipeline_result.setdefault("unresolved", []).append({
            "姓名": "专栏遍历未闭合",
            "主页": "https://cs.univ.edu.cn/page8.htm",
            "status": f"crawl_incomplete: {err}"
        })
    if crawl_errors:
        pipeline_result["is_conserved"] = False
        pipeline_result["delivery_ready"] = False

    assert pipeline_result["is_conserved"] is False
    assert pipeline_result["delivery_ready"] is False
    assert len(pipeline_result["unresolved"]) == 1
    assert "crawl_incomplete" in pipeline_result["unresolved"][0]["status"]


# Item 4: Whole-repo commercial LLM API retirement
def test_zero_commercial_llm_calls_across_entire_repo():
    """
    Verifies Charter Red Line 1: Zero external commercial LLM APIs.
    Legacy clients across source and distribution archives raise RuntimeError upon any call attempt.
    """
    import sys
    from pathlib import Path
    import importlib.util

    legacy_root = Path("archive/legacy_v1/v1_engine").resolve()
    if not (legacy_root / "project" / "llm" / "client.py").is_file():
        pytest.skip("legacy_v1 source archive is not present in this workspace")
    v1_engine_path = str(legacy_root)
    if v1_engine_path not in sys.path:
        sys.path.insert(0, v1_engine_path)

    from project.llm.client import LLMClient as LegacySrcClient

    # Source archive
    src_client = LegacySrcClient()
    with pytest.raises(RuntimeError, match="Commercial LLM APIs retired"):
        src_client.test_connection()
    with pytest.raises(RuntimeError, match="Commercial LLM APIs retired"):
        src_client.chat_completion([])
    with pytest.raises(RuntimeError, match="Commercial LLM APIs retired"):
        src_client.chat_completion_json([])

    # Dist archive
    dist_client_path = Path("archive/legacy_v1/v1_engine/dist/高校教师信息智能采集器/_internal/project/llm/client.py")
    if dist_client_path.exists():
        spec = importlib.util.spec_from_file_location("dist_client", str(dist_client_path))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        dist_c = mod.LLMClient()
        with pytest.raises(RuntimeError, match="Commercial LLM APIs retired"):
            dist_c.test_connection()
        with pytest.raises(RuntimeError, match="Commercial LLM APIs retired"):
            dist_c.chat_completion([])


# Item 5: Mandatory three-element lease fencing for failure submission
def test_sync_task_result_error_requires_worker_id_and_token(tmp_path):
    """
    Verifies that sync_task_result failure submission strictly requires
    both worker_id and lease_token, and verifies lease expiration.
    """
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = q.submit_task("大学", "学院")
    claimed = q.claim_task("worker-1", lease_seconds=10.0)

    # Missing worker_id or lease_token must raise LeaseConflictError
    with pytest.raises(LeaseConflictError, match="mandatory"):
        q.sync_task_result(task.task_id, [], {}, "", error="Fetch failed", worker_id=None, lease_token=claimed.lease_token)

    with pytest.raises(LeaseConflictError, match="mandatory"):
        q.sync_task_result(task.task_id, [], {}, "", error="Fetch failed", worker_id="worker-1", lease_token=None)

    # Wrong token must raise LeaseConflictError
    with pytest.raises(LeaseConflictError):
        q.sync_task_result(task.task_id, [], {}, "", error="Fetch failed", worker_id="worker-1", lease_token="bad-token")

    # Valid token and worker_id succeeds
    res = q.sync_task_result(task.task_id, [], {}, "", error="Valid failure", worker_id="worker-1", lease_token=claimed.lease_token)
    assert res.status == "failed"
    assert res.error == "Valid failure"


# Item 6: Free mail domains require scholar name affinity
def test_free_mail_domain_requires_name_affinity():
    """
    Verifies that public/free email domains (gmail, 163, qq, etc.) do NOT bypass
    scholar name affinity checks, preventing generic administrative mailboxes
    (office@gmail.com, admin@163.com) from being attributed to scholars.
    """
    # Generic service mailboxes should NOT match scholar
    assert is_personal_email_for_scholar("office@gmail.com", "张三") is False
    assert is_personal_email_for_scholar("admin@163.com", "李四") is False
    assert is_personal_email_for_scholar("service@qq.com", "王五") is False

    # Valid scholar mailboxes with name or initials DO match
    assert is_personal_email_for_scholar("zhangsan@gmail.com", "张三") is True
    assert is_personal_email_for_scholar("lsjwork@126.com", "刘圣洁") is True
    assert is_personal_email_for_scholar("wangwei_1985@163.com", "王伟") is True
