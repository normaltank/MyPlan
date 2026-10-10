"""Observe eight MyPlan contracts using synthetic, offline inputs.

From the repository root, with its locked development dependencies installed::

    .venv/bin/python scripts/reproduce_contract_audit.py --repo .

This is a manual audit observer, not a test that requires bugs to persist.
Each observation reports current behavior, including a rejection after a fix.
No websites, models, production queues or remote Git operations are used.
All databases and workbooks are created in the reported temporary directory.
The field example covers deterministic extraction through an actual workbook;
it does not run the full browser plan, which has additional cleanup for some
source types. The single-input example loads only the CollectRequest class
from app.py's AST, avoiding global application initialization.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch


AUDITED_REMOTE_BASELINE = "51b5178982efd472eed35851848f92c587320932"
SOURCE_FILES = (
    "server/intake.py", "server/agent_queue.py", "server/university_delivery.py",
    "server/case_replay.py", "server/faculty_extractor.py", "server/faculty_identity.py",
    "server/titles.py", "server/delivery_fields.py", "server/excel_export.py", "server/app.py",
    "server/browser_batch_tool.py",
)


def finish(queue, task, records):
    claim = queue.claim_task("contract-audit", task_id=task.task_id)
    emails = sum(bool(record.get("邮箱")) for record in records)
    queue.complete_task(
        task.task_id, records,
        {"total": len(records), "with_email": emails, "no_email": len(records) - emails},
        worker_id=claim.worker_id, lease_token=claim.lease_token,
    )


def synthetic_record(college="C"):
    return {
        "序号": 1, "姓名": "张三", "职称": "教授", "邮箱": "", "学校": "U", "学院": college,
        "采集人": "synthetic-audit", "采集日期": "2026-10-11", "简介": "合成样本的完整简介",
        "主页": "https://example.edu/p1/",
    }


def workbook_rows(path):
    import openpyxl

    book = openpyxl.load_workbook(path)
    try:
        result = {}
        for sheet in book.worksheets:
            values = list(sheet.iter_rows(values_only=True))
            result[sheet.title] = [dict(zip(values[0], row)) for row in values[1:]] if values else []
        return result
    finally:
        book.close()


def observe_tsv(_repo, _root):
    from server.intake import parse_batch

    return [
        {"input": text, "parsed": asdict(parse_batch(text))}
        for text in (
            "U\tC\t\thttps://example.edu/faculty/",
            "U\t\thttps://example.edu/college/\thttps://example.edu/faculty/",
        )
    ]


def observe_snapshot(_repo, root):
    from server.agent_queue import AgentTaskQueue
    from server.university_delivery import manifest_snapshot_is_safe, publish_delivery

    queue = AgentTaskQueue(root / "snapshot" / "tasks.db", legacy_json_path=None)
    task = queue.submit_task("U", "C", "https://example.edu/C/")
    finish(queue, task, [synthetic_record()])
    delivery = publish_delivery(queue, task.batch_id, task.school, root / "snapshot" / "outputs")
    manifest = json.loads(Path(delivery["manifest_path"]).read_text(encoding="utf-8"))
    before = manifest_snapshot_is_safe(queue, manifest)
    marked = queue.mark_no_faculty(task.task_id, 1)
    current = queue.get_task(task.task_id)
    return {
        "safe_before": before, "mark_no_faculty": marked,
        "safe_after": manifest_snapshot_is_safe(queue, manifest),
        "old_manifest_total": manifest["total"], "current_task_total": current.stats["total"],
        "current_record_count": len(current.records),
        "current_scope_state": queue.get_delivery_scope(task.batch_id, task.school)["state"],
        "old_workbook": workbook_rows(delivery["excel_path"]),
        "workbook_path": delivery["excel_path"],
        "boundary": "Publication and snapshot checker only; no HTTP download request.",
    }


def observe_regression_selection(_repo, root):
    from server.case_replay import _summary, discover

    cases = root / "cases"
    for name, stamp, ready in (
        ("historical_success", "2026-10-01T00:00:00Z", True),
        ("later_capture", "2026-10-10T00:00:00Z", False),
    ):
        directory = cases / name
        (directory / "out").mkdir(parents=True)
        (directory / "plan.json").write_text(json.dumps({
            "school": "U", "college": "C", "branches": [{
                "url": "https://example.edu/roster", "card_selector": "li", "single_page": True,
            }],
        }), encoding="utf-8")
        (directory / "result.json").write_text(json.dumps({
            "delivery_ready": ready, "total": 1, "with_email": 0, "unresolved": [],
        }), encoding="utf-8")
        (directory / "out" / "case_audit.json").write_text("{}", encoding="utf-8")
        (directory / "captured_at").write_text(stamp, encoding="utf-8")
    old = {"delivery_ready": True, "total": 1, "with_email": 0, "unresolved": [],
           "records": [{"name": "Synthetic A", "bio": "original synthetic text"}]}
    new = {**old, "records": [{"name": "Synthetic B", "bio": "different synthetic text"}]}
    return {
        "default_selected": [case.name for case in discover(cases)],
        "both_explicitly_named": [case.name for case in discover(
            cases, ["historical_success", "later_capture"])],
        "old_summary": _summary(old), "changed_summary": _summary(new),
        "same_summary_after_person_and_bio_changed": _summary(old) == _summary(new),
        "boundary": "Discovery and summary helpers only; no browser replay.",
    }


def observe_empty_receipt(_repo, root):
    from server.agent_queue import AgentTaskQueue
    from server.university_delivery import publish_delivery

    queue = AgentTaskQueue(root / "empty" / "tasks.db", legacy_json_path=None)
    task = queue.submit_task("U", "C", "https://example.edu/C/")
    finish(queue, task, [])
    delivery = publish_delivery(queue, task.batch_id, task.school, root / "empty" / "outputs")
    saved = queue.get_task(task.task_id)
    return {
        "task_status": saved.status, "has_no_faculty_review": bool(saved.stats.get("no_faculty")),
        "scope_state": delivery["delivery_scope"]["state"], "total": delivery.get("total"),
        "workbook_created": bool(delivery.get("excel_path")) and Path(delivery["excel_path"]).is_file(),
        "has_coverage_evidence": "coverage" in saved.stats,
        "boundary": "Legacy direct queue receipt; standard browser receipts have additional gates.",
    }


def observe_missing_fields(_repo, root):
    from server.excel_export import export_dual_sheet_excel
    from server.faculty_extractor import FacultyDirectoryExtractor, FacultyHarvestPipeline

    roster = "https://example.edu/roster"
    html = "<main><ul><li><span>张三</span></li></ul></main>"
    candidates, review = FacultyDirectoryExtractor.extract_with_review(html, roster, card_selector="main li")
    result = FacultyHarvestPipeline.harvest_candidates(
        candidates, lambda _url: None, base_url=roster, school="U", college="C",
        agent_name="synthetic-audit", profileless_urls={roster},
    )
    rows = [{key: value for key, value in row.items() if not key.startswith("_")}
            for row in result["sheet1_with_email"] + result["sheet2_no_email"]]
    path = root / "missing-fields.xlsx"
    export_dual_sheet_excel(rows, str(path))
    return {
        "input_html": html, "candidates": [asdict(candidate) for candidate in candidates],
        "name_review": review, "delivery_ready": result["delivery_ready"],
        "unresolved": result["unresolved"], "records": rows, "workbook": workbook_rows(path),
        "workbook_path": str(path),
        "boundary": "Deterministic extraction to workbook only; run_plan is not invoked. "
                    "run_plan has additional homepage cleanup for image-table records.",
    }


def observe_same_name(_repo, _root):
    from server.faculty_extractor import FacultyCandidate
    from server.faculty_identity import merge_same_name_observations

    candidates = [
        FacultyCandidate("张三", "https://example.edu/roster/a", "教授", "张三 教授 研究方向：数学"),
        FacultyCandidate("张三", "https://example.edu/roster/b", "讲师", "张三 讲师 研究方向：音乐"),
    ]
    kept, sources, merges = merge_same_name_observations(
        candidates, {(candidate.name, candidate.url): {"university_faculty_roster"}
                     for candidate in candidates}, {candidate.url for candidate in candidates},
    )
    return {
        "input_candidates": [asdict(candidate) for candidate in candidates],
        "output_candidates": [asdict(candidate) for candidate in kept], "merges": merges,
        "output_sources": [{"name": key[0], "url": key[1], "source_kinds": sorted(value)}
                           for key, value in sorted(sources.items())],
    }


def observe_affiliations(_repo, root):
    from server.agent_queue import AgentTaskQueue
    from server.university_delivery import publish_delivery

    queue = AgentTaskQueue(root / "affiliations" / "tasks.db", legacy_json_path=None)
    for college in ("A", "B"):
        task = queue.submit_task("U", college, "https://example.edu/" + college,
                                 mode="batch", batch_id="synthetic-batch")
        finish(queue, task, [synthetic_record(college)])
    delivery = publish_delivery(queue, "synthetic-batch", "U", root / "affiliations" / "outputs")
    manifest = json.loads(Path(delivery["manifest_path"]).read_text(encoding="utf-8"))
    return {
        "total": delivery["total"], "manifest_affiliations": [item["colleges"] for item in manifest["identities"]],
        "workbook": workbook_rows(delivery["excel_path"]), "workbook_path": delivery["excel_path"],
    }


def observe_single_request(repo, _root):
    from typing import Optional
    from pydantic import BaseModel

    path = repo / "server" / "app.py"
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    definition = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "CollectRequest")
    isolated = ast.Module(body=[definition], type_ignores=[])
    namespace = {"__name__": "_audit_collect_request", "BaseModel": BaseModel, "Optional": Optional}
    exec(compile(isolated, filename=str(path), mode="exec"), namespace)
    submitted = {"school": "U", "college": "C", "url": "https://example.edu/C/",
                 "roster_urls": ["https://example.edu/roster"]}
    return {
        "submitted": submitted,
        "parsed": namespace["CollectRequest"].model_validate(submitted).model_dump(),
        "boundary": "Only the actual request class is evaluated; app.py is not imported or started. "
                    "If that class gains new dependencies, this loader may need updating.",
    }


def refuse_connection(*_args, **_kwargs):
    raise RuntimeError("This synthetic audit does not permit network connections")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="MyPlan repository root")
    args = parser.parse_args()
    repo = args.repo.resolve()
    missing = [path for path in SOURCE_FILES if not (repo / path).is_file()]
    if missing:
        parser.error("Required repository files are missing: " + ", ".join(missing))
    root = Path(tempfile.mkdtemp(prefix="myplan-contract-audit-"))
    os.environ.update({
        "HARVEST_TASK_DB": str(root / "defaults" / "tasks.db"),
        "HARVEST_SITE_CLOCK_DB": str(root / "site-clock.db"),
        "FACULTY_RECON_EVIDENCE_ROOT": str(root / "recon-evidence"),
        "HARVEST_PER_SITE_INTERVAL_SECONDS": "0", "HARVEST_CROSS_CHECK": "0",
        "HARVEST_DISABLE_NATIVE_CHROME_FALLBACK": "1",
    })
    sys.path.insert(0, str(repo))
    results = {
        "original_audit_baseline": AUDITED_REMOTE_BASELINE,
        "repository": str(repo), "reproduction_directory": str(root),
        "source_sha256": {path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in SOURCE_FILES},
        "interpretation": "Observations, not pass/fail assertions. Rejections after a code change are retained "
                          "for review; an exception alone does not prove a fix. All inputs are synthetic.",
    }
    observations = (
        ("S1_tsv_columns", observe_tsv), ("S2_invalidated_snapshot", observe_snapshot),
        ("S3_regression_selection", observe_regression_selection), ("S4_empty_receipt", observe_empty_receipt),
        ("P1_missing_fields", observe_missing_fields), ("P2_same_name_merge", observe_same_name),
        ("P3_multi_college_export", observe_affiliations), ("P4_single_collect_schema", observe_single_request),
    )
    with ExitStack() as stack:
        stack.enter_context(patch("socket.create_connection", refuse_connection))
        stack.enter_context(patch.object(socket.socket, "connect", refuse_connection))
        stack.enter_context(patch.object(socket.socket, "connect_ex", refuse_connection))
        for name, observe in observations:
            try:
                results[name] = observe(repo, root)
            except Exception as exc:
                results[name] = {"observation_exception": type(exc).__name__, "message": str(exc)}
    encoded = json.dumps(results, ensure_ascii=False, indent=2)
    (root / "observations.json").write_text(encoded + "\n", encoding="utf-8")
    print(encoded)


if __name__ == "__main__":
    main()
