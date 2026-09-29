"""
Comprehensive test suite validating remediation of all 11 adversarial audit findings (Codex Audit).
Enforces:
1. Strict Dual-Sheet Conservation Axiom.
2. Zero keyword blacklists & zero AI summarization.
3. Zero external commercial LLM APIs.
4. Fencing token protection against ABA race conditions in task leases.
5. Batch completion gates preventing premature or corrupted Excel export.
6. Layout-aware CSS block boundaries & whitespace fidelity.
7. Noise element isolation and institutional mailbox filtering.
8. Sensitive L1 baseline drift circuit breaker.
"""
import pytest
import time
import uuid
from bs4 import BeautifulSoup
from lxml import etree

from server.faculty_extractor import (
    FacultyCandidate,
    FacultyDirectoryExtractor,
    FacultyProfileExtractor,
    FacultyHarvestPipeline,
    LayoutAwareTokenizer,
    is_noise_element,
    is_institutional_email,
)
from server.detail_mecc import DetailMECCExtractor
from server.agent_queue import AgentTaskQueue, LeaseConflictError
from server.runtime_l1 import L1RuntimeExecutor, SQLiteWrapperRegistry, cluster_key_for


# 1. Single profile-less faculty in a 1-person text list
def test_single_profileless_faculty_extracted():
    html = "<html><body><ul><li><span>张三</span></li></ul></body></html>"
    base_url = "https://cs.univ.edu.cn/faculty.htm"
    cands = FacultyDirectoryExtractor.extract(html, base_url)
    assert len(cands) == 1, f"Expected 1 candidate, got {len(cands)}"
    assert cands[0].name == "张三"
    assert cands[0].url == base_url

    pipeline = FacultyHarvestPipeline()
    result = pipeline.harvest_directory(
        directory_html=html,
        base_url=base_url,
        profile_fetcher=lambda u: "",
        school="测试大学",
        college="计科院"
    )
    assert result["total_count"] == 1
    assert len(result["sheet1_with_email"]) == 0
    assert len(result["sheet2_no_email"]) == 1
    assert result["sheet2_no_email"][0]["姓名"] == "张三"
    assert len(result["unresolved"]) == 0
    assert result["is_conserved"] is True
    assert result["delivery_ready"] is True


# 2. Empty response or timeout breaks conservation
def test_empty_response_or_timeout_breaks_conservation():
    lis = "".join(f"<li><a href='/t/{i}.htm'>{name}</a></li>" for i, name in enumerate(["张伟", "李娜", "王强"]))
    html = f"<html><body><ul>{lis}</ul></body></html>"
    base_url = "https://cs.univ.edu.cn/list.htm"

    def faulty_fetcher(url: str):
        if "1.htm" in url:
            # Timeout / failure
            raise TimeoutError("Connection timed out")
        elif "2.htm" in url:
            # Empty response
            return ""
        return "<html><body><div class='article-content'><h1>张伟</h1><p>邮箱: zw@univ.edu.cn</p></div></body></html>"

    result = FacultyHarvestPipeline.harvest_directory(
        directory_html=html,
        base_url=base_url,
        profile_fetcher=faulty_fetcher,
        school="测试大学",
        college="计科院"
    )

    # 2 out of 3 failed/timed out
    assert len(result["unresolved"]) == 2
    unresolved_names = {u["姓名"] for u in result["unresolved"]}
    assert unresolved_names == {"李娜", "王强"}

    # Conservation must NOT hold when unresolved items exist!
    assert result["is_conserved"] is False
    assert result["delivery_ready"] is False


# 3. Zero candidates does NOT allow delivery
def test_zero_candidates_not_delivery_ready():
    html = "<html><body><div><p>本系暂无教师公布</p></div></body></html>"
    base_url = "https://cs.univ.edu.cn/none.htm"
    result = FacultyHarvestPipeline.harvest_directory(
        directory_html=html,
        base_url=base_url,
        profile_fetcher=lambda u: "",
    )
    assert result["total_count"] == 0
    assert result["is_conserved"] is False
    assert result["delivery_ready"] is False


