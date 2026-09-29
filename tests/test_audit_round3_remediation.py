"""
Comprehensive Regression Test Suite for Codex Round 3 Audit Remediation:
1. Legitimate scholar with /news/ in URL is NOT excluded by keyword blacklists.
2. Duplicate candidates are NOT split across dual sheets, and queue rejects identical duplicate records.
3. Shared page emails are strictly scoped to teacher DOM card containers (same-surname peers do not misattribute).
4. Expired leases cannot renew or submit failure results via atomic SQL lease fencing.
5. Incomplete crawl traversals / errors are recorded in unresolved, preventing unverified completion.
"""

import time
import pytest
from bs4 import BeautifulSoup

from server.agent_queue import AgentTaskQueue, LeaseConflictError
from server.detail_mecc import DetailMECCExtractor, is_personal_email_for_scholar
from server.faculty_extractor import (
    FacultyCandidate,
    FacultyDirectoryExtractor,
    FacultyHarvestPipeline,
)


# Counter-Example 1: Legitimate scholar with /news/ path not excluded
def test_legitimate_scholar_with_news_url_not_excluded():
    html_directory = """
    <html><body>
    <div class="content">
        <ul class="faculty-list">
            <li><a href="/news/t1.htm">张三</a><span>教授</span></li>
        </ul>
    </div>
    </body></html>
    """
    base_url = "https://cs.univ.edu.cn/teachers.htm"
    cands = FacultyDirectoryExtractor.extract(html_directory, base_url)
    assert len(cands) == 1, f"Expected 1 candidate, got {len(cands)}"
    assert cands[0].name == "张三"
    assert "/news/t1.htm" in cands[0].url

    profile_html = """
    <html><body>
    <div class="article-content">
        <h1>张三</h1>
        <p>个人简介：从事计算机视觉研究。</p>
        <p>邮箱：zhangsan@univ.edu.cn</p>
    </div>
    </body></html>
    """
    pipeline = FacultyHarvestPipeline()
    res = pipeline.harvest_candidates(
        candidates=cands,
        profile_fetcher=lambda u: profile_html,
        base_url=base_url,
        school="测试大学",
        college="计科院"
    )
    assert res["total_count"] == 1
    assert len(res["sheet1_with_email"]) == 1
    assert res["sheet1_with_email"][0]["姓名"] == "张三"
    assert res["sheet1_with_email"][0]["邮箱"] == "zhangsan@univ.edu.cn"
    assert res["is_conserved"] is True
    assert res["delivery_ready"] is True


# Counter-Example 2: Duplicate candidates collapsed & queue rejects duplicate records
def test_duplicate_candidate_not_split_across_sheets_and_rejected_by_complete_task(tmp_path):
    # Part A: Pipeline deduplication & person-level conservation
    url = "https://cs.univ.edu.cn/zs.htm"
    cands = [
        FacultyCandidate(name="张三", url=url, title="教授"),
        FacultyCandidate(name="张三", url=url, title="教授")
    ]
    profile_html = "<html><body><div class='bio'><h1>张三</h1><p>邮箱: zhangsan@univ.edu.cn</p></div></body></html>"
    pipeline = FacultyHarvestPipeline()
    res = pipeline.harvest_candidates(
        candidates=cands,
        profile_fetcher=lambda u: profile_html,
        base_url="https://cs.univ.edu.cn/faculty.htm",
        school="大学",
        college="学院"
    )
    # Must be deduplicated to 1 scholar, total=1
    assert res["total_count"] == 1
    assert len(res["sheet1_with_email"]) == 1
    assert len(res["sheet2_no_email"]) == 0
    assert res["is_conserved"] is True

    # Part B: Queue complete_task rejects duplicate identical records
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = q.submit_task("大学", "学院")
    claimed = q.claim_task("worker-1")

    duplicate_records = [
        {"name": "张三", "profile_url": url, "email": "zhangsan@univ.edu.cn"},
        {"name": "张三", "profile_url": url, "email": "zhangsan@univ.edu.cn"}
    ]
    with pytest.raises(ValueError, match="duplicate record detected"):
        q.complete_task(
            task.task_id,
            duplicate_records,
            {"total": 2, "with_email": 2, "no_email": 0},
            worker_id="worker-1",
            lease_token=claimed.lease_token
        )


