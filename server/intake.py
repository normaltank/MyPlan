"""Batch intake: one college per task, optionally with the roster links a person gave.

Rows come from a textarea, often pasted from a spreadsheet::

    大学  学院  学院主页  [师资链接]

Tab-separated rows keep names that contain spaces; otherwise whitespace or
commas separate the columns. A college may occupy several rows, one per roster
link; its rows become a single task carrying every link, so the college is
reconnoitred, reconciled and delivered once.

The links are leads, not a verified plan: the scout still opens each one in a
real browser and decides whether it is a current faculty roster. Obvious data
problems are reported before any task is queued, for a person to confirm.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import re
from urllib.parse import urlsplit, urlunsplit

_HEADER_WORDS = ("主页", "链接", "网址", "入口", "官网", "URL", "url")
_SPLIT = re.compile(r"[\s,，]+")
_TRIM = "，。、；;,.\"'“”‘’（）()<>《》【】[] 　"
_SECOND_LEVEL = {"edu", "com", "org", "gov", "net", "ac"}


@dataclass
class IntakeCollege:
    school: str
    college: str
    url: str
    roster_urls: list[str] = field(default_factory=list)
    lines: list[int] = field(default_factory=list)


@dataclass
class IntakeResult:
    colleges: list[IntakeCollege]
    errors: list[str]
    warnings: list[str]


def normalize_url(raw: str) -> str | None:
    """An http(s) URL without credentials; a bare 'www.x.edu.cn/...' gets http://."""
    value = (raw or "").strip().strip(_TRIM)
    if not value:
        return None
    if not re.match(r"^[a-z][a-z0-9+.-]*://", value, re.I):
        if not re.match(r"^[\w-]+(\.[\w-]+)+(:\d+)?(/|$)", value):
            return None
        value = "http://" + value
    try:
        parts = urlsplit(value)
        port = parts.port  # Access validates the port's syntax and upper bound.
    except ValueError:
        return None
    if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
            or parts.username or parts.password or port == 0):
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def site_domain(url: str) -> str:
    """The registrable domain ('huse.edu.cn', 'hnist.cn') used to spot a wrong school."""
    labels = (urlsplit(url).hostname or "").split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _columns(line: str) -> list[str]:
    if "\t" in line:
        return [part.strip() for part in line.split("\t") if part.strip()]
    return [part for part in _SPLIT.split(line.strip()) if part]


def parse_batch(text: str) -> IntakeResult:
    colleges: dict[tuple[str, str], IntakeCollege] = {}
    errors: list[str] = []
    warnings: list[str] = []
    lines = (text or "").lstrip("﻿").splitlines()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        parts = _columns(line)
        if (not colleges and not errors and len(parts) in (3, 4) and normalize_url(parts[2]) is None
                and any(word in parts[2] for word in _HEADER_WORDS)):
            continue  # a header row pasted with the table ('大学 学院 学院主页 师资链接')
        if len(parts) not in (3, 4):
            errors.append(f"第 {number} 行应为：大学 学院 学院主页（官网入口） [师资链接]（当前 {len(parts)} 项）")
            continue
        school, college = parts[0], parts[1]
        url = normalize_url(parts[2])
        roster = normalize_url(parts[3]) if len(parts) == 4 else None
        if url is None:
            errors.append(f"第 {number} 行的学院主页（官网入口）不是有效网址")
            continue
        if len(parts) == 4 and roster is None:
            errors.append(f"第 {number} 行的师资链接不是有效网址")
            continue
        entry = colleges.setdefault((school, college), IntakeCollege(school, college, url))
        if entry.url != url:
            errors.append(f"第 {number} 行与第 {entry.lines[0]} 行是同一学院，但学院主页不同")
            continue
        entry.lines.append(number)
        if roster and roster not in entry.roster_urls:
            entry.roster_urls.append(roster)
    result = list(colleges.values())
    for entry in result:
        home = urlsplit(entry.url)
        for roster in entry.roster_urls:
            if site_domain(roster) != site_domain(entry.url):
                warnings.append(f"{entry.school} {entry.college}：师资链接 {roster} 不在学院主页的站点上")
            elif (urlsplit(roster).hostname == home.hostname
                  and not (urlsplit(roster).path or "/").startswith(home.path.rsplit("/", 1)[0] + "/")):
                warnings.append(f"{entry.school} {entry.college}：师资链接 {roster} 不在学院主页的目录下")
    by_school: dict[str, Counter] = {}
    for entry in result:
        by_school.setdefault(entry.school, Counter())[site_domain(entry.url)] += 1
    for entry in result:
        domains = by_school[entry.school]
        usual, count = domains.most_common(1)[0]
        if len(domains) > 1 and count > 1 and site_domain(entry.url) != usual:
            warnings.append(f"{entry.school} {entry.college}：学院主页在 {site_domain(entry.url)}，"
                            f"同校其他学院在 {usual}，请确认是否为本校学院")
    return IntakeResult(result, errors, warnings)
