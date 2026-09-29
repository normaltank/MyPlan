"""
End-to-end verification of the L1 -> L2 -> L3 cascade in server/faculty_extractor.py:
1. L1 Cache Hit: Returns XPath-extracted candidates (<1ms) without running L2.
2. L1 Drift: Trips persistent circuit breaker, emits compile ticket to SQLite queue, gracefully falls back to L2.
3. L1 Miss: Executes L2 isomorphic mining, emits compile ticket when emit_ticket=True.
4. Cluster MECC in Pipeline: Multi-page profile harvesting with cross-page template inference & detail-df.
"""
from __future__ import annotations

import time
from server.faculty_extractor import (
    FacultyDirectoryExtractor,
    FacultyHarvestPipeline,
)
from server.runtime_l1 import L1RuntimeExecutor, SQLiteWrapperRegistry, cluster_key_for
from server.agent_queue import AgentTaskQueue


URL = "https://cs.univ.edu.cn/faculty/list.htm"
SPEC = {
    "record_xpath": "//div[@id='faculty-list']/div[@class='card']",
    "name_xpath": ".//a",
    "url_xpath": ".//a/@href",
    "mode": "dom"
}


def make_dir_html(teachers: list[tuple[str, str, str]]) -> str:
    cards = "".join(
        f"<div class='card'><h3><a href='{href}'>{name}</a></h3><span class='title'>{title}</span></div>"
        for name, title, href in teachers
    )
    return f"""
    <html>
    <body>
        <div class='nav'><a href='/'>首页</a><a href='/about'>关于</a></div>
        <div id='faculty-list'>{cards}</div>
        <div class='footer'>版权所有 某某大学 邮箱：office@univ.edu.cn</div>
    </body>
    </html>
    """


TEACHERS = [
    ("张三", "教授", "/t/1.htm"),
    ("李四", "副教授", "/t/2.htm"),
    ("王五", "讲师", "/t/3.htm"),
    ("赵六", "教授", "/t/4.htm"),
]


def test_cascade_l1_hit(tmp_path):
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    executor = L1RuntimeExecutor(registry, queue=queue)

    html = make_dir_html(TEACHERS)
    key = cluster_key_for(html, URL)
    registry.put(key, SPEC, {"golden": [t[0] for t in TEACHERS]})

    t0 = time.perf_counter()
    candidates = FacultyDirectoryExtractor.extract(html, URL, executor=executor)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    assert len(candidates) == 4
    assert [c.name for c in candidates] == ["张三", "李四", "王五", "赵六"]
    assert candidates[0].url == "https://cs.univ.edu.cn/t/1.htm"
    # L1 hit should be sub-millisecond on warm execution
    assert elapsed_ms < 10.0


def test_cascade_l1_drift_falls_back_to_l2(tmp_path):
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    executor = L1RuntimeExecutor(registry, queue=queue)

    # Register golden with 10 members
    normal_html = make_dir_html(TEACHERS * 2 + [("孙七", "讲师", "/t/5.htm"), ("周八", "教授", "/t/6.htm")])
    key = cluster_key_for(normal_html, URL)
    registry.put(key, SPEC, {"golden": ["name"] * 10})

    # Sudden drop to 2 members (>50% drop) -> causes L1 drift
    drift_html = make_dir_html(TEACHERS[:2])
    # The skeleton changed or count dropped -> trips circuit and falls through to L2
    candidates = FacultyDirectoryExtractor.extract(drift_html, URL, executor=executor)

    # L2 executes and extracts the 2 members
    assert len(candidates) == 2
    assert [c.name for c in candidates] == ["张三", "李四"]

    # Verify circuit was tripped and compile ticket emitted
    tickets = [t for t in queue.get_pending_tasks() if t.task_type == "compile_ticket"]
    assert len(tickets) >= 1
    assert tickets[0].cluster_key == key


