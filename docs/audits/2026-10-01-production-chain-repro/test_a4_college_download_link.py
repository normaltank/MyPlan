"""A4: the per-college download link points at the outputs root, but the worker writes
the college workbook under outputs/tasks/<task_id>/attempt-N/. The link 404s."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from server.browser_batch_sync import build_payload


def test_worker_layout_college_link_is_not_downloadable(tmp_path, monkeypatch):
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
    assert payload["download_url"] == "/download/样本大学_样本学院.xlsx"

    from server import app as web
    monkeypatch.setattr(web, "OUTPUT_DIR", str(outputs))
    with pytest.raises(HTTPException) as caught:          # request=None is the internal admin caller
        asyncio.run(web.download_file(payload["download_url"].removeprefix("/download/")))
    assert caught.value.status_code == 404
    # The file the link was meant to serve does exist:
    assert excel.is_file()
