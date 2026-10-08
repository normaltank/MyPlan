"""Persistent SQLite task queue. Transactions provide inter-process safety."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator
from zoneinfo import ZoneInfo

_HERE = Path(__file__).resolve().parent
_LEGACY = _HERE / "agent_queue.json"
_LOG = logging.getLogger(__name__)


def default_db_path() -> Path:
    """The queue database used when no path is given: ``HARVEST_TASK_DB`` or server/tasks.db.

    Every component that falls back to the default (Web app, worker, extractor
    cache, sync CLI) resolves it here, so one override moves all of them.
    """
    override = os.environ.get("HARVEST_TASK_DB")
    return Path(override) if override else _HERE / "tasks.db"


class LeaseConflictError(RuntimeError):
    """A worker lost ownership or its lease expired."""


class AgentTask:
    """Mutable compatibility view used by the existing Web and browser code."""

    def __init__(self, task_id: str, school: str, college: str, url: str | None = None,
                 mode: str = "single", batch_id: str | None = None,
                 task_type: str = "harvest") -> None:
        self.task_id, self.school, self.college = task_id, school, college
        self.url, self.mode, self.batch_id = url, mode, batch_id or task_id
        self.task_type, self.status = task_type, "queued"
        self.owner_user_id: int | None = None
        # Roster links a person supplied with the college; leads for the scout.
        self.roster_urls: list[str] = []
        self.worker_id: str | None = None
        self.lease_until: float | None = None
        self.lease_token: str | None = None
        self.cluster_key: str | None = None
        self.prompt_text: str | None = None
        self.progress = 0
        self.current_step = "工单已进入队列，等待智能体接单..."
        self.logs: list[str] = []
        self.agent_thoughts: list[dict[str, str]] = []
        self.stats: dict[str, Any] = {}
        self.download_url: str | None = None
        self.teachers_preview: list[dict[str, Any]] = []
        self.records: list[dict[str, Any]] = []
        self.error: str | None = None
        self.created_at = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None

    def add_log(self, msg: str) -> None:
        self.logs.append(msg)
        self.current_step = msg

    def add_thought(self, phase: str, thought: str, action: str) -> None:
        self.agent_thoughts.append({"time": time.strftime("%H:%M:%S"),
                                    "phase": phase, "thought": thought, "action": action})
        self.logs.append(f"[{phase}] {thought}")
        self.current_step = f"[{phase}] {action}"

    def to_dict(self) -> dict[str, Any]:
        fields = ("task_id", "school", "college", "url", "roster_urls", "mode", "batch_id", "task_type",
                  "owner_user_id",
                  "status", "worker_id", "lease_until", "lease_token", "cluster_key", "prompt_text",
                  "progress", "current_step", "logs", "agent_thoughts", "stats",
                  "download_url", "teachers_preview", "error", "created_at",
                  "started_at", "finished_at")
        return {field: getattr(self, field) for field in fields}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _value(record: dict[str, Any], en: str, zh: str) -> str:
    value = record.get(en, record.get(zh, ""))
    return "" if value is None else str(value)


def _site_type_registry():
    """Import the registry under both the package and the legacy flat layout."""
    try:
        from .site_type_registry import SiteTypeRegistry
    except ImportError:  # imported as top-level ``agent_queue`` from server/
        from site_type_registry import SiteTypeRegistry
    return SiteTypeRegistry


class AgentTaskQueue:
    def __init__(self, db_path: str | Path | None = None,
                 legacy_json_path: str | Path | None = None) -> None:
        builtin = db_path is None and not os.environ.get("HARVEST_TASK_DB")
        self.db_path = Path(db_path) if db_path is not None else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # SQLite is the only task store. The former in-memory views were written
        # but never read, and loaded every task with all records at import time.
        self._active_task: AgentTask | None = None
        self._init_schema()
        source = legacy_json_path if legacy_json_path is not None else (_LEGACY if builtin else None)
        if source is not None:
            self._migrate_legacy(Path(source))
        # Failure types are reconciled when a task fails and before statistics
        # are read. Constructing a queue must not create files beside the DB.

    def _reconcile_failure_types(self) -> None:
        SiteTypeRegistry = _site_type_registry()
        with self._connect() as con:
            rows = con.execute("""SELECT task_id,school,college,url,error,stats_json,finished_at
                FROM tasks WHERE task_type='harvest' AND status='failed'""").fetchall()
        SiteTypeRegistry(self.db_path.parent / "outputs" / "site-types.sqlite3").reconcile_failures(
            [dict(row) for row in rows])

    def _reconcile_failure_types_best_effort(self) -> None:
        try:
            self._reconcile_failure_types()
        except (OSError, sqlite3.Error, ValueError, ImportError) as exc:
            _LOG.warning("Failure type registry update pending for %s: %s", self.db_path, exc)

    def get_failure_type_stats(self) -> dict:
        SiteTypeRegistry = _site_type_registry()
        self._reconcile_failure_types()
        return SiteTypeRegistry(self.db_path.parent / "outputs" / "site-types.sqlite3").failure_counts()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path, timeout=5)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA foreign_keys=ON")
        try:
            with con:
                yield con
        finally:
            con.close()

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("""CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY, school TEXT NOT NULL, college TEXT NOT NULL,
                url TEXT, status TEXT NOT NULL CHECK(status IN
                    ('queued','running','completed','failed','compiling')),
                mode TEXT NOT NULL, task_type TEXT NOT NULL CHECK(task_type IN
                    ('harvest','compile_ticket')), created_at REAL NOT NULL,
                lease_until REAL, lease_token TEXT, error TEXT, worker_id TEXT, batch_id TEXT,
                cluster_key TEXT, prompt_text TEXT, stats_json TEXT NOT NULL DEFAULT '{}',
                meta_json TEXT NOT NULL DEFAULT '{}', download_url TEXT,
                started_at REAL, finished_at REAL)""")
            cols = [row[1] for row in con.execute("PRAGMA table_info(tasks)").fetchall()]
            if "lease_token" not in cols:
                con.execute("ALTER TABLE tasks ADD COLUMN lease_token TEXT")
            if "owner_user_id" not in cols:
                con.execute("ALTER TABLE tasks ADD COLUMN owner_user_id INTEGER")
            con.execute("CREATE INDEX IF NOT EXISTS idx_tasks_owner_created ON tasks(owner_user_id,created_at)")
            con.execute("""CREATE TABLE IF NOT EXISTS records (
                id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL
                    REFERENCES tasks(task_id) ON DELETE CASCADE,
                seq INTEGER NOT NULL, name TEXT NOT NULL, title TEXT NOT NULL,
                email TEXT NOT NULL, bio TEXT NOT NULL, profile_url TEXT NOT NULL,
                payload_json TEXT NOT NULL, UNIQUE(task_id, seq))""")
            con.execute("CREATE INDEX IF NOT EXISTS idx_claim ON tasks(status, lease_until, created_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_batch ON tasks(batch_id, created_at)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_records_task ON records(task_id, seq)")
            con.execute("""CREATE TABLE IF NOT EXISTS university_deliveries (
                version TEXT PRIMARY KEY,batch_id TEXT NOT NULL,school TEXT NOT NULL,
                manifest_json TEXT NOT NULL,created_at REAL NOT NULL)""")
            from server.university_identity_review import ensure_schema as ensure_university_reviews
            ensure_university_reviews(con)
            con.execute("""CREATE TABLE IF NOT EXISTS worker_presence (
                instance_id TEXT PRIMARY KEY,last_seen REAL NOT NULL)""")
            con.execute("""CREATE TABLE IF NOT EXISTS worker_selfcheck (
                id INTEGER PRIMARY KEY CHECK(id=1),checked_at REAL NOT NULL,
                ok INTEGER NOT NULL,problems_json TEXT NOT NULL)""")
            con.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_open_ticket ON tasks(cluster_key)
                WHERE task_type='compile_ticket' AND status IN ('queued','compiling','running')""")

    @staticmethod
    def _meta(task: AgentTask) -> str:
        return _json({"progress": task.progress, "current_step": task.current_step,
                      "logs": task.logs, "agent_thoughts": task.agent_thoughts,
                      "teachers_preview": task.teachers_preview, "roster_urls": task.roster_urls})

    def _insert(self, con: sqlite3.Connection, task: AgentTask) -> None:
        con.execute("""INSERT INTO tasks (task_id, school, college, url, status, mode,
            task_type, created_at, lease_until, lease_token, error, worker_id, batch_id, cluster_key,
            prompt_text, stats_json, meta_json, download_url, started_at, finished_at, owner_user_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task.task_id, task.school, task.college, task.url, task.status, task.mode,
             task.task_type, task.created_at, task.lease_until, task.lease_token, task.error, task.worker_id,
             task.batch_id, task.cluster_key, task.prompt_text, _json(task.stats),
             self._meta(task), task.download_url, task.started_at, task.finished_at, task.owner_user_id))

    def _write_records(self, con: sqlite3.Connection, task_id: str,
                       records: list[dict[str, Any]]) -> None:
        con.execute("DELETE FROM records WHERE task_id=?", (task_id,))
        con.executemany("""INSERT INTO records
            (task_id, seq, name, title, email, bio, profile_url, payload_json)
            VALUES (?,?,?,?,?,?,?,?)""", [
            (task_id, i, _value(r, "name", "姓名"), _value(r, "title", "职称"),
             _value(r, "email", "邮箱"), _value(r, "bio", "简介"),
             _value(r, "profile_url", "主页"), _json(r))
            for i, r in enumerate(records, 1)])

    def _decode(self, con: sqlite3.Connection, row: sqlite3.Row) -> AgentTask:
        task = AgentTask(row["task_id"], row["school"], row["college"], row["url"],
                         row["mode"], row["batch_id"], row["task_type"])
        for field in ("status", "owner_user_id", "worker_id", "lease_until", "lease_token", "cluster_key", "prompt_text",
                      "error", "created_at", "started_at", "finished_at", "download_url"):
            if field in row.keys():
                setattr(task, field, row[field])
        task.stats = json.loads(row["stats_json"])
        meta = json.loads(row["meta_json"])
        for field in ("progress", "current_step", "logs", "agent_thoughts", "roster_urls"):
            if field in meta:
                setattr(task, field, meta[field])
        task.records = [json.loads(r[0]) for r in con.execute(
            "SELECT payload_json FROM records WHERE task_id=? ORDER BY seq", (task.task_id,))]
        task.teachers_preview = task.records[:10] or meta.get("teachers_preview", [])
        return task

    def _migrate_legacy(self, path: Path) -> None:
        if not path.exists():
            return
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            if con.execute("SELECT 1 FROM tasks LIMIT 1").fetchone():
                return
            data = json.loads(path.read_text(encoding="utf-8"))
            for task_id, td in data.get("tasks", {}).items():
                task = AgentTask(td.get("task_id", task_id), td.get("school", ""),
                                 td.get("college", ""), td.get("url"),
                                 td.get("mode", "single"), td.get("batch_id"))
                task.status = td.get("status", "queued")
                task.lease_until = 0.0 if task.status == "running" else None
                for field in ("progress", "current_step", "logs", "agent_thoughts",
                              "teachers_preview", "stats",
                              "download_url", "error", "created_at", "started_at", "finished_at"):
                    if field in td:
                        setattr(task, field, td[field])
                self._insert(con, task)
                self._write_records(con, task.task_id, td.get("records", []))

    def submit_task(self, school: str, college: str, url: str | None = None,
                    mode: str = "single", batch_id: str | None = None,
                    *, owner_user_id: int | None = None,
                    roster_urls: list[str] | None = None) -> AgentTask:
        task = AgentTask(uuid.uuid4().hex, school, college, url, mode, batch_id)
        task.owner_user_id = owner_user_id
        task.roster_urls = list(roster_urls or [])
        with self._connect() as con:
            self._insert(con, task)
        return task

    def get_task(self, task_id: str) -> AgentTask | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            return self._decode(con, row) if row else None

    def retry_failed_review_task(self, task_id: str) -> bool:
        """Requeue one failed college after its evidence-bound human review."""
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute("SELECT status,task_type,meta_json FROM tasks WHERE task_id=?",
                              (task_id,)).fetchone()
            if not row or row['status'] != 'failed' or row['task_type'] != 'harvest':
                return False
            meta = json.loads(row['meta_json'])
            meta.update(progress=0, current_step='人工复核已保存，等待重新采集',
                        teachers_preview=[])
            updated = con.execute("""UPDATE tasks SET status='queued',worker_id=NULL,
                lease_until=NULL,lease_token=NULL,error=NULL,started_at=NULL,
                finished_at=NULL,stats_json='{}',download_url=NULL,meta_json=?
                WHERE task_id=? AND status='failed'""", (_json(meta), task_id))
        if updated.rowcount == 1:
            self._reconcile_failure_types_best_effort()
        return updated.rowcount == 1

    def get_pending_tasks(self) -> list[AgentTask]:
        with self._connect() as con:
            rows = con.execute("""SELECT * FROM tasks WHERE status='queued' OR
                (status IN ('running','compiling') AND lease_until < ?)
                ORDER BY created_at,task_id""", (time.time(),)).fetchall()
            return [self._decode(con, row) for row in rows]

    def claim_task(self, worker_id: str, lease_seconds: float = 300,
                   *, task_id: str | None = None, claimant: str | None = None,
                   task_type: str | None = None,
                   exclude_task_ids: Iterable[str] = ()) -> AgentTask | None:
        """Atomically claim a queued task or reclaim an expired lease.

        ``claimant=`` accepts the original Web API's positional task ID.
        ``exclude_task_ids`` lets a worker skip tasks it is still running, so a
        lapsed lease never makes one process run the same college twice.
        """
        if claimant is not None:
            task_id, worker_id = worker_id, claimant
        if not worker_id or lease_seconds <= 0:
            raise ValueError("worker_id and positive lease_seconds required")
        now = time.time()
        query = """SELECT task_id,task_type FROM tasks WHERE
            (status='queued' OR (status IN ('running','compiling') AND lease_until < ?))"""
        args: list[Any] = [now]
        if task_id is not None:
            query += " AND task_id=?"
            args.append(task_id)
        if task_type is not None:
            query += " AND task_type=?"
            args.append(task_type)
        excluded = sorted(set(exclude_task_ids))
        if excluded:
            query += " AND task_id NOT IN (" + ",".join("?" for _ in excluded) + ")"
            args.extend(excluded)
        query += " ORDER BY created_at,task_id LIMIT 1"
        with self._connect() as con:
            # Idle polling must not take the database write lock: look first,
            # and lock only when there is something to claim.
            if con.execute(query, args).fetchone() is None:
                return None
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(query, args).fetchone()
            if row is None:
                return None
            status = "compiling" if row["task_type"] == "compile_ticket" else "running"
            token = uuid.uuid4().hex
            con.execute("""UPDATE tasks SET status=?,worker_id=?,lease_until=?,lease_token=?,
                started_at=COALESCE(started_at,?),error=NULL WHERE task_id=?""",
                (status, worker_id, now + lease_seconds, token, now, row["task_id"]))
            claimed = self._decode(con, con.execute("SELECT * FROM tasks WHERE task_id=?",
                                                    (row["task_id"],)).fetchone())
        return claimed

    def renew_lease(self, task_id: str, worker_id: str, lease_seconds: float = 120,
                    lease_token: str | None = None) -> bool:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        if not worker_id or not lease_token:
            return False
        now = time.time()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            query = """UPDATE tasks
                       SET lease_until = ?
                       WHERE task_id = ?
                         AND worker_id = ?
                         AND lease_token = ?
                         AND status IN ('running', 'compiling')
                         AND lease_until IS NOT NULL
                         AND lease_until > ?"""
            res = con.execute(query, (now + lease_seconds, task_id, worker_id, lease_token, now))
            return res.rowcount == 1

    def complete_task(self, task_id: str, records: list[dict[str, Any]], stats: dict[str, Any],
                      *, worker_id: str | None = None, lease_token: str | None = None) -> AgentTask:
        if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
            raise TypeError("records must be a list of dictionaries")
        if not isinstance(stats, dict):
            raise TypeError("stats must be a dictionary")
        if not worker_id or not lease_token:
            raise LeaseConflictError(f"worker_id and lease_token are both mandatory for complete_task on task {task_id}")

        now = time.time()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status,worker_id,lease_until,lease_token,school,college FROM tasks WHERE task_id=?",
                              (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            if (row["status"] not in ("running", "compiling") or
                    row["lease_until"] is None or row["lease_until"] <= now or
                    row["worker_id"] != worker_id or
                    row["lease_token"] != lease_token):
                raise LeaseConflictError(task_id)

            from server.delivery_fields import alias_issues, scope_issues
            affiliations = scope_issues(records, school=row['school'], college=row['college'])
            if affiliations:
                raise ValueError(f'record scope mismatch: {affiliations}')
            aliases = alias_issues(records)
            if aliases:
                raise ValueError(f'delivery field validation failed: {aliases}')
            from server.excel_export import excel_cell_issues
            cells = excel_cell_issues(records)
            if cells:
                raise ValueError(f'delivery field validation failed: {cells}')

            # Strict conservation: stats must explicitly contain total, with_email, and no_email (no auto-inferring)
            for req_field in ("total", "with_email", "no_email"):
                if req_field not in stats:
                    raise ValueError(f"sheet conservation failed: stats must contain '{req_field}'")
            unresolved = stats.get("unresolved", 0)
            if "coverage" in stats:
                coverage = stats["coverage"]
                if (not isinstance(coverage, dict) or coverage.get("complete") is not True
                        or coverage.get("issues")):
                    raise ValueError("coverage ledger is incomplete or unresolved")
            unresolved_count = len(unresolved) if isinstance(unresolved, list) else unresolved
            if unresolved_count > 0:
                raise ValueError(f"sheet conservation failed: cannot complete task with {unresolved_count} unresolved scholars")
            if stats["with_email"] + stats["no_email"] != stats["total"]:
                raise ValueError("sheet conservation failed: with_email + no_email != total")
            if len(records) != stats["total"]:
                raise ValueError(f"sheet conservation failed: records count {len(records)} != stats total {stats['total']}")

            # Verification of record uniqueness (一人一行: no duplicate person records)
            from server.faculty_identity import identity_review_issues
            from server.identity_review import audit_pairs
            if identity_review_issues(records, audit_pairs(records, stats, task_id)):
                raise ValueError("person identity requires review for distinct profile URLs")
            seen_identities = set()
            for r in records:
                name = (r.get("name") or r.get("姓名") or "").strip()
                p_url = (r.get("profile_url") or r.get("url") or r.get("主页链接") or r.get("主页") or "").strip()
                em = (r.get("email") or r.get("邮箱") or "").strip().lower()
                t_id = (r.get("id") or r.get("teacher_id") or "").strip()
                if t_id:
                    ident = ("id", name, t_id)
                elif p_url and p_url not in ("", "/"):
                    ident = ("url", name, p_url)
                elif em:
                    ident = ("email", name, em)
                else:
                    title = (r.get("title") or r.get("职称") or "").strip()
                    card = (r.get("card_context") or r.get("简介") or "").strip()[:50]
                    # Also consider row index/序号 if present so distinct profile-less scholars on the roster are preserved
                    row_idx = str(r.get("序号", ""))
                    ident = ("raw", name, title, card, row_idx)
                if ident in seen_identities:
                    raise ValueError(f"sheet conservation failed: duplicate record detected for teacher '{name}' ({ident})")
                seen_identities.add(ident)

            # Verification of mutual exclusivity and exact email partition in records
            actual_with_em = sum(1 for r in records if (r.get("email") or r.get("邮箱") or "").strip())
            actual_no_em = sum(1 for r in records if not (r.get("email") or r.get("邮箱") or "").strip())
            if actual_with_em != stats["with_email"] or actual_no_em != stats["no_email"]:
                raise ValueError(
                    f"sheet conservation failed: records email partition ({actual_with_em} with email, {actual_no_em} without email) does not match stats ({stats['with_email']}, {stats['no_email']})"
                )

            self._write_records(con, task_id, records)
            con.execute("""UPDATE tasks SET status='completed',stats_json=?,lease_until=NULL,lease_token=NULL,
                finished_at=?,error=NULL WHERE task_id=?""", (_json(stats), now, task_id))
        task = self.get_task(task_id)
        assert task is not None
        task.progress = 100
        task.current_step = f"采集完成，入库 {stats.get('total', len(records))} 位学者"
        with self._connect() as con:
            con.execute("UPDATE tasks SET meta_json=? WHERE task_id=?", (self._meta(task), task_id))
        return task

    def create_compile_ticket(self, cluster_key: str, prompt_text: str) -> AgentTask:
        if not cluster_key or not prompt_text:
            raise ValueError("cluster_key and prompt_text required")
        task = AgentTask(uuid.uuid4().hex, "", "", mode="l3", task_type="compile_ticket")
        task.cluster_key, task.prompt_text = cluster_key, prompt_text
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("""SELECT * FROM tasks WHERE cluster_key=? AND
                task_type='compile_ticket' AND status IN ('queued','compiling','running')""",
                (cluster_key,)).fetchone()
            if row is not None:
                return self._decode(con, row)
            self._insert(con, task)
        return task

    def sync_task_result(self, task_id: str, records: list[dict[str, Any]],
                         stats: dict[str, Any], download_url: str,
                         error: str | None = None,
                         worker_id: str | None = None,
                         lease_token: str | None = None) -> AgentTask | None:
        if self.get_task(task_id) is None:
            return None
        if error:
            if not worker_id or not lease_token:
                raise LeaseConflictError(f"worker_id and lease_token are both mandatory for failure submission on task {task_id}")
            now = time.time()
            with self._connect() as con:
                con.execute("BEGIN IMMEDIATE")
                query = """UPDATE tasks
                           SET status='failed', error=?, lease_until=NULL, lease_token=NULL, finished_at=?, stats_json=?
                           WHERE task_id=?
                             AND worker_id = ?
                             AND lease_token = ?
                             AND status IN ('running', 'compiling')
                             AND lease_until IS NOT NULL
                             AND lease_until > ?"""
                params = [error, now, json.dumps(stats, ensure_ascii=False), task_id, worker_id, lease_token, now]
                res = con.execute(query, params)
                if res.rowcount != 1:
                    row = con.execute("SELECT task_id FROM tasks WHERE task_id=?", (task_id,)).fetchone()
                    if row is None:
                        raise KeyError(task_id)
                    raise LeaseConflictError(task_id)
        else:
            self.complete_task(task_id, records, stats, worker_id=worker_id, lease_token=lease_token)
            with self._connect() as con:
                con.execute("UPDATE tasks SET download_url=? WHERE task_id=?", (download_url, task_id))
        if error:
            self._reconcile_failure_types_best_effort()
        return self.get_task(task_id)

    async def get_next_task(self) -> AgentTask:
        while True:
            task = self.claim_task("server-worker", task_type="harvest")
            if task:
                self._active_task = task
                return task
            await asyncio.sleep(0.2)

    def task_done(self) -> None:
        task, self._active_task = self._active_task, None
        if task is None:
            return
        if task.status == "completed":
            self.complete_task(task.task_id, task.records, task.stats, worker_id=task.worker_id, lease_token=task.lease_token)
            with self._connect() as con:
                con.execute("UPDATE tasks SET download_url=? WHERE task_id=?",
                            (task.download_url, task.task_id))
        elif task.status == "failed":
            self.sync_task_result(task.task_id, [], {"total": 0, "with_email": 0, "no_email": 0}, "", task.error or "worker failed", worker_id=task.worker_id, lease_token=task.lease_token)

    def get_queue_stats(self, *, owner_user_id: int | None = None) -> dict[str, int]:
        with self._connect() as con:
            if owner_user_id is None:
                counts = dict(con.execute("SELECT status,COUNT(*) FROM tasks GROUP BY status"))
            else:
                counts = dict(con.execute("SELECT status,COUNT(*) FROM tasks "
                                          "WHERE owner_user_id=? GROUP BY status", (owner_user_id,)))
        return {"total_tasks": sum(counts.values()), **{s: counts.get(s, 0) for s in
                ("queued", "running", "completed", "failed", "compiling")}}

    def get_daily_counts(self, day: str, *, owner_user_id: int | None = None) -> dict[str, int | str]:
        chosen = date.fromisoformat(day)
        zone = ZoneInfo('Asia/Taipei')
        start = datetime.combine(chosen, datetime.min.time(), zone).timestamp()
        end = datetime.combine(chosen + timedelta(days=1), datetime.min.time(), zone).timestamp()
        clause = " AND owner_user_id=?" if owner_user_id is not None else ""
        owner_args: tuple[int, ...] = (owner_user_id,) if owner_user_id is not None else ()
        with self._connect() as con:
            submitted = con.execute("SELECT COUNT(*) FROM tasks WHERE task_type='harvest' "
                "AND created_at>=? AND created_at<?" + clause,
                (start, end, *owner_args)).fetchone()[0]
            finished = con.execute("SELECT status,stats_json FROM tasks WHERE task_type='harvest' "
                "AND finished_at>=? AND finished_at<?" + clause,
                (start, end, *owner_args)).fetchall()
        completed = [row for row in finished if row['status'] == 'completed']
        return {'date': day, 'submitted_colleges': submitted,
                'completed_colleges': len(completed),
                'delivered_record_rows': sum(json.loads(row['stats_json']).get('total', 0)
                                             for row in completed),
                'failed_colleges': sum(row['status'] == 'failed' for row in finished)}

    def touch_worker(self, instance_id: str) -> None:
        with self._connect() as con:
            con.execute("INSERT INTO worker_presence VALUES (?,?) ON CONFLICT(instance_id) "
                        "DO UPDATE SET last_seen=excluded.last_seen", (instance_id, time.time()))

    def remove_worker(self, instance_id: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM worker_presence WHERE instance_id=?", (instance_id,))

    def record_self_check(self, problems: list[str]) -> None:
        """Keep the latest worker start-up check so the Web page can show why it is offline."""
        with self._connect() as con:
            con.execute("""INSERT INTO worker_selfcheck VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET
                checked_at=excluded.checked_at,ok=excluded.ok,problems_json=excluded.problems_json""",
                (time.time(), int(not problems), _json(list(problems))))

    def get_worker_status(self) -> dict[str, Any]:
        now = time.time()
        with self._connect() as con:
            count, last_seen = con.execute("SELECT COUNT(*),MAX(last_seen) FROM worker_presence "
                                          "WHERE last_seen>=? AND last_seen<=?", (now - 30, now)).fetchone()
            check = con.execute("SELECT checked_at,ok,problems_json FROM worker_selfcheck WHERE id=1").fetchone()
        self_check = ({"ok": bool(check["ok"]), "problems": json.loads(check["problems_json"]),
                       "checked_at": check["checked_at"]} if check else None)
        return {"online": count > 0, "online_count": count, "last_seen": last_seen,
                "self_check": self_check}

    def get_request_history(self, limit: int = 20,
                            *, owner_user_id: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 50:
            raise ValueError('history limit must be between 1 and 50')
        if offset < 0:
            raise ValueError('history offset must be non-negative')
        with self._connect() as con:
            where = "WHERE task_type='harvest'"
            params: list[Any] = []
            if owner_user_id is not None:
                where += " AND owner_user_id=?"
                params.append(owner_user_id)
            groups = con.execute("SELECT batch_id,MAX(created_at) AS created_at FROM tasks "
                + where + " GROUP BY batch_id ORDER BY created_at DESC,batch_id DESC LIMIT ? OFFSET ?",
                (*params, limit, offset)).fetchall()
            requests = []
            for group in groups:
                rows = con.execute('SELECT task_id,mode,school,status FROM tasks WHERE batch_id=? '
                    "AND task_type='harvest'" + (" AND owner_user_id=?" if owner_user_id is not None else "")
                    + " ORDER BY created_at,task_id",
                    (group['batch_id'], owner_user_id) if owner_user_id is not None
                    else (group['batch_id'],)).fetchall()
                single = len(rows) == 1 and rows[0]['mode'] == 'single'
                requests.append({'id': rows[0]['task_id'] if single else group['batch_id'],
                    'kind': 'task' if single else 'batch', 'created_at': group['created_at'],
                    'schools': sorted({row['school'] for row in rows}), 'total_colleges': len(rows),
                    'active': any(row['status'] in {'queued', 'running', 'compiling'} for row in rows)})
        return requests

    def get_batch_status(self, batch_id: str) -> dict[str, Any]:
        with self._connect() as con:
            rows = con.execute("""SELECT * FROM tasks WHERE batch_id=? AND task_type='harvest'
                ORDER BY created_at,task_id""", (batch_id,)).fetchall()
            tasks = [self._decode(con, row) for row in rows]
        if not tasks:
            return {}
        completed = sum(t.status == "completed" for t in tasks)
        failed = sum(t.status == "failed" for t in tasks)
        running = [t for t in tasks if t.status == "running"]

        if completed == len(tasks) and failed == 0:
            batch_status = "completed"
        elif failed > 0 and (completed + failed == len(tasks)):
            batch_status = "partially_failed" if completed > 0 else "failed"
        elif running:
            batch_status = "running"
        else:
            batch_status = "queued"

        active = running[0] if running else (tasks[-1] if completed == len(tasks) else None)
        scopes = [self.get_delivery_scope(batch_id, school) for school in sorted({t.school for t in tasks})]
        return {"batch_id": batch_id, "total_colleges": len(tasks),
                "delivery_scopes": scopes,
                "delivery_state": "ready" if all(s["state"] == "ready" for s in scopes) else
                    "blocked" if any(s["state"] == "blocked" for s in scopes) else "collecting",
                "completed_colleges": completed, "failed_colleges": failed,
                "status": batch_status,
                "progress": int(sum(t.progress for t in tasks) / len(tasks)),
                "current_step": active.current_step if active else "排队中...",
                "stats": {k: sum(t.stats.get(k, 0) for t in tasks if t.status == "completed")
                          for k in ("total", "with_email", "no_email")},
                "subtasks": [t.to_dict() for t in tasks]}

    def get_delivery_scope(self, batch_id: str, school: str) -> dict[str, Any]:
        from server.university_delivery import delivery_scope, university_snapshot
        with self._connect() as con:
            scope = delivery_scope(con, batch_id, school)
            if scope["state"] == "ready":
                conflicts = university_snapshot(con, school, scope["task_ids"])["identity_conflicts"]
                scope["identity_conflicts"] = conflicts
                if any(c["requires_review"] for c in conflicts):
                    scope["state"] = "blocked"
            return scope

    def get_university_snapshot(self, school: str) -> dict[str, Any]:
        from server.university_delivery import university_snapshot
        with self._connect() as con:
            return university_snapshot(con, school)

    def submit_batch(self, items: list[tuple], batch_id: str,
                     *, owner_user_id: int | None = None) -> list[AgentTask]:
        """Expose the entire requested target set in one transaction before workers claim.

        Each item is (school, college, url) or (school, college, url, roster_urls).
        """
        tasks = []
        for school, college, url, *rest in items:
            task = AgentTask(uuid.uuid4().hex, school, college, url, "batch", batch_id)
            task.roster_urls = list(rest[0] or []) if rest else []
            tasks.append(task)
        for task in tasks:
            task.owner_user_id = owner_user_id
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            for task in tasks:
                self._insert(con, task)
        return tasks


agent_queue = AgentTaskQueue(os.environ.get("HARVEST_TASK_DB") or None)
