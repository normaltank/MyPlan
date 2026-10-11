"""Task-local rendered-page checkpoints, isolated by the complete harvest plan.

These are prior browser captures, never an independent website fetcher. Callers
must supply a directory belonging to one task. Every read verifies the saved
HTML digest and its age; changed plans use an entirely separate partition.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any


class HarvestState:
    def __init__(self, directory: str | Path, plan: dict[str, Any], *, max_age_seconds: float = 86400):
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be finite and positive")
        canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        self.plan_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        from server.page_types import selected_implementation_digest
        self.implementation_digest = selected_implementation_digest(plan, only_nonlegacy=True)
        partition = self.plan_digest
        if self.implementation_digest:
            partition += '-' + self.implementation_digest
        self.directory = Path(directory).resolve() / "page-state" / partition
        self.html_directory = self.directory / "html"
        self.html_directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / "pages.sqlite3"
        self.max_age_seconds = max_age_seconds
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS pages (
                kind TEXT NOT NULL, page_key TEXT NOT NULL, url TEXT NOT NULL,
                sha256 TEXT NOT NULL, captured_at REAL NOT NULL, metadata TEXT NOT NULL,
                PRIMARY KEY (kind, page_key))""")
            connection.commit()

    def save_page(self, kind: str, url: str, html: str, *, meta: dict[str, Any] | None = None,
                  key: str | None = None) -> dict[str, Any]:
        """Atomically save a successful page; key can identify an AJAX page state."""
        if not isinstance(html, str) or not html.strip():
            raise ValueError("checkpoint HTML must be non-empty")
        metadata = json.dumps(meta or {}, ensure_ascii=False, sort_keys=True, allow_nan=False)
        data = html.encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        destination = self.html_directory / (digest + ".html")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.html_directory, suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        captured_at = time.time()
        page_key = url if key is None else key
        with closing(sqlite3.connect(self.db_path)) as connection:
            connection.execute("""INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(kind, page_key) DO UPDATE SET url=excluded.url,
                sha256=excluded.sha256, captured_at=excluded.captured_at, metadata=excluded.metadata""",
                (kind, page_key, url, digest, captured_at, metadata))
            connection.commit()
        return {"kind": kind, "key": page_key, "url": url, "html": html,
                "sha256": digest, "html_path": str(destination), "captured_at": captured_at,
                "meta": json.loads(metadata)}

    def _valid_record(self, row: tuple) -> dict[str, Any] | None:
        kind, page_key, url, digest, captured_at, metadata = row
        try:
            age = time.time() - captured_at
            if not math.isfinite(age) or not 0 <= age <= self.max_age_seconds:
                return None
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                return None
            path = self.html_directory / (digest + ".html")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                return None
            html = data.decode("utf-8")
            meta = json.loads(metadata)
            if not html.strip() or not isinstance(meta, dict):
                return None
        except (OSError, ValueError, TypeError):
            return None
        return {"kind": kind, "key": page_key, "url": url, "html": html, "sha256": digest,
                "html_path": str(path), "captured_at": captured_at, "age_seconds": age, "meta": meta}

    def load_page_record(self, kind: str, url: str, *, key: str | None = None) -> dict[str, Any] | None:
        """Return verified evidence and its original capture time, or a cache miss."""
        try:
            with closing(sqlite3.connect(self.db_path)) as connection:
                row = connection.execute("SELECT * FROM pages WHERE kind=? AND page_key=? AND url=?",
                                         (kind, url if key is None else key, url)).fetchone()
        except sqlite3.DatabaseError:
            return None
        return self._valid_record(row) if row else None

    def load_page(self, kind: str, url: str, *, key: str | None = None) -> str | None:
        record = self.load_page_record(kind, url, key=key)
        return record["html"] if record else None

    def list_pages(self, kind: str) -> list[dict[str, Any]]:
        """List only intact, unexpired pages in this task and plan partition."""
        try:
            with closing(sqlite3.connect(self.db_path)) as connection:
                rows = connection.execute("SELECT * FROM pages WHERE kind=? ORDER BY page_key", (kind,)).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [record for row in rows if (record := self._valid_record(row)) is not None]

    @staticmethod
    def branch_key(branch: dict[str, Any]) -> str:
        return json.dumps(branch, sort_keys=True, separators=(",", ":"), allow_nan=False)

    def save_roster_progress(self, branch: dict[str, Any], url: str, html: str, sequence: int) -> dict:
        """Keep a contiguous prefix; a lost cursor update only causes a safe retry."""
        # Without max_pages the capture has no page cap, so neither does its checkpoint.
        if (type(sequence) is not int or sequence < 0
                or (branch.get("max_pages") is not None and sequence >= branch["max_pages"])):
            raise ValueError("invalid roster sequence")
        previous = [] if sequence == 0 else self.load_roster_progress(branch)
        if len(previous) != sequence:
            raise ValueError("roster checkpoint prefix is incomplete")
        key = self.branch_key(branch)
        record = self.save_page("roster", url, html, key=key + f"|{sequence}",
                                meta={"branch_url": branch["url"], "sequence": sequence})
        refs = [{"url": row["url"], "key": row["key"]} for row in previous]
        refs.append({"url": url, "key": record["key"]})
        self.save_page("roster-progress", branch["url"], html, key=key,
                       meta={"pages": refs, "sequence": sequence})
        return record

    def load_roster_progress(self, branch: dict[str, Any]) -> list[dict]:
        key = self.branch_key(branch)
        cursor = self.load_page_record("roster-progress", branch["url"], key=key)
        if cursor is None:
            return []
        refs = cursor["meta"].get("pages")
        if (not isinstance(refs, list) or not refs
                or (branch.get("max_pages") is not None and len(refs) > branch["max_pages"])
                or type(cursor["meta"].get("sequence")) is not int
                or cursor["meta"]["sequence"] != len(refs) - 1):
            return []
        pages = []
        for sequence, ref in enumerate(refs):
            if (not isinstance(ref, dict) or set(ref) != {"url", "key"}
                    or not isinstance(ref["url"], str) or ref["key"] != key + f"|{sequence}"):
                return []
            record = self.load_page_record("roster", ref["url"], key=ref["key"])
            if (record is None or record["meta"].get("branch_url") != branch["url"]
                    or type(record["meta"].get("sequence")) is not int
                    or record["meta"]["sequence"] != sequence):
                return []
            pages.append(record)
        if cursor["sha256"] != pages[-1]["sha256"]:
            return []
        return pages
