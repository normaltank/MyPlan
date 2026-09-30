"""Record-level consistency checks against the captured source pages.

Every field is extracted deterministically, so each should be traceable to the
page it came from. These checks look for records where that trace is missing or
points at someone else. They are hints for a reviewer, never edits: nothing here
changes a record or blocks delivery.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from bs4 import BeautifulSoup

LISTED_LIMIT = 30


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def page_text(html: str) -> str:
    return _compact(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))


def check_records(records: list[dict[str, Any]], pages: dict[str, str]) -> dict[str, Any]:
    """``pages`` maps each captured source URL (roster or profile) to its text."""
    from server.titles import TITLE_KEYWORDS
    titles = set(TITLE_KEYWORDS) | {"教师"}
    names = {_compact(row.get("姓名", "")) for row in records if row.get("姓名")}
    emails = Counter((row.get("邮箱") or "").strip().lower() for row in records if row.get("邮箱"))
    flags = []
    for row in records:
        name, url = _compact(row.get("姓名", "")), row.get("主页", "")
        email = (row.get("邮箱") or "").strip()
        text = pages.get(url)
        found = []
        if text is not None and name and name not in text:
            found.append("name_not_on_source_page")
        if email and text is not None and email.lower() not in text.lower():
            found.append("email_not_on_source_page")
        if email and emails[email.lower()] > 1:
            found.append("email_on_several_records")
        if row.get("职称") and row["职称"] not in titles:
            found.append("title_outside_vocabulary")
        bio = _compact(row.get("简介", ""))
        if bio and name and name not in bio:
            others = sorted(other for other in names if other != name and len(other) >= 2 and other in bio)
            if others:
                found.append("biography_names_another_teacher")
        for check in found:
            flags.append({"check": check, "name": row.get("姓名", ""), "url": url})
    counts = Counter(item["check"] for item in flags)
    return {"checked": len(records), "flagged_records": len({(item["name"], item["url"]) for item in flags}),
            "counts": dict(counts), "items": flags[:LISTED_LIMIT]}
