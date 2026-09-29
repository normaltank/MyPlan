import json
import sqlite3

import httpx
import openpyxl
import pytest

from server.browser_batch_sync import build_payload, sync_report


def _report(tmp_path, ready=True):
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    excel = output_root / "样本大学_样本学院.xlsx"
    excel.write_bytes(b"workbook placeholder")
    records = [{
        "序号": 1, "姓名": "张富有", "职称": "教授", "邮箱": "0000000001@example.edu",
        "学校": "样本大学", "学院": "样本学院", "采集人": "Browser-assisted Subagent",
        "采集日期": "2026-09-24", "简介": "完整履历", "主页": "https://example.edu/p/1",
    }]
    records_path = tmp_path / "records.json"
    records_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    report = {"school": "样本大学", "college": "样本学院",
              "delivery_ready": ready, "unresolved": [] if ready else [{"status": "page_limit_reached"}],
              "total": 1, "with_email": 1, "no_email": 0,
              "records_path": str(records_path), "excel_path": str(excel),
              "directory_pages": 1, "candidate_count": 1}
    report_path = tmp_path / "audit.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return report_path, output_root


def test_sync_payload_uses_local_records_without_agent_copying_biographies(tmp_path):
    report_path, output_root = _report(tmp_path)
    payload = build_payload(report_path, "worker-1", "lease-1", output_root)

    assert payload["download_url"] == "/download/样本大学_样本学院.xlsx"
    assert payload["stats"] == {"total": 1, "with_email": 1, "no_email": 0,
                                "unresolved": 0, "directory_pages": 1, "candidate_count": 1}
    assert payload["records"][0]["简介"] == "完整履历"
    assert payload["lease_token"] == "lease-1"


def test_unresolved_audit_cannot_be_synced_as_complete(tmp_path):
    report_path, output_root = _report(tmp_path, ready=False)
    with pytest.raises(ValueError, match="delivery_ready"):
        build_payload(report_path, "worker-1", "lease-1", output_root)


def test_incomplete_coverage_cannot_be_synced_despite_ready_flag(tmp_path):
    path, root = _report(tmp_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    report["coverage"] = {"complete": False, "issues": [{"status": "branch_termination_unverified"}]}
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="coverage"):
        build_payload(path, "worker-1", "lease-1", root)


def test_coverage_and_model_attempts_persist_in_delivery_stats(tmp_path):
    path, root = _report(tmp_path)
    report = json.loads(path.read_text(encoding="utf-8"))
    report.update(coverage={"complete": True, "issues": [], "scope": "verified_roster_branches"},
                  coverage_path="local-coverage.json", recon_attempts=[{"model_calls": 1}],
                  checkpoints={"restored_profiles": 0})
    path.write_text(json.dumps(report), encoding="utf-8")
    stats = build_payload(path, "worker-1", "lease-1", root)["stats"]
    for key in ("coverage", "coverage_path", "recon_attempts", "checkpoints"):
        assert stats[key] == report[key]


def test_school_mismatch_cannot_enter_university_workbook(tmp_path):
    report_path, output_root = _report(tmp_path)
    record_path = tmp_path / "records.json"
    records = json.loads(record_path.read_text(encoding="utf-8"))
    records[0]["学校"] = "其他大学"
    record_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="school|college"):
        build_payload(report_path, "worker-1", "lease-1", output_root)


@pytest.mark.asyncio
async def test_sync_posts_to_local_task_endpoint(tmp_path):
    report_path, output_root = _report(tmp_path)
    seen = []

    def responder(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "synced"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url="http://127.0.0.1:8000") as client:
        result = await sync_report("task-1", report_path, "worker-1", "lease-1",
                                   output_root=output_root, db_path=tmp_path / "missing.db", client=client)

    assert result["status"] == "synced"
    assert seen[0][0] == "/api/task/task-1/sync"
    assert seen[0][1]["records"][0]["姓名"] == "张富有"
    assert seen[0][1]["download_url"] == "/download/样本大学.xlsx"
    assert (output_root / "样本大学.xlsx").is_file()


@pytest.mark.asyncio
async def test_failed_sync_preserves_previous_university_workbook(tmp_path):
    report_path, output_root = _report(tmp_path)
    university = output_root / "样本大学.xlsx"
    university.write_bytes(b"previous delivery")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _request: httpx.Response(409)),
        base_url="http://127.0.0.1:8000",
    ) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await sync_report("task-1", report_path, "worker-1", "lease-1",
                              output_root=output_root, db_path=tmp_path / "missing.db", client=client)

    assert university.read_bytes() == b"previous delivery"


@pytest.mark.asyncio
async def test_university_export_reloads_completed_tasks_after_sync(tmp_path):
    report_path, output_root = _report(tmp_path)
    database = tmp_path / "tasks.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE tasks (task_id TEXT, school TEXT, status TEXT, created_at REAL)")
        connection.execute("CREATE TABLE records (task_id TEXT, seq INTEGER, payload_json TEXT)")

    base_record = json.loads((tmp_path / "records.json").read_text(encoding="utf-8"))[0]

    def add_task(task_id, name, created_at):
        row = dict(base_record, 姓名=name)
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO tasks VALUES (?,?,?,?)", (task_id, "样本大学", "completed", created_at))
            connection.execute("INSERT INTO records VALUES (?,?,?)", (task_id, 1, json.dumps(row, ensure_ascii=False)))

    add_task("old", "刘圣洁", 1)

    def responder(_request):
        add_task("task-1", "张富有", 2)
        add_task("late", "孙洁", 3)
        return httpx.Response(200, json={"status": "synced"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder), base_url="http://127.0.0.1:8000") as client:
        await sync_report("task-1", report_path, "worker-1", "lease-1",
                          output_root=output_root, db_path=database, client=client)

    workbook = openpyxl.load_workbook(output_root / "样本大学.xlsx", read_only=True)
    assert workbook["教师列表"].max_row == 4