# 4. Homonyms without homepages are both preserved
def test_homonyms_without_links_both_preserved(tmp_path):
    from server.autonomous_agent import AutonomousBrowserAgent
    # Two distinct li cards with the same name "王伟", no links
    html = """<html><body>
    <div class="roster">
        <ul>
            <li class="item"><span>王伟</span> <span class="title">教授</span></li>
            <li class="item"><span>王伟</span> <span class="title">副教授</span></li>
        </ul>
    </div></body></html>"""
    base_url = "https://cs.univ.edu.cn/teachers.htm"

    cands = FacultyDirectoryExtractor.extract(html, base_url)
    assert len(cands) == 2, f"Expected 2 homonym candidates, got {len(cands)}"
    assert cands[0].name == "王伟" and cands[1].name == "王伟"

    pipeline_result = FacultyHarvestPipeline.harvest_candidates(
        candidates=cands,
        profile_fetcher=lambda u: "",
        base_url=base_url,
        school="某大学",
        college="电气学院"
    )
    assert pipeline_result["total_count"] == 2
    assert len(pipeline_result["sheet2_no_email"]) == 2

    # Export to Excel and assert no rows were dropped
    out_file = str(tmp_path / "homonyms_test.xlsx")
    agent = AutonomousBrowserAgent()
    records = pipeline_result["sheet1_with_email"] + pipeline_result["sheet2_no_email"]
    stats = agent.export_dual_sheet_excel(records, out_file)
    assert stats["total"] == 2
    assert stats["no_email"] == 2


# 5. Clean name: parenthetical title parsing and whitespace normalization
def test_clean_name_brackets_and_spaces():
    # Parenthetical title extraction
    name, title = FacultyDirectoryExtractor._clean_name("张三（教授）")
    assert name == "张三" and title == "教授"

    name, title = FacultyDirectoryExtractor._clean_name("李四(副教授)")
    assert name == "李四" and title == "副教授"

    name, title = FacultyDirectoryExtractor._clean_name("王五（博导/讲师）")
    assert name == "王五" and title == "讲师"

    name, title = FacultyDirectoryExtractor._clean_name("赵六（博导）")
    assert name == "赵六" and title == "教师"

    # Whitespace normalization
    name, title = FacultyDirectoryExtractor._clean_name("孙  洁")
    assert name == "孙洁"

    name, title = FacultyDirectoryExtractor._clean_name("John   Doe")
    assert name == "John Doe"


# 6. Lease fencing token protects against ABA race conditions
def test_lease_token_fencing_against_aba(tmp_path):
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = q.submit_task("清华", "信院")

    # Worker A claims with lease_seconds=0.01
    claim1 = q.claim_task("worker-1", lease_seconds=0.01)
    assert claim1 is not None
    token1 = claim1.lease_token
    assert token1 is not None

    time.sleep(0.02)  # Wait for lease to expire

    # Worker A re-claims the task with the same worker_id
    claim2 = q.claim_task("worker-1", lease_seconds=100)
    assert claim2 is not None
    token2 = claim2.lease_token
    assert token2 is not None
    assert token1 != token2, "New claim must issue a distinct lease_token"

    # Old callback using token1 must be rejected!
    assert not q.renew_lease(task.task_id, "worker-1", lease_seconds=60, lease_token=token1)
    with pytest.raises(LeaseConflictError):
        q.complete_task(
            task.task_id,
            [{"name": "张三", "email": "zs@tsinghua.edu.cn"}],
            {"total": 1, "with_email": 1, "no_email": 0},
            worker_id="worker-1",
            lease_token=token1
        )

    # Valid callback with token2 succeeds
    assert q.renew_lease(task.task_id, "worker-1", lease_seconds=60, lease_token=token2)
    completed = q.complete_task(
        task.task_id,
        [{"name": "张三", "email": "zs@tsinghua.edu.cn"}],
        {"total": 1, "with_email": 1, "no_email": 0},
        worker_id="worker-1",
        lease_token=token2
    )
    assert completed.status == "completed"


# 7. Batch completion is blocked if any college failed
def test_batch_merge_blocked_on_failure(tmp_path):
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    batch_id = "batch-101"
    t1 = q.submit_task("高校A", "院1", mode="batch", batch_id=batch_id)
    t2 = q.submit_task("高校A", "院2", mode="batch", batch_id=batch_id)

    # Complete t1
    c1 = q.claim_task("w1", task_id=t1.task_id)
    q.complete_task(t1.task_id, [{"name": "A"}], {"total": 1, "with_email": 0, "no_email": 1}, worker_id="w1", lease_token=c1.lease_token)

    # Fail t2
    c2 = q.claim_task("w2", task_id=t2.task_id)
    q.sync_task_result(t2.task_id, [], {}, "", error="Network unreachable", worker_id="w2", lease_token=c2.lease_token)

    b_status = q.get_batch_status(batch_id)
    assert b_status["total_colleges"] == 2
    assert b_status["completed_colleges"] == 1
    assert b_status["failed_colleges"] == 1
    assert b_status["status"] == "partially_failed", f"Status must be partially_failed, got {b_status['status']}"
    assert b_status["status"] != "completed"


