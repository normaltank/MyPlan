"""A4: the per-college download link points at the outputs root, but the worker writes
the college workbook under outputs/tasks/<task_id>/attempt-N/. The link 404s.
Expected after the fix: the link downloads, or no college link is offered."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from server.browser_batch_sync import build_payload


@pytest.mark.xfail(strict=True, reason="A4: college download_url drops the tasks/<id>/attempt-N/ path")
def test_worker_layout_college_link_is_downloadable_or_absent(tmp_path, monkeypatch):
    outputs = tmp_path / "outputs"
    attempt = outputs / "tasks" / "abc123" / "attempt-1"      # agent_worker._harvest_with_repair layout
    attempt.mkdir(parents=True)
    excel = attempt / "样本大学_样本学院.xlsx"                   # run_plan: out / f"{name}.xlsx"
    excel.write_bytes(b"placeholder")
    records = [{"序号": 1, "姓名": "张富有", "职称": "教授", "邮箱": "", "学校": "样本大学", "学院": "样本学院",
                "采集人": "w", "采集日期": "2026-10-01", "简介": "", "主页": "https://example.edu/p/1"}]
    (attempt / "records.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    report = {"school": "样本大学", "college": "样本学院", "delivery_ready": True, "unresolved": [],
              "total": 1, "with_email": 0, "no_email": 1, "records_path": str(attempt / "records.json"),
              "excel_path": str(excel), "directory_pages": 1, "candidate_count": 1}
    audit = attempt / "audit.json"
    audit.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    payload = build_payload(audit, "w", "lease", outputs)
    print("download_url =", payload["download_url"])
    # Observed on main: "/download/样本大学_样本学院.xlsx", which download_file answers with 404
    # although the workbook exists under tasks/abc123/attempt-1/.
    assert excel.is_file()
    if not payload["download_url"]:
        return                                            # no link shown: acceptable fix
    from server import app as web
    monkeypatch.setattr(web, "OUTPUT_DIR", str(outputs))
    try:                                                  # request=None is the internal admin caller
        asyncio.run(web.download_file(payload["download_url"].removeprefix("/download/")))
    except HTTPException as exc:
        pytest.fail(f"college link {payload['download_url']} returned {exc.status_code}")
