"""Evidence-backed website shape observations, separate from reusable plans.

An observed type is a research case, not permission to reuse CSS or deliver data.
Observed cases must not enter the verified plan cache or imply completed collection.
"""

from __future__ import annotations

import hashlib
import json
import ast
from pathlib import Path
import re
import sqlite3
import time


_FAILURE_CODE = re.compile(r"^[a-z][a-z0-9_]{1,79}$")


def classify_failure(error: str, stats: dict) -> tuple[list[str], str, str]:
    """Return reason codes, family and evidence quality, without storing raw errors."""
    raw_codes = stats.get("failure_codes")
    if isinstance(raw_codes, list):
        codes = sorted({code for code in raw_codes
                        if isinstance(code, str) and _FAILURE_CODE.fullmatch(code)})
        if codes:
            return codes, "audit", "structured"

    match = re.search(r"unresolved=(\d+)\s+(\[[^\]]{0,1024}\])", error)
    if match:
        try:
            labels = ast.literal_eval(match.group(2))
        except (ValueError, SyntaxError):
            labels = None
        if isinstance(labels, list):
            codes = sorted({code for code in labels
                            if isinstance(code, str) and _FAILURE_CODE.fullmatch(code)})
            if codes:
                quality = "legacy_partial" if int(match.group(1)) > len(labels) else "legacy_label"
                return codes, "audit", quality
    if error.startswith("审计未通过交付门禁"):
        return ["audit_unclassified"], "audit", "fallback"
    if "缺少入口 URL" in error:
        return ["missing_entry_url"], "input", "legacy_label"
    if "超过最大运行时长" in error:
        return ["task_timeout"], "runtime", "legacy_label"
    if error.startswith("侦察失败"):
        if "侦察预算耗尽" in error:
            return ["recon_budget_exceeded"], "recon", "legacy_label"
        if "成本保护已停止" in error:
            return ["recon_cost_guard_manual_stop"], "recon", "legacy_label"
        if "超时" in error:
            return ["recon_timeout"], "recon", "legacy_label"
        if "_validate_plan" in error or "plan requires" in error:
            return ["recon_plan_invalid"], "recon", "legacy_label"
        return ["recon_failure"], "recon", "fallback"
    if error.startswith("同步回后台失败"):
        return ["delivery_sync_failure"], "delivery", "legacy_label"
    if error.startswith("审计文件缺失"):
        return ["missing_audit_file"], "audit", "legacy_label"
    if error.startswith("官网无在职专任教师名录"):
        return ["no_official_roster"], "source", "legacy_label"
    return ["unclassified_failure"], "unknown", "fallback"


