"""
Comprehensive Regression Test Suite for Codex Round 2 Audit Remediation:
1. Strict lease_token requirement fencing against ABA.
2. Complete task rejects missing stats or email partition mismatches.
3. DetailMECCExtractor.extract_cluster strictly filters institutional emails.
4. LayoutAwareTokenizer respects CSS style="display:block" on inline elements.
5. Shared profile URL does not cross-contaminate emails between scholars.
6. PDF text with C++ templates like <vector> preserved intact.
7. Navigation/menu chrome links not extracted as delivery-ready faculty.
"""

import html
import pytest
from bs4 import BeautifulSoup

from server.agent_queue import AgentTaskQueue, LeaseConflictError
from server.detail_mecc import DetailMECCExtractor, LayoutAwareTokenizer
from server.faculty_extractor import (
    FacultyCandidate,
    FacultyHarvestPipeline,
    FacultyDirectoryExtractor
)
from Opus.iso_mining import mine_records


# 1. Strict lease_token requirement fencing against ABA
def test_mandatory_lease_token_fences_aba(tmp_path):
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = q.submit_task("大学", "学院")
    
    # Worker claims task (lease 1)
    claimed1 = q.claim_task("worker-1", lease_seconds=0.01)
    token1 = claimed1.lease_token
    assert token1 is not None

    import time
    time.sleep(0.02)

    # Worker re-claims after expiration (lease 2)
    claimed2 = q.claim_task("worker-1", lease_seconds=120)
    token2 = claimed2.lease_token
    assert token2 is not None and token2 != token1

    # Caller with token=None CANNOT renew
    assert not q.renew_lease(task.task_id, "worker-1", lease_seconds=60, lease_token=None)
    # Caller with stale token1 CANNOT renew
    assert not q.renew_lease(task.task_id, "worker-1", lease_seconds=60, lease_token=token1)
    # Caller with valid token2 CAN renew
    assert q.renew_lease(task.task_id, "worker-1", lease_seconds=60, lease_token=token2)

    # Caller with token=None CANNOT complete
    with pytest.raises(LeaseConflictError):
        q.complete_task(task.task_id, [{"name": "A", "email": ""}], {"total": 1, "with_email": 0, "no_email": 1}, worker_id="worker-1", lease_token=None)

    # Caller with stale token1 CANNOT complete
    with pytest.raises(LeaseConflictError):
        q.complete_task(task.task_id, [{"name": "A", "email": ""}], {"total": 1, "with_email": 0, "no_email": 1}, worker_id="worker-1", lease_token=token1)

    # Caller with active token2 CAN complete
    completed = q.complete_task(task.task_id, [{"name": "A", "email": ""}], {"total": 1, "with_email": 0, "no_email": 1}, worker_id="worker-1", lease_token=token2)
    assert completed.status == "completed"


# 2. Complete task rejects missing stats or email partition mismatches
def test_complete_task_rejects_missing_stats_and_partition_mismatch(tmp_path):
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = q.submit_task("大学", "学院")
    claimed = q.claim_task("worker-1")

    # Missing with_email / no_email (auto-infer is eliminated)
    with pytest.raises(ValueError, match="sheet conservation failed: stats must contain 'with_email'"):
        q.complete_task(task.task_id, [{"name": "A", "email": ""}], {"total": 1}, worker_id="worker-1", lease_token=claimed.lease_token)

    # Partition mismatch: records has 1 with email, stats says with_email=0, no_email=1
    with pytest.raises(ValueError, match="sheet conservation failed: records email partition"):
        q.complete_task(
            task.task_id,
            [{"name": "A", "email": "a@x.edu.cn"}],
            {"total": 1, "with_email": 0, "no_email": 1},
            worker_id="worker-1",
            lease_token=claimed.lease_token
        )


# 3. DetailMECCExtractor.extract_cluster strictly filters institutional emails
def test_single_page_cluster_rejects_institutional_email():
    html_doc = """
    <html><body>
    <div class="teacher-detail">
        <h1>张三</h1>
        <p>职称：教授</p>
        <p>个人学术履历：从事分布式计算研究。</p>
        <div class="contact">
            <p>联系方式：office@univ.edu.cn</p>
        </div>
    </div>
    </body></html>
    """
    # Even if only 1 page is extracted in extract_cluster, office@univ.edu.cn must NOT be assigned
    cluster_results = DetailMECCExtractor.extract_cluster([("http://univ.edu.cn/zhang", html_doc, "张三")])
    assert len(cluster_results) == 1
    detail = cluster_results[0]
    assert detail.name == "张三"
    assert detail.email == "", f"Institutional email office@ must not be assigned as personal email, got {detail.email}"


