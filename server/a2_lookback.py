"""Read-only lookback for colleges that may have been delivered short by A2.

Before PR #10 the collector read each roster page right after DOMContentLoaded.
A pager, cards or navigation links that a script inserted later were missed and
the branch could still be recorded as terminated (``next_control_absent``).
This command compares each completed task's stored coverage ledger with the
recon browser evidence that verified the plan, and lists the branches where the
collector saw less than recon did. It opens the task database read-only, only
reads files, and writes nothing; a flagged college is a lead to re-check, not
proof of a short delivery.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent

# Merge time of PR #10 (ebd06fa); a service started before it still ran the old reader.
A2_FIX_MERGED_AT = datetime(2026, 10, 1, 19, 53, 9, tzinfo=timezone.utc).timestamp()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _session_ids(active_plan: dict) -> set[str]:
    recon = active_plan.get("recon") or {}
    ids = {recon.get("session_id")}
    ids.update(phase.get("session_id") for phase in recon.get("phases") or [])
    ids.update(attempt.get("session_id") for attempt in active_plan.get("recon_attempts") or [])
    return {sid for sid in ids if isinstance(sid, str) and sid}


def recon_observations(evidence_dirs: list[Path]) -> dict[str, list[dict]]:
    """Every saved browser_verify result, keyed by the page URL it was taken on."""
    by_url: dict[str, list[dict]] = {}
    for root in evidence_dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.json")):
            value = _load_json(path)
            if not isinstance(value, dict) or type(value.get("cardCount")) is not int or not value.get("url"):
                continue
            by_url.setdefault(value["url"], []).append(
                {"cardCount": value["cardCount"], "nextCount": value.get("nextCount"),
                 "nextUrl": value.get("nextUrl"), "file": str(path)})
    return by_url


def _navigates(next_url: Any, page_url: str) -> bool:
    """Recon saved no selector, so only a next link that leads to another page counts."""
    return (isinstance(next_url, str) and next_url.startswith(("http://", "https://"))
            and next_url.split("#")[0] != page_url.split("#")[0])


def _plan_branches(plan: dict) -> dict[str, dict]:
    return {branch["url"]: branch for branch in plan.get("branches") or [] if branch.get("url")}


def branch_findings(plan: dict, coverage: dict, observations: dict[str, list[dict]]) -> list[dict]:
    """Where the stored capture saw fewer pages, cards or child links than recon."""
    findings = []
    planned = _plan_branches(plan)
    pages_by_branch: dict[str, list[str]] = {}
    for page in coverage.get("pages") or []:
        pages_by_branch.setdefault(page.get("branch_url"), []).append(page.get("url"))
    cards = {row.get("url"): row.get("visible_cards") for row in coverage.get("roster_observations") or []}

    def check_paged(evidence: dict, branch: dict) -> None:
        url = evidence.get("url")
        pages = pages_by_branch.get(url) or [url]
        if branch.get("next_selector") and evidence.get("end") == "next_control_absent":
            last = pages[-1]
            # An AJAX pager keeps one URL for every page; only the entry page is then comparable.
            if evidence.get("visited_pages") == 1 or pages.count(last) == 1:
                # A next control whose link goes nowhere (javascript:void(0)) marks the last page.
                seen = [row for row in observations.get(last, []) if row.get("nextCount") == 1
                        and _navigates(row.get("nextUrl"), last)]
                if seen or evidence.get("visited_pages") == 1:
                    findings.append({
                        "branch": url, "check": "stopped_where_recon_saw_next",
                        "visited_pages": evidence.get("visited_pages"), "last_page": last,
                        "recon_next_seen": bool(seen), "evidence": [row["file"] for row in seen][:3]})
        for page_url in dict.fromkeys(pages):
            recon = [row["cardCount"] for row in observations.get(page_url, []) if row["cardCount"] > 0]
            captured = cards.get(page_url)
            if recon and isinstance(captured, int) and captured < min(recon):
                findings.append({"branch": url, "check": "fewer_cards_than_recon", "page": page_url,
                                 "captured_cards": captured, "recon_cards": min(recon)})

    for evidence in coverage.get("branches") or []:
        branch = planned.get(evidence.get("url"), {})
        if branch.get("kind") == "navigation" or "child_visits" in evidence:
            recon = [row["cardCount"] for row in observations.get(evidence.get("url"), []) if row["cardCount"] > 0]
            discovered = evidence.get("discovered_children")
            if recon and isinstance(discovered, int) and discovered < min(recon):
                findings.append({"branch": evidence.get("url"), "check": "fewer_children_than_recon",
                                 "discovered_children": discovered, "recon_links": min(recon)})
            for child in evidence.get("child_visits") or []:
                check_paged(child, branch.get("child_branch") or {})
        else:
            check_paged(evidence, branch)
    return findings


def legacy_findings(branches: list[dict]) -> list[dict]:
    """Without the plan or recon, only a pager that ended on its entry page stands out."""
    return [{"branch": item.get("url"), "check": "stopped_on_entry_page", "visited_pages": 1}
            for item in branches if isinstance(item, dict)
            and item.get("end") == "next_control_absent" and item.get("visited_pages") == 1]


def lookback(db_path: Path, outputs_dir: Path) -> list[dict]:
    with closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as connection:
        rows = connection.execute(
            "SELECT task_id, school, college, finished_at, stats_json FROM tasks "
            "WHERE status='completed' AND task_type='harvest' ORDER BY finished_at").fetchall()
    report = []
    for task_id, school, college, finished_at, stats_json in rows:
        try:
            stats = json.loads(stats_json or "{}")
        except ValueError:
            stats = {}
        entry = {"task_id": task_id, "school": school, "college": college, "total": stats.get("total"),
                 "finished_at": datetime.fromtimestamp(finished_at, timezone.utc).isoformat() if finished_at else None,
                 "before_fix_merge": bool(finished_at and finished_at < A2_FIX_MERGED_AT)}
        coverage = stats.get("coverage")
        task_dir = outputs_dir / "tasks" / task_id
        active = _load_json(task_dir / "active-plan.json")
        if not isinstance(coverage, dict) or not isinstance(active, dict):
            # Earlier IDE runs left only a flat audit beside the workbook, without recon evidence.
            audit = _load_json(outputs_dir / f"{school}_{college}_audit.json")
            if isinstance(audit, dict) and isinstance(audit.get("branches"), list):
                findings = legacy_findings(audit["branches"])
                report.append({**entry, "status": "suspect" if findings else "legacy_audit_only",
                               "findings": findings})
            else:
                report.append({**entry, "status": "no_coverage_evidence"})
            continue
        dirs = [task_dir / "cache-evidence"]
        recon_root = outputs_dir / "recon-evidence"
        for sid in sorted(_session_ids(active)):
            dirs.extend(sorted(recon_root.glob(f"{sid}*")))
        observations = recon_observations(dirs)
        findings = branch_findings(active.get("plan") or {}, coverage, observations)
        report.append({**entry, "status": "suspect" if findings else "no_finding",
                       "recon_pages_observed": len(observations), "findings": findings})
    return report


def _print_text(report: list[dict]) -> None:
    labels = {"suspect": "可疑", "no_finding": "未见异常",
              "legacy_audit_only": "只有旧版审计、无侦察证据，仅查首页即停", "no_coverage_evidence": "无覆盖证据"}
    for status in labels:
        group = [row for row in report if row["status"] == status]
        print(f"\n## {labels[status]}：{len(group)} 个已完成学院工单")
        for row in group:
            print(f"- {row['school']} {row['college']}（{row['task_id'][:8]}，{row['total']} 人，"
                  f"完成于 {row['finished_at']}{'，早于 A2 修复合并' if row['before_fix_merge'] else ''}）")
            for item in row.get("findings", []):
                print(f"    {item['check']}: {json.dumps(item, ensure_ascii=False)}")
            if status == "no_finding" and not row.get("recon_pages_observed"):
                print("    注意：没找到侦察证据，只核对了覆盖账本本身")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读回查：A2 修复前可能少采的已交付学院")
    parser.add_argument("--db-path", type=Path,
                        default=Path(os.environ.get("HARVEST_TASK_DB") or _ROOT / "server/tasks.db"))
    parser.add_argument("--outputs-dir", type=Path, default=_ROOT / "server/outputs")
    parser.add_argument("--json", action="store_true", help="输出完整 JSON")
    args = parser.parse_args(argv)
    if not args.db_path.is_file():
        parser.error(f"任务库不存在：{args.db_path}")
    report = lookback(args.db_path, args.outputs_dir)
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