class SiteTypeRegistry:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as con:
            con.execute("""CREATE TABLE IF NOT EXISTS site_type_observations (
                observation_id INTEGER PRIMARY KEY,
                school TEXT NOT NULL, college TEXT NOT NULL,
                entry_url TEXT NOT NULL, branch_url TEXT NOT NULL,
                shape_code TEXT NOT NULL, features_json TEXT NOT NULL,
                evidence_path TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('observed','accepted','rejected')),
                observed_at REAL NOT NULL,
                UNIQUE(branch_url, shape_code, evidence_sha256)
            )""")
            con.execute("""CREATE INDEX IF NOT EXISTS site_type_counts
                ON site_type_observations(shape_code, status)""")
            con.execute("""CREATE TABLE IF NOT EXISTS failure_type_observations (
                task_id TEXT NOT NULL, reason_code TEXT NOT NULL,
                family TEXT NOT NULL, evidence_quality TEXT NOT NULL,
                school TEXT NOT NULL, college TEXT NOT NULL, entry_url TEXT NOT NULL,
                finished_at REAL NOT NULL,
                PRIMARY KEY(task_id, reason_code)
            )""")
            con.execute("""CREATE INDEX IF NOT EXISTS failure_type_counts
                ON failure_type_observations(family, reason_code)""")
            con.execute("""CREATE TABLE IF NOT EXISTS failure_type_history (
                task_id TEXT NOT NULL, finished_at REAL NOT NULL,
                reason_code TEXT NOT NULL, family TEXT NOT NULL,
                evidence_quality TEXT NOT NULL, school TEXT NOT NULL,
                college TEXT NOT NULL, entry_url TEXT NOT NULL,
                PRIMARY KEY(task_id, finished_at, reason_code)
            )""")
            con.execute("""CREATE INDEX IF NOT EXISTS failure_history_counts
                ON failure_type_history(family, reason_code)""")

    def record(self, *, school: str, college: str, entry_url: str, branch_url: str,
               shape_code: str, features: dict, evidence_path: str | Path) -> int:
        path = Path(evidence_path).resolve(strict=True)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        payload = json.dumps(features, ensure_ascii=False, sort_keys=True, allow_nan=False)
        with sqlite3.connect(self.db_path, timeout=30) as con:
            con.execute("""INSERT INTO site_type_observations
                (school,college,entry_url,branch_url,shape_code,features_json,
                 evidence_path,evidence_sha256,status,observed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(branch_url,shape_code,evidence_sha256) DO UPDATE SET
                    features_json=excluded.features_json, observed_at=excluded.observed_at""",
                (school, college, entry_url, branch_url, shape_code, payload,
                 str(path), digest, "observed", time.time()))
            row = con.execute("""SELECT observation_id FROM site_type_observations
                WHERE branch_url=? AND shape_code=? AND evidence_sha256=?""",
                (branch_url, shape_code, digest)).fetchone()
        return row[0]

    def reconcile_failures(self, failed_tasks: list[dict]) -> None:
        """Mirror current failures and retain each observed failed attempt permanently."""
        current: set[tuple[str, str]] = set()
        with sqlite3.connect(self.db_path, timeout=30) as con:
            for task in failed_tasks:
                try:
                    stats = json.loads(task["stats_json"] or "{}")
                except (ValueError, TypeError):
                    stats = {}
                if not isinstance(stats, dict):
                    stats = {}
                codes, family, quality = classify_failure(task["error"] or "", stats)
                finished_at = task["finished_at"] or 0
                placeholders = ",".join("?" for _ in codes)
                con.execute(f"""DELETE FROM failure_type_history
                    WHERE task_id=? AND finished_at=? AND reason_code NOT IN ({placeholders})""",
                    (task["task_id"], finished_at, *codes))
                for code in codes:
                    key = (task["task_id"], code)
                    current.add(key)
                    con.execute("""INSERT INTO failure_type_observations
                        (task_id,reason_code,family,evidence_quality,school,college,entry_url,finished_at)
                        VALUES (?,?,?,?,?,?,?,?)
                        ON CONFLICT(task_id,reason_code) DO UPDATE SET
                            family=excluded.family,evidence_quality=excluded.evidence_quality,
                            school=excluded.school,college=excluded.college,
                            entry_url=excluded.entry_url,finished_at=excluded.finished_at""",
                        (*key, family, quality, task["school"], task["college"],
                         task["url"] or "", finished_at))
                    con.execute("""INSERT INTO failure_type_history
                        (task_id,finished_at,reason_code,family,evidence_quality,school,college,entry_url)
                        VALUES (?,?,?,?,?,?,?,?)
                        ON CONFLICT(task_id,finished_at,reason_code) DO UPDATE SET
                            family=excluded.family,evidence_quality=excluded.evidence_quality,
                            school=excluded.school,college=excluded.college,
                            entry_url=excluded.entry_url""",
                        (task["task_id"], finished_at, code, family, quality,
                         task["school"], task["college"], task["url"] or ""))
            stored = con.execute("SELECT task_id,reason_code FROM failure_type_observations").fetchall()
            con.executemany("""DELETE FROM failure_type_observations
                WHERE task_id=? AND reason_code=?""", [key for key in stored if key not in current])

    def failure_counts(self) -> dict:
        with sqlite3.connect(self.db_path, timeout=30) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""SELECT family,reason_code,COUNT(*) AS task_count,
                SUM(evidence_quality='legacy_partial') AS partial_count
                FROM failure_type_observations GROUP BY family,reason_code
                ORDER BY task_count DESC, family, reason_code""").fetchall()
            total = con.execute("""SELECT COUNT(DISTINCT task_id)
                FROM failure_type_observations""").fetchone()[0]
            historic = con.execute("""SELECT COUNT(*) FROM (
                SELECT task_id,finished_at FROM failure_type_history
                GROUP BY task_id,finished_at)""").fetchone()[0]
            history_rows = con.execute("""SELECT family,reason_code,COUNT(*) AS failure_events
                FROM failure_type_history GROUP BY family,reason_code
                ORDER BY failure_events DESC,family,reason_code""").fetchall()
        return {"failed_tasks": total, "types": [dict(row) for row in rows],
                "historical_failure_events": historic,
                "historical_types": [dict(row) for row in history_rows]}


def record_audit_shapes(audit_path: str | Path, registry: SiteTypeRegistry) -> list[int]:
    """Register observed supported shapes from local, hash-bound audit evidence.

    The producer uses only structure counts, not names, titles, emails or text.
    This never approves a plan, rewrites task state or marks a type accepted.
    """
    audit = json.loads(Path(audit_path).read_text(encoding='utf-8'))
    evidence = {item['media_url']: item for item in audit.get('pdf_roster_evidence', [])}
    ids = []
    for item in audit.get('roster_observations', []):
        if item.get('kind') != 'pdf_roster' or item.get('layout') != 'rank_grid':
            continue
        source = evidence.get(item.get('media_url'))
        if source is None:
            raise ValueError('rank-grid audit is missing its original evidence')
        path = Path(source['original_path'])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != source.get('sha256') or digest != item.get('sha256'):
            raise ValueError('rank-grid evidence changed since the audit')
        features = {key: item[key] for key in ('page_count', 'pages_with_text_layer',
                   'pages_ocrd', 'records_detected', 'records_admitted') if key in item}
        if any(type(value) is not int or value < 0 for value in features.values()):
            raise ValueError('shape features must be structure counts')
        features['section_count'] = len(item.get('sections', []))
        ids.append(registry.record(school=audit['school'], college=audit['college'],
            entry_url=audit.get('entry_url') or item['url'], branch_url=item['url'],
            shape_code='pdf_rank_grouped_name_grid', features=features, evidence_path=path))
    return ids


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Register observed shapes from a saved audit')
    parser.add_argument('--audit', required=True)
    parser.add_argument('--db', required=True, help='Explicit registry path; no implicit production database')
    args = parser.parse_args()
    print(json.dumps({'observation_ids': record_audit_shapes(args.audit, SiteTypeRegistry(args.db)),
                      'status': 'observed'}, ensure_ascii=False))