def test_cascade_l1_miss_emits_compile_ticket_when_requested(tmp_path):
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    executor = L1RuntimeExecutor(registry, queue=queue)

    html = make_dir_html(TEACHERS)
    # Registry is empty, so L1 is a miss. emit_ticket=True schedules L3 compile ticket.
    candidates = FacultyDirectoryExtractor.extract(html, URL, executor=executor, emit_ticket=True)

    assert len(candidates) == 4
    assert [c.name for c in candidates] == ["张三", "李四", "王五", "赵六"]

    tickets = [t for t in queue.get_pending_tasks() if t.task_type == "compile_ticket"]
    assert len(tickets) == 1
    assert "schedule L3 synthesis" in (tickets[0].prompt_text or "")


def test_end_to_end_harvest_with_cluster_mecc(tmp_path):
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    executor = L1RuntimeExecutor(registry, queue=queue)

    def mock_profile_fetcher(url: str) -> str:
        if "1.htm" in url:
            return """
            <html><body>
                <div class='site-header'><h1>CS学院</h1></div>
                <div class='profile-content'>
                    <h1>张三</h1>
                    <p class='title'>教授</p>
                    <p>邮箱：zhangsan@cs.univ.edu.cn</p>
                    <div class='bio'>研究系统软件与编译技术。</div>
                </div>
                <div class='site-footer'>联系邮箱：dean@cs.univ.edu.cn</div>
            </body></html>
            """
        elif "2.htm" in url:
            return """
            <html><body>
                <div class='site-header'><h1>CS学院</h1></div>
                <div class='profile-content'>
                    <h1>李四</h1>
                    <p class='title'>副教授</p>
                    <p>电子邮箱：lisi@cs.univ.edu.cn</p>
                    <div class='bio'>研究数据密集型计算。</div>
                </div>
                <div class='site-footer'>联系邮箱：dean@cs.univ.edu.cn</div>
            </body></html>
            """
        elif "3.htm" in url:
            return """
            <html><body>
                <div class='site-header'><h1>CS学院</h1></div>
                <div class='profile-content'>
                    <h1>王五</h1>
                    <p class='title'>讲师</p>
                    <div class='bio'>研究网络协议优化，暂未公开个人邮箱。</div>
                </div>
                <div class='site-footer'>联系邮箱：dean@cs.univ.edu.cn</div>
            </body></html>
            """
        elif "4.htm" in url:
            # Obfuscated email
            return """
            <html><body>
                <div class='site-header'><h1>CS学院</h1></div>
                <div class='profile-content'>
                    <h1>赵六</h1>
                    <p class='title'>教授</p>
                    <p>联系方式：zhaoliu(at)cs.univ.edu.cn</p>
                    <div class='bio'>研究人工智能与多模态模型。</div>
                </div>
                <div class='site-footer'>联系邮箱：dean@cs.univ.edu.cn</div>
            </body></html>
            """
        return ""

    dir_html = make_dir_html(TEACHERS)
    result = FacultyHarvestPipeline.harvest_directory(
        directory_html=dir_html,
        base_url=URL,
        profile_fetcher=mock_profile_fetcher,
        school="某某大学",
        college="计算机学院",
        executor=executor
    )

    s1 = result["sheet1_with_email"]
    s2 = result["sheet2_no_email"]

    # Dual Sheet Conservation Axiom
    assert result["is_conserved"] is True
    assert result["delivery_ready"] is True
    assert len(s1) + len(s2) == 4
    assert len(s1) == 3
    assert len(s2) == 1

    # Check that institutional footer email dean@cs.univ.edu.cn is rejected by detail-df
    s1_emails = {r["姓名"]: r["邮箱"] for r in s1}
    assert s1_emails["张三"] == "zhangsan@cs.univ.edu.cn"
    assert s1_emails["李四"] == "lisi@cs.univ.edu.cn"
    assert s1_emails["赵六"] == "zhaoliu@cs.univ.edu.cn"

    s2_names = [r["姓名"] for r in s2]
    assert s2_names == ["王五"]
    assert s2[0]["邮箱"] == ""