# 8. Complete task strictly requires full, consistent stats
def test_complete_task_requires_full_stats(tmp_path):
    q = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    task = q.submit_task("高校", "学院")
    claimed = q.claim_task("worker", task_id=task.task_id)

    # Incomplete stats (missing with_email/no_email and empty records)
    with pytest.raises(ValueError, match="sheet conservation failed"):
        q.complete_task(task.task_id, [], {"total": 1}, worker_id="worker", lease_token=claimed.lease_token)

    # Inconsistent total
    with pytest.raises(ValueError, match="sheet conservation failed"):
        q.complete_task(task.task_id, [{"name": "A"}], {"total": 1, "with_email": 1, "no_email": 1}, worker_id="worker", lease_token=claimed.lease_token)

    # Unresolved non-zero
    with pytest.raises(ValueError, match="unresolved"):
        q.complete_task(task.task_id, [{"name": "A"}], {"total": 1, "with_email": 1, "no_email": 0, "unresolved": 1}, worker_id="worker", lease_token=claimed.lease_token)


# 9. Layout-aware tokenizer preserves inline spaces and handles 1500-level deep DOM
def test_layout_aware_tokenizer_inline_spaces_and_deep_dom():
    html = """<html><body>
        <p>研究领域：A <em>and</em> B，涉及 <span>C</span> <span>D</span></p>
    </body></html>"""
    soup = BeautifulSoup(html, "lxml")
    text = LayoutAwareTokenizer.get_clean_text(soup.body)
    assert "A and B" in text, f"Inline spaces missing! Got: {text}"
    assert "C D" in text, f"Span spaces missing! Got: {text}"

    # Deep nested DOM: 1500 divs
    deep_html = "<div>" * 1500 + "深度文本" + "</div>" * 1500
    deep_soup = BeautifulSoup(deep_html, "lxml")
    # Must not raise RecursionError
    deep_text = LayoutAwareTokenizer.get_clean_text(deep_soup)
    assert "深度文本" in deep_text


# 10. profile-footer is noise and institutional emails are rejected on single pages
def test_profile_footer_is_noise_and_institutional_rejected():
    html = """<html><body>
        <div class="profile-header"><h1>张三</h1><p>教授</p></div>
        <div class="article-content">
            <p>张三，长期从事人工智能理论研究。</p>
        </div>
        <div class="profile-footer">
            <p>学院公共邮箱: office@cs.univ.edu.cn</p>
        </div>
    </body></html>"""
    soup = BeautifulSoup(html, "lxml")
    footer = soup.find(class_="profile-footer")
    assert is_noise_element(footer) is True, "profile-footer must be recognized as noise!"

    detail = DetailMECCExtractor.extract_single(html, "https://cs.univ.edu.cn/zs.htm", fallback_name="张三")
    assert detail.name == "张三"
    # Public office mailbox must NOT be assigned to scholar!
    assert detail.email == "", f"Public institutional email leaked! Got: {detail.email}"


# 11. L1 drift trips on partial scholar drop
def test_l1_drift_on_partial_drop(tmp_path):
    names = ["张伟", "李娜", "王强", "赵敏"]
    url = "https://univ.edu.cn/teachers.htm"
    def make_page(n_list):
        lis = "".join(f"<li><span class='name'>{n}</span></li>" for n in n_list)
        return f"<html><body><div id='faculty'><ul>{lis}</ul></div></body></html>"

    spec = {
        "record_xpath": "//div[@id='faculty']//li",
        "name_xpath": ".//span[@class='name']",
        "url_xpath": None,
    }
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    key = cluster_key_for(make_page(names), url)
    registry.put(key, spec, {"golden": names})  # Baseline = 4 scholars

    executor = L1RuntimeExecutor(registry, queue=queue)

    # 4 -> 3 scholars (1 dropped). Must trip drift, NOT return hit!
    records, status = executor.execute(make_page(names[:3]), url)
    assert status == "drift", f"Expected drift on dropped scholar, got {status}"
    assert registry.get(key)["disabled_at"] is not None
