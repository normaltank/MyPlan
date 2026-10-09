"""人工复核生僻姓氏的人名：待复核条目、决定文件与决定的应用。

生僻姓（Opus/rare_surnames.txt）里有很多常用字，出现在人名位置的词可能根本
不是人名（宿舍、学院），所以既不自动采信，也不丢弃，交给人确认。决定只对
完全相同的证据（名录页、主页链接、名字、卡片原文）生效，页面变了就要重新确认。
"""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any

from server import review_store

STATUS = "rare_surname_needs_review"
DECISIONS_FILE = "name_reviews.json"
VERDICTS = ("person", "not_person")


def _digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def review_id(roster_url: str, profile_url: str, name: str, card_text: str) -> str:
    source = json.dumps([roster_url, profile_url, name, _digest(card_text)],
                        ensure_ascii=False, separators=(",", ":"))
    return _digest(source)


def pending_item(roster_url: str, candidate: Any) -> dict[str, Any]:
    """The unresolved audit entry for one rare-surname name awaiting a person."""
    card = candidate.card_context or ""
    return {"url": roster_url, "status": STATUS,
            "id": review_id(roster_url, candidate.url, candidate.name, card),
            "name": candidate.name, "title": candidate.title,
            "profile_url": candidate.url, "card_text": card,
            **({"reason": candidate.name_review_reason}
               if getattr(candidate, 'name_review_reason', '') else {})}


def load_decisions(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    try:
        return {row["id"]: row for row in review_store.rows(path)
                if row.get("verdict") in VERDICTS
                and (row["verdict"] == "not_person" or _valid_name(row.get("verified_name")))}
    except (KeyError, TypeError, AttributeError):
        return {}


def _valid_name(name: Any) -> bool:
    return (isinstance(name, str) and bool(name.strip()) and len(name) <= 40
            and "\n" not in name and "\r" not in name)


def save_decision(path: str | Path, item: dict[str, Any], verdict: str,
                  verified_name: str = "") -> dict[str, Any]:
    if verdict not in VERDICTS:
        raise ValueError("verdict must be person or not_person")
    if verdict == "person" and not _valid_name(verified_name):
        raise ValueError("a confirmed name must be one nonempty line of at most 40 characters")
    decision = {key: item[key] for key in ("id", "url", "profile_url", "name")}
    decision.update(verdict=verdict, reviewed_at=time.time())
    if verdict == "person":
        decision["verified_name"] = verified_name.strip()
    review_store.put(path, decision)
    return decision


def collect_items(audit_path: str | Path) -> list[dict[str, Any]]:
    """Pending rare-surname names recorded in one harvest audit."""
    audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    items, seen = [], set()
    for item in audit.get("unresolved", []):
        if item.get("status") != STATUS or item.get("id") in seen:
            continue
        if item.get("id") != review_id(item.get("url", ""), item.get("profile_url", ""),
                                       item.get("name", ""), item.get("card_text", "")):
            raise ValueError("name review item does not match its own evidence")
        seen.add(item["id"])
        items.append(item)
    return items


def review_complete(audit_path: str | Path, items: list[dict[str, Any]]) -> bool:
    decisions = load_decisions(Path(audit_path).parent / DECISIONS_FILE)
    return all(item["id"] in decisions for item in items)
