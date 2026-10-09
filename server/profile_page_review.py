"""人工复核“个人页上找不到本人”：名录链接打开的页面不写这位老师的名字。

集美大学师范学院（badr 10-09）：赵红霞的名录链接打开是 CMS 的“您无权访问此页面”。
程序不能自己认定这页是失效页还是别人的页，也不能因此丢掉名录上的老师，所以交给人：

- ``roster_only``：链接不是本人页，按名录收录，主页记名录页，简介留空；
- ``not_teacher``：不是本院教师，不收。

决定绑定个人页网址、名录姓名和页面可见文字的摘要；页面文字变了要重新判。重新采集时，
判过的老师不再用这一页，页面若没有别人在用，也不再拿它的正文证明挡交付。
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path, PureWindowsPath
import re
import time
from typing import Any

from bs4 import BeautifulSoup

from server import review_store

STATUS = "profile_identity_not_found"
DECISIONS_FILE = "profile_page_reviews.json"
VERDICTS = ("roster_only", "not_teacher")
PAGE_TEXT_LIMIT = 600


def page_text(html: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True)).strip()


def page_digest(html: str) -> str:
    """Visible text only: a visit counter in the markup does not void a decision."""
    return sha256(re.sub(r"\s+", "", page_text(html)).encode("utf-8")).hexdigest()


def review_id(profile_url: str, name: str, digest: str) -> str:
    return sha256(json.dumps([profile_url, name, digest], ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def load_decisions(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        return {row["id"]: row for row in review_store.rows(path)
                if row.get("verdict") in VERDICTS
                and (row["verdict"] == "not_teacher" or str(row.get("roster_url", "")).startswith(("http://", "https://")))}
    except (KeyError, TypeError, AttributeError):
        return {}


def save_decision(path: str | Path, item: dict[str, Any], verdict: str) -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError("verdict must be roster_only or not_teacher")
    if verdict == "roster_only" and not item.get("roster_url"):
        raise ValueError("no roster page to collect this teacher from")
    decision = {key: item.get(key, "") for key in ("id", "url", "name", "page_sha256", "roster_url")}
    decision.update(verdict=verdict, reviewed_at=time.time())
    review_store.put(path, decision)
    return decision


def _snapshot(audit_path: Path, audit: dict, url: str) -> str | None:
    """The saved capture of the page, read from beside the audit only."""
    root = audit_path.parent.resolve()
    for snapshot in audit.get("snapshots", []):
        if snapshot.get("kind") != "profile" or snapshot.get("url") != url:
            continue
        # Paths may be written on Windows; only the last two parts locate the file here.
        parts = PureWindowsPath(str(snapshot.get("path", ""))).parts
        if len(parts) < 2:
            continue
        local = (root / parts[-2] / parts[-1]).resolve()
        if local.is_relative_to(root) and local.is_file():
            return local.read_text(encoding="utf-8")
    return None


def collect_items(audit_path: str | Path) -> list[dict[str, Any]]:
    """One item per (page, name) the audit could not find on its page, with the saved page."""
    path = Path(audit_path).resolve()
    audit = json.loads(path.read_text(encoding="utf-8"))
    sources = audit.get("coverage", {}).get("profile_sources", {})
    contexts = {(row.get("profile_url"), row.get("name")): row.get("roster_context", "")
                for row in audit.get("eligibility", [])}
    items, seen = [], set()
    for issue in audit.get("unresolved", []):
        url, name = issue.get("url"), issue.get("name")
        if issue.get("status") != STATUS or not url or not name or (url, name) in seen:
            continue
        seen.add((url, name))
        html = _snapshot(path, audit, url)
        if html is None:
            continue
        digest = page_digest(html)
        rosters = sorted(sources.get(url) or [])
        items.append({"id": review_id(url, name, digest), "url": url, "name": name,
                      "page_sha256": digest, "page_text": page_text(html)[:PAGE_TEXT_LIMIT],
                      "roster_url": rosters[0] if rosters else "",
                      "card_text": contexts.get((url, name), "")})
    return items


def review_complete(audit_path: str | Path, items: list[dict[str, Any]]) -> bool:
    audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    flagged = {(issue.get("url"), issue.get("name")) for issue in audit.get("unresolved", [])
               if issue.get("status") == STATUS}
    decisions = load_decisions(Path(audit_path).parent / DECISIONS_FILE)
    # A flagged name without its saved page has nothing to decide on.
    return flagged <= {(item["url"], item["name"]) for item in items} and all(
        item["id"] in decisions for item in items)


def apply_decisions(candidates: list, candidate_sources: dict, profile_html: dict[str, str],
                    decisions: dict[str, dict[str, Any]]) -> tuple[list, dict, set[str], list[dict]]:
    """Settle reviewed (page, name) pairs on this capture.

    Returns the candidates and their sources, the page URLs no remaining
    candidate uses (the caller stops proving them), and the applied decisions.
    """
    from dataclasses import replace

    kept, applied = [], []
    sources = dict(candidate_sources)
    for candidate in candidates:
        html = profile_html.get(candidate.url)
        decision = decisions.get(review_id(candidate.url, candidate.name, page_digest(html))) if html else None
        if decision is None:
            kept.append(candidate)
            continue
        applied.append({key: decision.get(key) for key in ("id", "url", "name", "verdict", "roster_url")})
        source = sources.pop((candidate.name, candidate.url), None)
        if decision["verdict"] == "roster_only":
            moved = replace(candidate, url=decision["roster_url"])
            kept.append(moved)
            if source is not None:
                sources.setdefault((moved.name, moved.url), set()).update(source)
    released = {item["url"] for item in applied} - {candidate.url for candidate in kept}
    return kept, sources, released, applied
