"""Deterministic L1 wrapper cache and fail-closed runtime.

Only validated XPath programs are executed here. Compilation belongs to L3.
The structural route uses tag topology, not page text or keyword lists.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import statistics
import sys
import threading
import time
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Mapping
from urllib.parse import urljoin, urlparse

from lxml import etree, html as lxml_html

_OPUS = str(Path(__file__).resolve().parent.parent / "Opus")
if _OPUS not in sys.path:
    sys.path.insert(0, _OPUS)
from iso_mining import url_template  # noqa: E402 — shared L2 URL normalizer

if TYPE_CHECKING:
    from server.agent_queue import AgentTaskQueue

RuntimeStatus = Literal["hit", "miss", "drift"]
_NON_RENDERED = frozenset({"script", "style", "noscript", "template", "svg", "iframe"})


def _route_key(page_url: str) -> str:
    parsed = urlparse(page_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("page_url must be an absolute HTTP(S) URL")
    return json.dumps([parsed.hostname.lower(), url_template(page_url)], ensure_ascii=False, separators=(",", ":"))


def _skeleton_hash(doc: etree._Element) -> str:
    """Merkle-style tag topology; repeated sibling signatures collapse.

    Counts and text vary across directory pages, while a changed container
    layout changes this signature. Attributes are left to the compiled XPath.
    """
    body = doc.find("body")
    root = body if body is not None else doc
    signatures: dict[etree._Element, bytes] = {}
    stack: list[tuple[etree._Element, bool]] = [(root, False)]
    while stack:
        node, ready = stack.pop()
        if not ready:
            stack.append((node, True))
            stack.extend((child, False) for child in reversed(node)
                         if isinstance(child.tag, str) and child.tag not in _NON_RENDERED)
            continue
        digest = hashlib.blake2b(digest_size=16)
        digest.update(str(node.tag).encode("utf-8"))
        previous: bytes | None = None
        for child in node:
            if not isinstance(child.tag, str) or child.tag in _NON_RENDERED:
                continue
            child_sig = signatures[child]
            if child_sig != previous:
                digest.update(child_sig)
            previous = child_sig
        signatures[node] = digest.digest()
    return signatures[root].hex()


def cluster_key_for(html: str, page_url: str) -> str:
    """Stable JSON tuple: (domain, Opus URL template, DOM skeleton hash)."""
    doc = lxml_html.document_fromstring(html)
    route = json.loads(_route_key(page_url))
    return json.dumps([*route, _skeleton_hash(doc)], ensure_ascii=False, separators=(",", ":"))


def _key_route(cluster_key: str) -> str:
    parts = json.loads(cluster_key)
    if not isinstance(parts, list) or len(parts) != 3 or not all(isinstance(x, str) for x in parts):
        raise ValueError("cluster_key must be the three-part key from cluster_key_for")
    return json.dumps(parts[:2], ensure_ascii=False, separators=(",", ":"))


class SQLiteWrapperRegistry:
    """SQLite-backed immutable wrapper versions with a persistent circuit flag."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            try:
                from server.agent_queue import default_db_path
            except ImportError:  # flat layout (server/ on sys.path)
                from agent_queue import default_db_path
            db_path = default_db_path().with_name("wrappers.db")  # beside the queue it tickets
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA synchronous=NORMAL")
            con.execute("""CREATE TABLE IF NOT EXISTS wrappers (
                cluster_key TEXT PRIMARY KEY, route_key TEXT NOT NULL,
                version INTEGER NOT NULL, spec_json TEXT NOT NULL,
                golden_json TEXT NOT NULL, hit_count INTEGER NOT NULL DEFAULT 0,
                last_success_at REAL, disabled_at REAL)""")
            con.execute("CREATE INDEX IF NOT EXISTS idx_wrappers_route ON wrappers(route_key)")

    def _connect(self) -> sqlite3.Connection:
        con = getattr(self._local, "connection", None)
        if con is None:
            con = sqlite3.connect(self.db_path, timeout=5)
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA busy_timeout=5000")
            self._local.connection = con
        return con

    def close(self) -> None:
        """Release this thread's connection, useful before deleting a DB on Windows."""
        con = getattr(self._local, "connection", None)
        if con is not None:
            con.close()
            self._local.connection = None

    def __del__(self) -> None:
        if hasattr(self, "_local"):
            self.close()

    def get(self, cluster_key: str) -> dict[str, Any] | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM wrappers WHERE cluster_key=?", (cluster_key,)).fetchone()
        if row is None:
            return None
        entry = dict(row)
        entry["spec"] = json.loads(entry.pop("spec_json"))
        entry["golden_samples"] = json.loads(entry.pop("golden_json"))
        return entry

    def put(self, cluster_key: str, spec: Mapping[str, Any] | Any,
            golden_samples: Mapping[str, Any] | list[Any]) -> None:
        """Atomically install or recompile a wrapper, resetting its circuit."""
        _key_route(cluster_key)
        if is_dataclass(spec) and not isinstance(spec, type):
            spec = asdict(spec)
        if not isinstance(spec, Mapping) or not isinstance(spec.get("record_xpath"), str):
            raise ValueError("spec must contain record_xpath")
        for field in ("record_xpath", "name_xpath", "url_xpath"):
            value = spec.get(field)
            if value:
                etree.XPath(str(value))  # Reject invalid programs before publication.
        if isinstance(golden_samples, list):
            golden_samples = {"sample": golden_samples}
        if not isinstance(golden_samples, Mapping) or not golden_samples:
            raise ValueError("golden_samples must contain at least one page")
        packed_spec = json.dumps(dict(spec), ensure_ascii=False, separators=(",", ":"))
        packed_golden = json.dumps(golden_samples, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as con:
            con.execute("""INSERT INTO wrappers
                (cluster_key, route_key, version, spec_json, golden_json)
                VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(cluster_key) DO UPDATE SET
                    version=wrappers.version+1, spec_json=excluded.spec_json,
                    golden_json=excluded.golden_json, hit_count=0,
                    last_success_at=NULL, disabled_at=NULL""",
                (cluster_key, _key_route(cluster_key), packed_spec, packed_golden))

    def keys_for_route(self, route_key: str) -> list[str]:
        with self._connect() as con:
            rows = con.execute("SELECT cluster_key FROM wrappers WHERE route_key=?", (route_key,)).fetchall()
        return [row[0] for row in rows]

    def trip(self, cluster_key: str) -> None:
        with self._connect() as con:
            con.execute("UPDATE wrappers SET disabled_at=COALESCE(disabled_at, ?) WHERE cluster_key=?",
                        (time.time(), cluster_key))

    def record_success(self, cluster_key: str) -> None:
        with self._connect() as con:
            con.execute("""UPDATE wrappers SET hit_count=hit_count+1,
                        last_success_at=? WHERE cluster_key=? AND disabled_at IS NULL""",
                        (time.time(), cluster_key))


def _first_text(result: Any) -> str:
    if isinstance(result, list):
        result = result[0] if result else None
    if result is None:
        return ""
    if hasattr(result, "text_content"):
        return result.text_content().strip()
    return str(result).strip()


def _golden_count(golden: Mapping[str, Any]) -> float:
    counts = []
    for sample in golden.values():
        if isinstance(sample, list):
            counts.append(len(sample))
        elif isinstance(sample, Mapping) and isinstance(sample.get("count"), int):
            counts.append(sample["count"])
    return float(statistics.median(counts)) if counts else 0.0


class L1RuntimeExecutor:
    """Run one cached XPath wrapper, returning a status that routes fallback."""

    def __init__(self, registry: SQLiteWrapperRegistry, queue: AgentTaskQueue | None = None) -> None:
        self.registry = registry
        self.queue = queue
        self._compiled: dict[tuple[str, int], tuple[etree.XPath, etree.XPath, etree.XPath | None]] = {}

    def _drift(self, key: str, page_url: str, reason: str) -> tuple[list[dict[str, str | None]], RuntimeStatus]:
        self.registry.trip(key)
        if self.queue is not None:
            self.queue.create_compile_ticket(key, f"L1 drift at {page_url}: {reason}. Recompile and validate against golden pages.")
        return [], "drift"

    def execute(self, html: str, page_url: str) -> tuple[list[dict[str, str | None]], RuntimeStatus]:
        try:
            route = _route_key(page_url)
        except (ValueError, Exception):
            return [], "miss"
        try:
            doc = lxml_html.document_fromstring(html)
        except etree.ParserError:
            prior = self.registry.keys_for_route(route)
            return self._drift(prior[0], page_url, "empty or invalid HTML") if prior else ([], "miss")
        key = json.dumps([*json.loads(route), _skeleton_hash(doc)], ensure_ascii=False, separators=(",", ":"))
        entry = self.registry.get(key)
        if entry is None:
            prior = self.registry.keys_for_route(route)
            return self._drift(prior[0], page_url, "DOM skeleton changed") if prior else ([], "miss")
        if entry["disabled_at"] is not None:
            return [], "drift"
        spec = entry["spec"]
        if spec.get("mode", "dom") != "dom":
            return self._drift(key, page_url, "wrapper mode is not DOM")
        try:
            cache_key = (key, entry["version"])
            paths = self._compiled.get(cache_key)
            if paths is None:
                paths = (etree.XPath(spec["record_xpath"]),
                         etree.XPath(spec.get("name_xpath") or "."),
                         etree.XPath(spec["url_xpath"]) if spec.get("url_xpath") else None)
                self._compiled[cache_key] = paths
            record_path, name_path, url_path = paths
            records = []
            for node in record_path(doc):
                if not isinstance(node, etree._Element):
                    return self._drift(key, page_url, "record XPath yielded non-elements")
                name = _first_text(name_path(node))
                raw_url = _first_text(url_path(node)) if url_path is not None else ""
                absolute_url = urljoin(page_url, raw_url) if raw_url else None
                if absolute_url is not None:
                    parsed_url = urlparse(absolute_url)
                    if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
                        return self._drift(key, page_url, "profile URL is not HTTP(S)")
                records.append({"name": name, "url": absolute_url})
        except (etree.XPathError, KeyError, TypeError, ValueError) as exc:
            return self._drift(key, page_url, f"XPath execution failed: {exc}")
        if not records:
            return self._drift(key, page_url, "zero records")
        if any(not row["name"] for row in records):
            return self._drift(key, page_url, "empty scholar name")
        mean_name_length = sum(len(row["name"]) for row in records) / len(records)
        if not 2 <= mean_name_length <= 6:
            return self._drift(key, page_url, "abnormal mean scholar name length")
        baseline = _golden_count(entry["golden_samples"])
        if baseline > 0 and len(records) < baseline:
            return self._drift(key, page_url, f"record count fell below golden baseline ({len(records)} < {baseline})")
        self.registry.record_success(key)
        return records, "hit"
