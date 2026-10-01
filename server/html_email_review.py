"""Human e-mail decisions for ordinary HTML ownership conflicts.

The extractor blocks a teacher whose captured page or card shows addresses it
cannot attribute. This module keeps that block until a person picks one of the
observed addresses, or confirms an empty field where every observed address
already carries evidence of belonging to someone else. A decision is bound to
the task, the teacher observation, the exact candidate set and the captured
page/card hashes; any change sends the item back to review. No model is used.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

from bs4 import BeautifulSoup

STATUSES = frozenset({"ambiguous_email_assignment", "ambiguous_shared_email", "ambiguous_surname_email",
                      "conflicting_card_email", "mailto_address_conflict"})
DECISIONS_FILE = "html_email_reviews.json"
VERDICTS = ("select", "none")
_CONTEXT_CHARS = 60
_CONTEXT_LIMIT = 3


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def item_id(item: dict) -> str:
    return digest({key: value for key, value in item.items() if key != "id"})


def task_id_for(attempt_dir: str | Path) -> str:
    """Tasks run in ``<output>/tasks/<task_id>/attempt-N``; anything else has no task."""
    path = Path(attempt_dir).resolve()
    return path.parent.name if path.parent.parent.name == "tasks" else ""


def _issue_addresses(issue: dict) -> list[str]:
    values = list(issue.get("matches") or [])
    if issue.get("email"):
        values.append(issue["email"])
    return [value for value in values if isinstance(value, str) and "@" in value]


def _page_text(html: str) -> str:
    return " ".join(BeautifulSoup(html or "", "html.parser").get_text(" ", strip=True).split())


def _contexts(text: str, address: str) -> list[str]:
    lowered, key = text.lower(), address.lower()
    found, start = [], lowered.find(key)
    while start >= 0 and len(found) < _CONTEXT_LIMIT:
        found.append(text[max(0, start - _CONTEXT_CHARS):start + len(key) + _CONTEXT_CHARS])
        start = lowered.find(key, start + len(key))
    return found


def build_items(issues, records, candidates, page_html) -> list[dict]:
    """One review item per teacher observation whose e-mail is blocked by an HTML conflict."""
    from server.faculty_extractor import find_emails, is_personal_email_for_scholar

    grouped: dict[tuple[str, str], list[dict]] = {}
    for issue in issues:
        if issue.get("status") in STATUSES and issue.get("姓名") and "主页" in issue:
            grouped.setdefault((issue["姓名"], issue["主页"]), []).append(issue)
    if not grouped:
        return []
    url_counts = Counter(cand.url for cand in candidates)
    page_texts = {url: _page_text(html) for url, html in page_html.items() if html}
    roster_names = {cand.name for cand in candidates if cand.name and len(cand.name) >= 2}

    def names_others(text: str, name: str) -> bool:
        compact = text.replace(" ", "")
        return any(other != name and other not in name and other in compact for other in roster_names)

    holders: dict[str, set] = {}
    for cand in candidates:
        texts = [cand.card_context or ""]
        # A linked page is the teacher's own scope only when it is theirs alone:
        # a group listing that names colleagues proves nothing about them.
        page = page_texts.get(cand.url, "")
        if url_counts[cand.url] == 1 and not names_others(page, cand.name):
            texts.append(page)
        for text in texts:
            for address in find_emails(text):
                holders.setdefault(address.lower(), set()).add((cand.name, cand.url))
    items = []
    for (name, url), owned_issues in grouped.items():
        record = next((row for row in records if row.get("姓名") == name and row.get("主页") == url), None)
        cand = next((c for c in candidates if c.name == name and c.url == url), None)
        if record is None or cand is None:
            continue  # Without the observed row and card there is nothing a person can bind to.
        current = (record.get("邮箱") or "").strip()
        addresses = {}
        for address in [*(a for issue in owned_issues for a in _issue_addresses(issue)), current]:
            if address:
                addresses.setdefault(address.lower(), address)
        shared = {issue["email"].lower() for issue in owned_issues
                  if issue.get("status") == "ambiguous_shared_email" and issue.get("email")}
        others_assigned = {(row.get("邮箱") or "").strip().lower() for row in records
                           if row is not record and (row.get("邮箱") or "").strip()}
        options = []
        for key in sorted(addresses):
            address = addresses[key]
            own = is_personal_email_for_scholar(address, name)
            # As in the extractor, a colleague's spelling counts only for a colleague
            # this teacher's own card or page actually names.
            scope = ((cand.card_context or "") + " " + page_texts.get(url, "")).replace(" ", "")
            other = sorted(other_name for other_name in roster_names if other_name != name
                           and other_name not in name and other_name in scope
                           and is_personal_email_for_scholar(address, other_name))
            reasons = []
            if key in shared:
                reasons.append("shared_contact")
            if holders.get(key, set()) - {(name, url)}:
                reasons.append("in_other_teacher_scope")
            if key in others_assigned:
                reasons.append("assigned_to_other_record")
            if other and not own:
                reasons.append("spells_other_teacher")
            elif other:
                reasons.append("also_spells_other_teacher")
            selectable = not ({"shared_contact", "in_other_teacher_scope", "assigned_to_other_record",
                               "spells_other_teacher"} & set(reasons))
            options.append({"email": address, "selectable": selectable, "reasons": reasons,
                            "spells_teacher": own, "spells_others": other,
                            "card_context": _contexts(cand.card_context or "", address),
                            "page_context": _contexts(page_texts.get(url, ""), address)})
        if not options:
            continue
        item = {"name": name, "url": url,
                "statuses": sorted({issue["status"] for issue in owned_issues}),
                "issues": owned_issues, "current_email": current,
                "card_text": cand.card_context or "",
                "card_sha256": hashlib.sha256((cand.card_context or "").encode("utf-8")).hexdigest(),
                "source_sha256": (hashlib.sha256(page_html[url].encode("utf-8")).hexdigest()
                                  if page_html.get(url) else ""),
                "shared_page": url_counts[url] > 1,
                "candidates": options,
                # An empty field is only a choice when every address is shown to be someone else's.
                "none_allowed": all(option["reasons"] for option in options)}
        item["id"] = item_id(item)
        items.append(item)
    return items


def load_decisions(path) -> dict:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema") != 1:
            return {}
        return {row["id"]: row for row in payload["decisions"]}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def _choice(item: dict, verdict: str, selected_email: str) -> str:
    if verdict == "select":
        option = next((o for o in item["candidates"] if o["email"] == selected_email), None)
        if option is None or not option["selectable"]:
            raise ValueError("selected address is not an observed, attributable candidate")
        return option["email"]
    if verdict == "none" and item["none_allowed"]:
        return ""
    raise ValueError("an empty e-mail needs evidence that every observed address is someone else's")


def save_decision(path, item: dict, verdict: str, selected_email: str = "", *, task_id: str, reviewed_by: int) -> dict:
    if (type(reviewed_by) is not int or reviewed_by <= 0 or not task_id
            or item.get("id") != item_id(item)):
        raise ValueError("verified reviewer, task and exact evidence required")
    email = _choice(item, verdict, selected_email)
    decision = {"id": item["id"], "task_id": task_id, "name": item["name"], "url": item["url"],
                "candidates": [o["email"] for o in item["candidates"]],
                "verdict": verdict, "email": email, "reviewed_by": reviewed_by, "reviewed_at": time.time()}
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    decisions = load_decisions(path)
    decisions[item["id"]] = decision
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False) as out:
            temporary = Path(out.name)
            json.dump({"schema": 1, "decisions": list(decisions.values())}, out, ensure_ascii=False, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return decision


def valid_decision(item: dict, decision: dict, task_id: str) -> str | None:
    """The decided e-mail ('' for a confirmed empty field), or None when it does not apply."""
    try:
        if (not task_id or decision["task_id"] != task_id or decision["id"] != item["id"]
                or item_id(item) != item["id"] or decision["name"] != item["name"]
                or decision["url"] != item["url"]
                or decision["candidates"] != [o["email"] for o in item["candidates"]]
                or type(decision["reviewed_by"]) is not int or decision["reviewed_by"] <= 0
                or not math.isfinite(decision["reviewed_at"]) or decision["reviewed_at"] <= 0):
            return None
        email = _choice(item, decision["verdict"], decision.get("email", ""))
        return email if email == decision["email"] else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def apply_decisions(unresolved, records, candidates, page_html, out_dir):
    """Settle reviewed conflicts in place; everything else, and stale decisions, stay unresolved."""
    items = build_items(unresolved, records, candidates, page_html)
    task_id = task_id_for(out_dir)
    decisions = load_decisions(Path(out_dir) / DECISIONS_FILE)
    settled, pending, certificates = set(), [], []
    for item in items:
        decision = decisions.get(item["id"], {})
        email = valid_decision(item, decision, task_id)
        if email is None:
            pending.append(item)
            continue
        record = next(row for row in records if row.get("姓名") == item["name"] and row.get("主页") == item["url"])
        record["邮箱"] = email
        settled.add((item["name"], item["url"]))
        certificates.append({"id": item["id"], "name": item["name"], "url": item["url"],
                             "verdict": decision["verdict"], "email": email,
                             "statuses": item["statuses"], "reviewed_by": decision["reviewed_by"],
                             "reviewed_at": decision["reviewed_at"]})
    remaining = [issue for issue in unresolved
                 if not (issue.get("status") in STATUSES
                         and (issue.get("姓名"), issue.get("主页")) in settled)]
    return remaining, pending, certificates


def collect_items(audit_path) -> list[dict]:
    """Items saved by the deterministic run; an edited item no longer matches its id."""
    audit = json.loads(Path(audit_path).read_text(encoding="utf-8"))
    items = audit.get("html_email_review_items") or []
    if any(item.get("id") != item_id(item) for item in items):
        raise ValueError("html e-mail evidence digest mismatch")
    return items


def has_pending(audit: dict) -> bool:
    return any(item.get("status") in STATUSES and "姓名" in item for item in audit.get("unresolved", []))


def evidence_missing(audit: dict) -> bool:
    """An audit written before this review existed: a deterministic rerun regenerates the evidence."""
    return has_pending(audit) and "html_email_review_items" not in audit


def review_complete(audit_path, audit: dict, items: list[dict]) -> bool:
    """Every blocked teacher has current evidence and a decision that still applies to it."""
    path = Path(audit_path).resolve()
    blocked = {(issue["姓名"], issue.get("主页")) for issue in audit.get("unresolved", [])
               if issue.get("status") in STATUSES and "姓名" in issue}
    if blocked - {(item["name"], item["url"]) for item in items}:
        return False
    decisions = load_decisions(path.parent / DECISIONS_FILE)
    task_id = task_id_for(path.parent)
    return all(valid_decision(item, decisions.get(item["id"], {}), task_id) is not None for item in items)
