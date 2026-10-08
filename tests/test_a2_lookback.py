"""Read-only A2 lookback: compare stored coverage with recon browser evidence."""
import json
import sqlite3

from server import a2_lookback

ENTRY = "https://x.edu.cn/szdw/list.htm"


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _db(tmp_path, tasks):
    path = tmp_path / "tasks.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE tasks (task_id TEXT, school TEXT, college TEXT, status TEXT, "
                           "task_type TEXT, finished_at REAL, stats_json TEXT)")
        connection.executemany("INSERT INTO tasks VALUES (?,?,?,'completed','harvest',?,?)", tasks)
    return path


def _task(outputs, task_id, plan, coverage, recon_results, session="recon-abc"):
    _write(outputs / "tasks" / task_id / "active-plan.json",
           {"plan": plan, "recon": {"session_id": session, "phases": [{"session_id": f"{session}-branch-1"}]}})
    for index, result in enumerate(recon_results, 1):
        _write(outputs / "recon-evidence" / f"{session}-branch-1" / "run" / f"{index:04d}.json", result)
    return json.dumps({"total": 2, "coverage": coverage})


def _paged_coverage(pages, visible):
    return {"branches": [{"url": ENTRY, "visited_pages": len(pages), "end": "next_control_absent"}],
            "pages": [{"url": url, "branch_url": ENTRY} for url in pages],
            "roster_observations": [{"url": url, "visible_cards": visible} for url in pages]}


def _run(tmp_path, tasks):
    db = _db(tmp_path, tasks)
    before = db.read_bytes()
    report = {row["task_id"]: row for row in a2_lookback.lookback(db, tmp_path / "outputs")}
    assert db.read_bytes() == before
    return report


def test_pager_that_stopped_on_entry_page_is_flagged(tmp_path):
    outputs = tmp_path / "outputs"
    plan = {"branches": [{"url": ENTRY, "card_selector": "li", "next_selector": "a.next"}]}
    stats = _task(outputs, "t1", plan, _paged_coverage([ENTRY], 10),
                  [{"url": ENTRY, "cardCount": 10, "nextCount": 1, "nextUrl": "https://x.edu.cn/szdw/list2.htm"}])
    row = _run(tmp_path, [("t1", "样本大学", "样本学院", 1.0, stats)])["t1"]
    assert row["status"] == "suspect" and row["before_fix_merge"]
    assert [item["check"] for item in row["findings"]] == ["stopped_where_recon_saw_next"]
    assert row["findings"][0]["recon_next_seen"] is True


def test_last_page_with_dead_next_link_is_not_flagged(tmp_path):
    outputs = tmp_path / "outputs"
    last = "https://x.edu.cn/szdw/list2.htm"
    plan = {"branches": [{"url": ENTRY, "card_selector": "li", "next_selector": "a.next"}]}
    stats = _task(outputs, "t1", plan, _paged_coverage([ENTRY, last], 8),
                  [{"url": ENTRY, "cardCount": 8, "nextCount": 1, "nextUrl": last},
                   {"url": last, "cardCount": 8, "nextCount": 1, "nextUrl": "javascript:void(0);"}])
    assert _run(tmp_path, [("t1", "样本大学", "样本学院", 1.0, stats)])["t1"]["status"] == "no_finding"


def test_fewer_cards_than_recon_saw_is_flagged(tmp_path):
    outputs = tmp_path / "outputs"
    plan = {"branches": [{"url": ENTRY, "card_selector": "li", "single_page": True}]}
    coverage = {"branches": [{"url": ENTRY, "visited_pages": 1, "end": "single_page_no_pagination_control_seen"}],
                "pages": [{"url": ENTRY, "branch_url": ENTRY}],
                "roster_observations": [{"url": ENTRY, "visible_cards": 3}]}
    stats = _task(outputs, "t1", plan, coverage, [{"url": ENTRY, "cardCount": 12, "nextCount": 0}])
    row = _run(tmp_path, [("t1", "样本大学", "样本学院", 1.0, stats)])["t1"]
    assert row["findings"] == [{"branch": ENTRY, "check": "fewer_cards_than_recon", "page": ENTRY,
                                "captured_cards": 3, "recon_cards": 12}]


def test_navigation_with_fewer_children_than_recon_is_flagged(tmp_path):
    outputs = tmp_path / "outputs"
    child = "https://x.edu.cn/szdw/a.htm"
    plan = {"branches": [{"url": ENTRY, "kind": "navigation", "child_link_selector": "a.dept",
                          "child_branch": {"card_selector": "li", "single_page": True}}]}
    coverage = {"branches": [{"url": ENTRY, "kind": "navigation", "discovered_children": 2, "child_visits": [
                    {"url": child, "visited_pages": 1, "end": "single_page_no_pagination_control_seen"}]}],
                "pages": [{"url": child, "branch_url": child}], "roster_observations": []}
    stats = _task(outputs, "t1", plan, coverage, [{"url": ENTRY, "cardCount": 3, "nextCount": 0}])
    row = _run(tmp_path, [("t1", "样本大学", "样本学院", 1.0, stats)])["t1"]
    assert row["findings"] == [{"branch": ENTRY, "check": "fewer_children_than_recon",
                                "discovered_children": 2, "recon_links": 3}]


def test_tasks_without_worker_evidence_use_flat_audit_or_say_so(tmp_path):
    outputs = tmp_path / "outputs"
    _write(outputs / "旧大学_甲学院_audit.json", {"branches": [
        {"url": ENTRY, "visited_pages": 1, "end": "next_control_absent"},
        {"url": "https://x.edu.cn/b.htm", "visited_pages": 3, "end": "next_control_absent"}]})
    _write(outputs / "旧大学_乙学院_audit.json", {"branches": [
        {"url": ENTRY, "visited_pages": 2, "end": "next_control_absent"}]})
    report = _run(tmp_path, [("a", "旧大学", "甲学院", 1.0, '{"total": 5}'),
                             ("b", "旧大学", "乙学院", 1.0, '{"total": 5}'),
                             ("c", "旧大学", "丙学院", 1.0, '{"total": 5}')])
    assert report["a"]["status"] == "suspect"
    assert report["a"]["findings"] == [{"branch": ENTRY, "check": "stopped_on_entry_page", "visited_pages": 1}]
    assert report["b"]["status"] == "legacy_audit_only"
    assert report["c"]["status"] == "no_coverage_evidence"


def test_cli_prints_groups(tmp_path, capsys):
    db = _db(tmp_path, [("c", "旧大学", "丙学院", 1.0, "{}")])
    assert a2_lookback.main(["--db-path", str(db), "--outputs-dir", str(tmp_path / "outputs")]) == 0
    assert "无覆盖证据：1 个已完成学院工单" in capsys.readouterr().out
