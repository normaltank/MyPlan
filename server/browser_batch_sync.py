"""Sync a completed browser batch from local files without LLM-visible biographies."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx


OUTPUT_ROOT = Path(__file__).resolve().parent / "outputs"
TASK_DB = None  # the queue default (HARVEST_TASK_DB or server/tasks.db), resolved per call
FIELDS = {"序号", "姓名", "职称", "邮箱", "学校", "学院", "采集人", "采集日期", "简介", "主页"}


def build_payload(report_path: str | Path, worker_id: str, lease_token: str,
                  output_root: str | Path = OUTPUT_ROOT) -> dict:
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    if not report.get("delivery_ready") or report.get("unresolved"):
        raise ValueError("delivery_ready must be true and unresolved must be empty")
    if "coverage" in report and (report["coverage"].get("complete") is not True or report["coverage"].get("issues")):
        raise ValueError("coverage ledger must be complete and have no issues")
    if not worker_id or not lease_token:
        raise ValueError("worker_id and lease_token are required")
    root = Path(output_root).resolve()
    excel = Path(report["excel_path"]).resolve()
    if not excel.is_file() or not excel.is_relative_to(root):
        raise ValueError("completed workbook must exist inside server/outputs")
    records = json.loads(Path(report["records_path"]).read_text(encoding="utf-8"))
    if not isinstance(records, list) or any(not isinstance(row, dict) or set(row) != FIELDS for row in records):
        raise ValueError("records must contain exactly the standard ten columns")
    from server.excel_export import excel_cell_issues
    cells = excel_cell_issues(records)
    if cells:
        raise ValueError(f"delivery field validation failed: {cells}")
    school = str(report.get("school", "")).strip()
    college = str(report.get("college", "")).strip()
    if not school or not college or any(
        str(row["学校"]).strip() != school or str(row["学院"]).strip() != college
        for row in records
    ):
        raise ValueError("record school or college differs from the audit")
    with_email = sum(row["邮箱"] is not None and bool(str(row["邮箱"]).strip()) for row in records)
    if (len(records) != report["total"] or with_email != report["with_email"]
            or len(records) - with_email != report["no_email"]):
        raise ValueError("audit counts do not match saved records")
    stats = {
        "total": len(records), "with_email": with_email,
        "no_email": len(records) - with_email, "unresolved": 0,
        "directory_pages": report["directory_pages"],
        "candidate_count": report["candidate_count"],
    }
    for field in ("coverage_scope", "eligibility", "excluded_count", "branches", "audit_path",
                  "coverage", "coverage_path", "checkpoints", "recon_attempts", "local_access_repairs",
                  "recon_usage_alerts", "identity_reviews", "identity_review_path", "recovery", "runtime_report",
                  "cross_check", "field_check"):
        if field in report:
            stats[field] = report[field]
    if report.get("audit_path"):
        stats["audit_sha256"] = hashlib.sha256(Path(report_path).read_bytes()).hexdigest()
    return {"records": records, "stats": stats,
            "download_url": f"/download/{excel.name}", "progress": 100,
            "worker_id": worker_id, "lease_token": lease_token}


async def sync_report(task_id: str, report_path: str | Path, worker_id: str,
                      lease_token: str, *, output_root: str | Path = OUTPUT_ROOT,
                      db_path: str | Path | None = TASK_DB,
                      client: httpx.AsyncClient) -> dict:
    payload = build_payload(report_path, worker_id, lease_token, output_root)
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    school = str(report.get("school", "")).strip()
    if not school:
        raise ValueError("audit requires school for university delivery")
    safe_school = re.sub(r'[\\/:*?"<>|]', "", school).strip() or "高校"
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    if db_path is None:
        from server.agent_queue import default_db_path
        db_path = default_db_path()
    database = Path(db_path)
    # The historical minimal database schema remains supported for offline clients.
    # Production queues have persisted college and batch scope and never use that fallback.
    if database.exists():
        with sqlite3.connect(database) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(tasks)")}
        if {"college", "batch_id", "task_type", "stats_json"}.issubset(columns):
            from server.agent_queue import AgentTaskQueue
            from server.university_delivery import publish_delivery
            queue = AgentTaskQueue(database)
            task = queue.get_task(task_id)
            if task is None or task.school != school or task.college != report["college"]:
                raise ValueError("audit task school or college differs from the queue")
            response = await client.post(f"/api/task/{quote(task_id, safe='')}/sync", json=payload)
            response.raise_for_status()
            task = queue.get_task(task_id)
            if task is None or task.status != "completed":
                raise ValueError("sync did not persist a completed college snapshot")
            result = response.json()
            if "delivery_scope" in result:
                # The Web sync endpoint already published this university; building
                # the same workbooks again here only lengthened the lock window.
                result.setdefault("university_excel", None)
                result.setdefault("current_identity_conflicts", [])
                return result
            delivery = await asyncio.to_thread(publish_delivery, queue, task.batch_id, school, root)
            result.update({"university_excel": delivery.get("university_excel"),
                           "delivery_scope": delivery["delivery_scope"],
                           "delivery_version": delivery.get("version"),
                           "university_download_url": delivery.get("download_url"),
                           "current_snapshot_state": delivery.get("current_snapshot_state"),
                           "current_identity_conflicts": delivery.get("current_identity_conflicts", [])})
            return result
    final_path = root / f"{safe_school}.xlsx"
    task_hash = hashlib.sha256(task_id.encode("utf-8")).hexdigest()[:12]
    pending_path = root / f".{safe_school}.{task_hash}.pending.xlsx"

    combined = list(payload["records"])
    if database.exists():
        with sqlite3.connect(database) as connection:
            rows = connection.execute("""SELECT r.payload_json FROM records r
                JOIN tasks t ON r.task_id=t.task_id
                WHERE t.school=? AND t.status='completed' AND t.task_id<>?
                ORDER BY t.created_at,r.seq""", (school, task_id)).fetchall()
        combined = [json.loads(row[0]) for row in rows] + combined

    from server.excel_export import export_dual_sheet_excel

    export_dual_sheet_excel(combined, str(pending_path))
    payload["download_url"] = f"/download/{final_path.name}"
    try:
        response = await client.post(f"/api/task/{quote(task_id, safe='')}/sync", json=payload)
        response.raise_for_status()
        if database.exists():
            with sqlite3.connect(database, timeout=30) as connection:
                connection.execute("BEGIN IMMEDIATE")
                fresh_rows = connection.execute("""SELECT r.payload_json FROM records r
                    JOIN tasks t ON r.task_id=t.task_id
                    WHERE t.school=? AND t.status='completed'
                    ORDER BY t.created_at,r.seq""", (school,)).fetchall()
                if fresh_rows:
                    fresh_records = [json.loads(row[0]) for row in fresh_rows]
                    export_dual_sheet_excel(fresh_records, str(pending_path))
                pending_path.replace(final_path)
        else:
            pending_path.replace(final_path)
    finally:
        pending_path.unlink(missing_ok=True)
    result = response.json()
    result["university_excel"] = str(final_path)
    result["delivery_scope"] = {"state": "ready", "school": school,
                                "target_colleges": [report["college"]],
                                "coverage_scope": "legacy_unscoped_database" if database.exists() else "single_college"}
    return result


async def _run_cli(args: argparse.Namespace) -> dict:
    parsed = urlparse(args.base_url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("sync endpoint must be the local backend")
    lease_token = os.environ.get("HARVEST_LEASE_TOKEN", "")
    async with httpx.AsyncClient(base_url=args.base_url, timeout=30) as client:
        return await sync_report(args.task_id, args.audit, args.worker_id,
                                 lease_token, client=client)


def main() -> None:
    import sys
    from server.local_service_config import local_web_url
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Sync a verified browser batch to the local task queue")
    parser.add_argument("--audit", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--base-url", default=local_web_url())
    args = parser.parse_args()
    result = asyncio.run(_run_cli(args))
    print(json.dumps({"status": result.get("status"), "task_id": args.task_id,
                      "university_excel": result.get("university_excel")},
                     ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
