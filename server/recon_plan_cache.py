"""Structure plans reused only after fresh Playwright evidence validates them."""
from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _contract() -> str:
    digest = hashlib.sha256(b"faculty-plan-cache-v3-source-relationship")
    for name in ("recon_discovery.md", "recon_instructions.md", "recon_provided_links.md", "recon_dom_probe.js",
                 'recon_contract.py', 'source_qualification.py', 'page_types.py', 'page_type_rank_grid.py'):
        digest.update((HERE / name).read_bytes())
    return digest.hexdigest()


def _payload_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _validate_structure(plan: dict, entry_url: str, discovery_pages: list[dict]) -> None:
    """One contract for both writes and reads, including full discovery evidence."""
    from server.agent_recon import validate_model_plan, _is_structure_url
    from server.browser_batch_tool import _validate_plan
    if not isinstance(plan, dict):
        raise ValueError("invalid cached plan")
    if plan.get('source_dispositions'):
        raise ValueError('source dispositions require fresh discovery')
    _validate_plan(plan)
    validate_model_plan(plan, school=plan["school"], college=plan["college"])
    if not isinstance(discovery_pages, list) or not discovery_pages or not _is_structure_url(entry_url):
        raise ValueError("complete discovery evidence required")
    observed = set()
    for page in discovery_pages:
        if (not isinstance(page, dict) or set(page) != {"url", "links", "fingerprint"}
                or not _is_structure_url(page["url"]) or not isinstance(page["links"], list)
                or not all(_is_structure_url(url) for url in page["links"])
                or not isinstance(page["fingerprint"], str)
                or re.fullmatch(r"[0-9a-f]{64}", page["fingerprint"]) is None):
            raise ValueError("invalid discovery evidence")
        observed.update([page["url"], *page["links"]])
    if entry_url not in {page["url"] for page in discovery_pages}:
        raise ValueError("entry page evidence required")
    if any(branch["url"] not in observed for branch in plan["branches"]):
        raise ValueError("discovered branch evidence required")
    if any(branch.get("kind", "roster") != "roster" or branch.get("initial_click_selector")
           for branch in plan["branches"]):
        raise ValueError("unsupported cached interaction")


