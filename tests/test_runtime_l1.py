"""L1 cache and drift contracts; deliberately use real SQLite files."""
from __future__ import annotations

import statistics
import time

from server.runtime_l1 import L1RuntimeExecutor, SQLiteWrapperRegistry, cluster_key_for
from server.runtime_l1 import _skeleton_hash
from lxml import etree


URL = "https://faculty.example.edu/teachers/list.htm"
SPEC = {"record_xpath": "//ul[@id='staff']/li", "name_xpath": ".//a", "url_xpath": ".//a/@href", "mode": "dom"}


def page(names: list[str], *, prefix: str = "") -> str:
    rows = "".join(f"<li><a href='/p/{i}.htm'>{name}</a></li>" for i, name in enumerate(names))
    return f"<html><body>{prefix}<ul id='staff'>{rows}</ul></body></html>"


NAMES = ["张三", "李四", "王五", "赵六", "孙七"]


def test_hit_and_persistent_registry_version(tmp_path):
    db = tmp_path / "wrappers.db"
    html = page(NAMES)
    key = cluster_key_for(html, URL)
    registry = SQLiteWrapperRegistry(db)
    assert L1RuntimeExecutor(registry).execute(html, URL) == ([], "miss")
    registry.put(key, SPEC, {"golden-page": [{"name": n} for n in NAMES]})
    records, status = L1RuntimeExecutor(registry).execute(html, URL)
    assert status == "hit"
    assert [r["name"] for r in records] == NAMES
    assert records[0]["url"] == "https://faculty.example.edu/p/0.htm"
    saved = SQLiteWrapperRegistry(db).get(key)
    assert saved is not None and saved["hit_count"] == 1
    assert saved["last_success_at"] is not None
    registry.put(key, SPEC, {"golden-page": NAMES})
    assert SQLiteWrapperRegistry(db).get(key)["version"] == 2


def test_count_drop_trips_persistent_circuit_and_emits_ticket(tmp_path):
    from server.agent_queue import AgentTaskQueue

    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    key = cluster_key_for(page(NAMES), URL)
    registry.put(key, SPEC, {"golden": NAMES})
    executor = L1RuntimeExecutor(registry, queue=queue)
    assert executor.execute(page(NAMES[:2]), URL) == ([], "drift")
    assert registry.get(key)["disabled_at"] is not None
    assert executor.execute(page(NAMES), URL) == ([], "drift")
    tickets = [t for t in queue.get_pending_tasks() if t.task_type == "compile_ticket"]
    assert len(tickets) == 1 and tickets[0].cluster_key == key


def test_empty_and_bad_name_length_are_drift(tmp_path):
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    normal = page(NAMES)
    registry.put(cluster_key_for(normal, URL), SPEC, {"golden": NAMES})
    assert L1RuntimeExecutor(registry).execute(page([]), URL) == ([], "drift")
    registry.put(cluster_key_for(normal, URL), SPEC, {"golden": NAMES})
    assert L1RuntimeExecutor(registry).execute(page(["过长的人物姓名"] * 5), URL) == ([], "drift")


def test_structural_route_change_returns_drift_not_silent_miss(tmp_path):
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    registry.put(cluster_key_for(page(NAMES), URL), SPEC, {"golden": NAMES})
    changed = "<html><body><section><p>new layout</p></section></body></html>"
    assert L1RuntimeExecutor(registry).execute(changed, URL) == ([], "drift")
    assert L1RuntimeExecutor(registry).execute(changed, "https://other.edu/list.htm") == ([], "miss")


def test_warm_l1_median_under_two_ms_for_small_page(tmp_path):
    html = page(NAMES)
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    registry.put(cluster_key_for(html, URL), SPEC, {"golden": NAMES})
    executor = L1RuntimeExecutor(registry)
    executor.execute(html, URL)
    elapsed = []
    for _ in range(40):
        start = time.perf_counter()
        assert executor.execute(html, URL)[1] == "hit"
        elapsed.append((time.perf_counter() - start) * 1000)
    assert statistics.median(elapsed) < 2.0


def test_malformed_page_fails_closed(tmp_path):
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    registry.put(cluster_key_for(page(NAMES), URL), SPEC, {"golden": NAMES})
    assert L1RuntimeExecutor(registry).execute("", URL) == ([], "drift")


def test_golden_sample_can_be_a_single_record_list(tmp_path):
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    html = page(NAMES)
    registry.put(cluster_key_for(html, URL), SPEC, [{"name": name} for name in NAMES])
    assert L1RuntimeExecutor(registry).execute(html, URL)[1] == "hit"


def test_skeleton_hash_handles_deep_dom_without_python_recursion():
    root = etree.Element("body")
    node = root
    for _ in range(1500):
        node = etree.SubElement(node, "div")
    assert len(_skeleton_hash(root)) == 32


def test_non_http_profile_link_fails_closed(tmp_path):
    registry = SQLiteWrapperRegistry(tmp_path / "wrappers.db")
    html = page(NAMES)
    registry.put(cluster_key_for(html, URL), SPEC, {"golden": NAMES})
    unsafe = html.replace("/p/0.htm", "javascript:void(0)")
    assert L1RuntimeExecutor(registry).execute(unsafe, URL) == ([], "drift")


def test_nonrendered_script_does_not_change_cluster_key():
    html = page(NAMES)
    with_script = html.replace("</body>", "<script src='/version-2.js'></script></body>")
    assert cluster_key_for(html, URL) == cluster_key_for(with_script, URL)