# 4. LayoutAwareTokenizer respects CSS style="display:block" on inline elements
def test_display_block_span_separated_by_newline():
    html_doc = """
    <div class="bio">
        <span style="display:block">第一段学术履历</span>
        <span style="display:block">第二段研究成果</span>
    </div>
    """
    soup = BeautifulSoup(html_doc, 'lxml')
    bio_div = soup.find('div', class_='bio')
    clean_text = LayoutAwareTokenizer.get_clean_text(bio_div)
    assert "第一段学术履历" in clean_text
    assert "第二段研究成果" in clean_text
    assert "第一段学术履历第二段研究成果" not in clean_text
    non_empty_lines = [l.strip() for l in clean_text.split('\n') if l.strip()]
    assert non_empty_lines == ["第一段学术履历", "第二段研究成果"]



# 5. Shared profile URL does not cross-contaminate emails between scholars
def test_shared_profile_url_does_not_cross_contaminate_emails():
    # Two distinct scholars share the same department profile URL
    shared_url = "http://cs.univ.edu.cn/faculty/group1.html"
    candidates = [
        FacultyCandidate(name="张三", url=shared_url, title="教授"),
        FacultyCandidate(name="李四", url=shared_url, title="副教授"),
    ]

    # The shared page contains only zhang@univ.edu.cn
    shared_html = """
    <html><body>
    <div class="article-content">
        <div class="teacher">
            <h2>张三 教授</h2>
            <p>邮箱：zhang@univ.edu.cn</p>
        </div>
        <div class="teacher">
            <h2>李四 副教授</h2>
            <p>研究方向：计算机视觉</p>
        </div>
    </div>
    </body></html>
    """

    def fetcher(url):
        if url == shared_url:
            return shared_html
        return ""

    result = FacultyHarvestPipeline.harvest_candidates(
        candidates=candidates,
        profile_fetcher=fetcher,
        base_url="http://cs.univ.edu.cn/faculty/",
        school="某某大学",
        college="计算机学院"
    )

    assert result["is_conserved"] is True
    assert result["total_count"] == 2
    sheet1 = result["sheet1_with_email"]
    sheet2 = result["sheet2_no_email"]

    # Zhang San should have the email
    assert len(sheet1) == 1
    assert sheet1[0]["姓名"] == "张三"
    assert sheet1[0]["邮箱"] == "zhang@univ.edu.cn"

    # Li Si must NOT get Zhang San's email
    assert len(sheet2) == 1
    assert sheet2[0]["姓名"] == "李四"
    assert sheet2[0]["邮箱"] == ""


# 6. PDF text with C++ templates like <vector> preserved intact
def test_pdf_text_with_cpp_vector_preserved_through_escape():
    raw_pdf_text = "主讲课程：C++程序设计，深入讲解 std::vector<int> 与 std::map<string, int> 的内存布局。"
    # Escaping ensures <int> is not stripped as an unknown HTML element by BeautifulSoup
    escaped_text = html.escape(raw_pdf_text)
    wrapper_html = f"<html><body><div class='article-content'><pre class='bio'>{escaped_text}</pre></div></body></html>"
    
    soup = BeautifulSoup(wrapper_html, 'lxml')
    mecc = soup.find('div', class_='article-content')
    extracted = LayoutAwareTokenizer.get_clean_text(mecc)
    assert "<int>" in extracted, f"C++ template <int> must be preserved, got: {extracted}"
    assert "std::vector<int>" in extracted


# 7. Navigation/menu chrome links not extracted as delivery-ready faculty
def test_menu_nav_link_not_extracted_as_delivery_ready_faculty():
    menu_html = """
    <html><body>
    <div class="menu">
        <a href="/news">张三</a>
    </div>
    <div class="main-content">
        <p>欢迎访问学院主页，近期学术动态请见新闻板块。</p>
    </div>
    </body></html>
    """
    cands = FacultyDirectoryExtractor.extract(menu_html, "http://univ.edu.cn")
    # Menu item pointing to /news must NOT be extracted as a faculty roster
    assert len(cands) == 0, f"Menu link must not be extracted as faculty candidate, got {cands}"

    res = mine_records(BeautifulSoup(menu_html, 'lxml'), page_url="http://univ.edu.cn")
    assert res.best is None or res.best.utility <= 0