# Counter-Example 3: Shared page email scoped to DOM card container (same surname peers)
def test_shared_page_does_not_misattribute_email_to_same_surname_peer():
    # Shared page where 张三 has no email in his card context, but 张四 has zhang@x.edu.cn
    shared_url = "https://cs.univ.edu.cn/teachers_all.htm"
    cand_zs = FacultyCandidate(
        name="张三",
        url=shared_url,
        title="教授",
        card_context="张三 教授 研究方向: 数据库"
    )
    cand_zsi = FacultyCandidate(
        name="张四",
        url=shared_url,
        title="副教授",
        card_context="张四 副教授 联系邮箱: zhang@x.edu.cn"
    )
    
    page_html = """
    <html><body>
    <div class="teacher-list">
        <div class="card"><h3>张三</h3><p>教授 研究方向: 数据库</p></div>
        <div class="card"><h3>张四</h3><p>副教授 联系邮箱: zhang@x.edu.cn</p></div>
    </div>
    </body></html>
    """
    pipeline = FacultyHarvestPipeline()
    res = pipeline.harvest_candidates(
        candidates=[cand_zs, cand_zsi],
        profile_fetcher=lambda u: page_html,
        base_url=shared_url,
        school="大学",
        college="学院"
    )
    assert res["total_count"] == 2
    # 张四 gets zhang@x.edu.cn
    assert len(res["sheet1_with_email"]) == 1
    assert res["sheet1_with_email"][0]["姓名"] == "张四"
    assert res["sheet1_with_email"][0]["邮箱"] == "zhang@x.edu.cn"
    # 张三 has no email and goes to sheet 2
    assert len(res["sheet2_no_email"]) == 1
    assert res["sheet2_no_email"][0]["姓名"] == "张三"
    assert res["sheet2_no_email"][0]["邮箱"] == ""
    assert res["is_conserved"] is True


# Counter-Example 4: Expired lease cannot renew or submit failure
def test_expired_lease_cannot_submit_failure_or_renew_atomic(tmp_path):
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = q.submit_task("大学", "学院")
    claimed = q.claim_task("worker-1", lease_seconds=0.01)
    token = claimed.lease_token
    assert token is not None

    time.sleep(0.02)  # Wait for lease to expire

    # Atomic renewal must fail on expired lease
    assert not q.renew_lease(task.task_id, "worker-1", lease_seconds=60, lease_token=token)

    # Submitting failure on expired lease must raise LeaseConflictError
    with pytest.raises(LeaseConflictError):
        q.sync_task_result(
            task.task_id,
            records=[],
            stats={"total": 0, "with_email": 0, "no_email": 0},
            download_url="",
            error="Fetch timeout",
            worker_id="worker-1",
            lease_token=token
        )

    # Task status must NOT be changed to 'failed'
    t = q.get_task(task.task_id)
    assert t.status != "failed"


# Counter-Example 5: Incomplete crawl traversal enters unresolved and blocks delivery
def test_incomplete_crawl_marks_unresolved_and_blocks_delivery():
    pipeline = FacultyHarvestPipeline()
    # If a profile fetch fails with timeout/exception
    cands = [FacultyCandidate(name="张三", url="https://cs.univ.edu.cn/zs.htm")]
    def failing_fetcher(url):
        raise ConnectionResetError("Connection reset by peer")

    res = pipeline.harvest_candidates(
        candidates=cands,
        profile_fetcher=failing_fetcher,
        base_url="https://cs.univ.edu.cn/faculty.htm",
        school="大学",
        college="学院"
    )
    assert len(res["unresolved"]) == 1
    assert res["is_conserved"] is False
    assert res["delivery_ready"] is False