async def navigation_evidence(page: Any) -> dict:
    """Only URLs and a hash persist; labels stay inside the browser process."""
    from server.agent_recon import _is_structure_url
    raw = await page.evaluate("""() => ({url:location.href, links:[...document.querySelectorAll('a[href]')]
        .map(a=>[a.href,(a.textContent||'').trim(),a.getAttribute('rel')||'',a.getAttribute('aria-label')||''])})""")
    tuples = [item for item in raw["links"] if _is_structure_url(item[0])]
    fingerprint = hashlib.sha256(json.dumps(tuples, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    return {"url": raw["url"], "links": sorted({item[0] for item in tuples}), "fingerprint": fingerprint}


def discovery_evidence(session_id: str) -> list[dict]:
    from server.agent_recon import recon_evidence_root
    root = recon_evidence_root() / f"{session_id}-discover"
    pages = {}
    for path in sorted(root.glob("*/*.discovery.json")):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            pages[value["url"]] = value
        except (OSError, ValueError, KeyError):
            continue
    return list(pages.values())


class ReconPlanCache:
    """Permanent structure versions; reading one never proves it is reusable."""

    def __init__(self, directory: str | Path, *, max_age_seconds: float | None = None):
        if max_age_seconds is not None and (not math.isfinite(max_age_seconds) or max_age_seconds <= 0):
            raise ValueError("max_age_seconds must be finite and positive")
        self.directory = Path(directory)
        self.max_age_seconds = max_age_seconds
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.directory / "plans.sqlite3"
        with self._connect() as con:
            con.execute("""CREATE TABLE IF NOT EXISTS plan_versions (
                version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                cache_key TEXT NOT NULL, saved_at REAL NOT NULL, last_seen_at REAL NOT NULL,
                contract TEXT NOT NULL, sha256 TEXT NOT NULL,
                structure_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
                UNIQUE(cache_key, sha256), UNIQUE(cache_key, structure_sha256))""")
            con.execute("""CREATE INDEX IF NOT EXISTS plan_lookup
                ON plan_versions(cache_key, last_seen_at DESC, version_id DESC)""")
            con.execute("""CREATE TABLE IF NOT EXISTS validation_events (
                validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                version_id INTEGER NOT NULL REFERENCES plan_versions(version_id),
                checked_at REAL NOT NULL, valid INTEGER NOT NULL CHECK(valid IN (0,1)),
                reason TEXT NOT NULL)""")
        self.migrate_legacy()

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            with con:
                yield con
        finally:
            con.close()

    @staticmethod
    def _key(school: str, college: str, url: str) -> str:
        return hashlib.sha256(json.dumps([school, college, url], ensure_ascii=False).encode()).hexdigest()

    def _path(self, school: str, college: str, url: str) -> Path:
        """Original JSON location, retained only for migration compatibility."""
        return self.directory / f"{self._key(school, college, url)}.json"

    @staticmethod
    def _checked_entry(raw: str) -> dict | None:
        from server.agent_recon import ReconError
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"contract", "saved_at", "entry_url", "plan", "discovery_pages", "sha256"}:
                return None
            if not isinstance(value["sha256"], str) or value["sha256"] != _payload_digest({k: v for k, v in value.items() if k != "sha256"}):
                return None
            if (not isinstance(value["contract"], str) or re.fullmatch(r"[0-9a-f]{64}", value["contract"]) is None
                    or type(value["saved_at"]) not in (int, float)
                    or not 0 <= value["saved_at"] <= 2 ** 63 - 1):
                return None
            _validate_structure(value["plan"], value["entry_url"], value["discovery_pages"])
            return value
        except (ValueError, KeyError, TypeError, AttributeError, ReconError):
            return None

    def _insert(self, value: dict) -> int:
        key = self._key(value["plan"]["school"], value["plan"]["college"], value["entry_url"])
        structure_digest = _payload_digest({k: v for k, v in value.items() if k not in {"saved_at", "sha256"}})
        with self._connect() as con:
            result = con.execute("""INSERT OR IGNORE INTO plan_versions
                (cache_key,saved_at,last_seen_at,contract,sha256,structure_sha256,payload_json)
                VALUES (?,?,?,?,?,?,?)""",
                (key, value["saved_at"], value["saved_at"], value["contract"], value["sha256"], structure_digest,
                 json.dumps(value, ensure_ascii=False, allow_nan=False)))
            inserted = result.rowcount
            con.execute("""UPDATE plan_versions SET last_seen_at=?
                WHERE cache_key=? AND structure_sha256=? AND last_seen_at<?""",
                (value["saved_at"], key, structure_digest, value["saved_at"]))
            return inserted

    def migrate_legacy(self) -> int:
        """Import valid old JSON, without deleting it or refreshing its age."""
        imported = 0
        for path in self.directory.glob("*.json"):
            if re.fullmatch(r"[0-9a-f]{64}\.json", path.name) is None:
                continue
            try:
                value = self._checked_entry(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                continue
            if value is None:
                continue
            if path != self._path(value["plan"]["school"], value["plan"]["college"], value["entry_url"]):
                continue
            imported += self._insert(value)
        return imported

    def put(self, plan: dict, entry_url: str, discovery_pages: list[dict]) -> None:
        _validate_structure(plan, entry_url, discovery_pages)
        payload = {"contract": _contract(), "saved_at": time.time(), "entry_url": entry_url,
                   "plan": plan, "discovery_pages": discovery_pages}
        payload["sha256"] = _payload_digest(payload)
        self._insert(payload)

    def get(self, school: str, college: str, entry_url: str) -> dict | None:
        with self._connect() as con:
            row = con.execute("""SELECT payload_json,saved_at,contract,sha256,structure_sha256 FROM plan_versions
                WHERE cache_key=? ORDER BY last_seen_at DESC,version_id DESC LIMIT 1""",
                (self._key(school, college, entry_url),)).fetchone()
        if row is None:
            return None
        value = self._checked_entry(row[0])
        if value is None or (value["saved_at"], value["contract"], value["sha256"]) != row[1:4]:
            return None
        if _payload_digest({k: v for k, v in value.items() if k not in {"saved_at", "sha256"}}) != row[4]:
            return None
        age = time.time() - value["saved_at"]
        if (value["contract"] != _contract() or not math.isfinite(age) or age < 0
                or (self.max_age_seconds is not None and age > self.max_age_seconds)):
            return None
        if (value["entry_url"] != entry_url or value["plan"]["school"] != school
                or value["plan"]["college"] != college):
            return None
        return value

    def record_validation(self, entry: dict, proof: dict) -> None:
        """Persist status only; tool events, page content and issues stay outside."""
        key = self._key(entry["plan"]["school"], entry["plan"]["college"], entry["entry_url"])
        valid = proof.get("valid") is True
        reason = "verified" if valid else proof.get("reason", "verification_failed")
        if not isinstance(reason, str) or re.fullmatch(r"[a-z_]{1,80}", reason) is None:
            reason = "verification_failed"
        with self._connect() as con:
            result = con.execute("""INSERT INTO validation_events(version_id,checked_at,valid,reason)
                SELECT version_id,?,?,? FROM plan_versions WHERE cache_key=? AND sha256=?""",
                (time.time(), int(valid), reason, key, entry["sha256"]))
            if result.rowcount != 1:
                raise ValueError("validation requires a saved plan version")


async def validate_cached_plan(entry: dict, context: Any, evidence_dir: str | Path, *,
                               verify_discovery: bool = True, expected_template: str | None = None) -> dict:
    """Revisit all discovery pages, then verify each roster and a real next page."""
    from server.agent_recon import selector_evidence_issues, validate_model_plan
    from server.browser_batch_tool import _validate_plan, _wait_for_change
    from server.recon_browser_mcp import BrowserScout
    page = None
    events = []
    observations = []
    verified_discovery = False

    def outcome(**values) -> dict:
        return {"model_calls": 0, "events": events, "verified_discovery": verified_discovery,
                "evidence_root": str(Path(evidence_dir)),
                "browser_tool_calls": sum(e.get("type") == "tool/call" for e in events), **values}

    async def tool(name: str, arguments: dict) -> dict:
        cid = str(len(events))
        events.append({"type": "tool/call", "data": {"callId": cid, "name": "mcp__playwright__" + name,
                                                      "arguments": json.dumps(arguments)}})
        result = await scout.call(name, arguments)
        events.append({"type": "tool/result", "data": {"message": {"content": [{"toolCallId": cid, **result}]}}})
        return json.loads(result["content"][0]["text"])

    try:
        plan = entry["plan"]
        _validate_plan(plan)
        validate_model_plan(plan, school=plan["school"], college=plan["college"])
        if verify_discovery:
            if not entry.get("discovery_pages"):
                return outcome(valid=False, reason="discovery_evidence_missing")
            entry_url = entry.get("entry_url", entry["discovery_pages"][0]["url"])
            _validate_structure(plan, entry_url, entry["discovery_pages"])
        page = await context.new_page()
        from server.browser_context import IsolatedPage
        if not isinstance(page, IsolatedPage):
            page = IsolatedPage(page, getattr(context, "raw_context", context))
        scout = BrowserScout(page, Path(evidence_dir), "branch", {'school': plan['school'], 'college': plan['college']})
        for prior in entry.get("discovery_pages", []) if verify_discovery else []:
            response = await page.goto(prior["url"], wait_until="domcontentloaded", timeout=30000)
            if response and response.status >= 400:
                return outcome(valid=False, reason="discovery_navigation_failed")
            current = await navigation_evidence(page)
            observations.append(current)
            await scout.snapshot()
            if current != prior:
                return outcome(valid=False, reason="discovery_changed")
        verified_discovery = verify_discovery
        for branch in plan["branches"]:
            if branch.get("kind", "roster") != "roster" or branch.get("initial_click_selector"):
                return outcome(valid=False, reason="unsupported_branch_shape")
            await tool("browser_navigate", {"url": branch["url"]})
            from server.source_qualification import SOURCE_FIELDS, source_unit_selector
            await tool('browser_verify_source', {**{key: branch[key] for key in SOURCE_FIELDS},
                                                  'unit_selector': source_unit_selector(branch)})
            if expected_template:
                from server.recon_templates import structural_fingerprint
                if structural_fingerprint(await page.content()) != expected_template:
                    return outcome(valid=False, reason="template_changed")
            args = {"card_selector": branch["card_selector"]}
            for selector in ("empty_state_selector", "roster_container_selector"):
                if selector in branch:
                    args[selector] = branch[selector]
            if branch.get("next_selector"):
                args["next_selector"] = branch["next_selector"]
            first = await tool("browser_verify", args)
            if expected_template and (not first.get("cardCount") or not first.get("linkedSamples")
                                      or not branch.get("profile_content_selector")):
                return outcome(valid=False, reason="unsupported_template_roster")
            if branch.get("next_selector") and first.get("nextCount") == 1 and not first.get("nextDisabled"):
                before = await page.content()
                await tool("browser_click", {"selector": branch["next_selector"]})
                if await _wait_for_change(page, before, page.url,
                                          unit_selector=branch["card_selector"]) is None:
                    return outcome(valid=False, reason="pagination_unchanged")
                await tool("browser_verify", {"card_selector": branch["card_selector"]})
            if branch.get("profile_content_selector"):
                profile_args = {key: branch[key] for key in
                                ("profile_content_selector", "profile_content_start_selector") if key in branch}
                samples = list(dict.fromkeys(url for url in first.get("linkedSamples", first.get("profileSamples", []))
                               if isinstance(url, str) and url.startswith(("https://", "http://"))
                               and url != first.get("url")))[:2]
                for profile_url in samples:
                    await tool("browser_navigate", {"url": profile_url})
                    await tool("browser_verify_profile", profile_args)
            issues = selector_evidence_issues({**plan, "branches": [branch]}, events)
            if issues:
                return outcome(valid=False, reason="branch_verification_failed", issues=issues)
        return outcome(valid=True, discovery_pages=observations)
    except Exception as exc:
        return outcome(valid=False, reason="browser_verification_failed", error_type=type(exc).__name__)
    finally:
        if page is not None:
            await page.close()
