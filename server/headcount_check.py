"""Independent headcount evidence for a harvested college. It never blocks delivery.

The harvest proves that every card on the rosters in the plan became a record.
It cannot see a roster the plan never named, or a list in the plan that is not a
faculty roster. This module compares the harvest with sources that do not depend
on the model's plan:

* numbers the site states about itself: a pager's record total next to the
  cards, and staff counts in the college's own introduction pages;
* a model-free navigation census: the site's own menu pages are opened and
  every page that lists people is read with the deterministic roster extractor,
  without any selector from the scout.

A name the census finds that the harvest lacks, a roster page outside the plan,
or a count that disagrees is reported with the page it came from, for a person to
confirm. The census also meets office staff, retirees and students, and stated
counts can be out of date, so a difference is a question, not a verdict.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

CENSUS_PAGE_LIMIT = 60
CENSUS_CONCURRENCY = 3
ROSTER_MIN_NAMES = 3
CENSUS_NAME_PRIOR = 0.85
LISTED_NAMES_LIMIT = 40
_NON_PAGE = re.compile(r"\.(?:pdf|docx?|xlsx?|pptx?|zip|rar|7z|jpe?g|png|gif|bmp|mp4|mp3)$", re.I)
# '共 74 条' beside a pager: the site's own record count for the list.
_PAGER_TOTAL = re.compile(r"共\s*(\d{1,4})\s*条")
# '专任教师 86 人', '教职工106名', '现有教授 20 人': staff counts a college states.
_STATED = re.compile(r"(专任教师|教职工|教职员工|在职教师|教授|副教授|讲师|博士生导师|硕士生导师)"
                     r"[^\d，,。；;、（）()]{0,6}?(\d{1,4})\s*(?:人|名|位)")


class _NoCachedWrapper:
    """The census reads every page with structural mining, never a cached wrapper."""

    def execute(self, html: str, url: str):
        return [], "miss"


def _compact(name: str) -> str:
    return re.sub(r"\s+", "", name or "")


def _canonical(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


def site_scope(entry_url: str):
    """Same host, and inside the entry's directory when the college lives under a path."""
    entry = urlsplit(entry_url)
    directory = str(PurePosixPath(entry.path or "/").parent) if not (entry.path or "/").endswith("/") else entry.path
    prefix = directory.rstrip("/") + "/"

    def inside(url: str) -> bool:
        parts = urlsplit(url)
        return (parts.scheme in {"http", "https"} and parts.netloc.lower() == entry.netloc.lower()
                and (parts.path or "/").startswith(prefix) and not _NON_PAGE.search(parts.path or ""))
    return inside


def page_links(html: str, url: str, inside) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        target = _canonical(urljoin(url, href))
        if inside(target) and target not in links:
            links.append(target)
    return links


def menu_links(pages: dict[str, str], inside) -> list[str]:
    """Links the site repeats on several of its own pages: menus, sub-menus, footers."""
    seen = Counter()
    order: list[str] = []
    for url, html in pages.items():
        for link in page_links(html, url, inside):
            seen[link] += 1
            if link not in order:
                order.append(link)
    needed = 2 if len(pages) > 1 else 1
    return [link for link in order if seen[link] >= needed]


def people_on_page(html: str, url: str) -> set[str]:
    """Names a deterministic reader finds on one page: roster cards and 、-lists."""
    from server.faculty_extractor import FacultyDirectoryExtractor
    from server.name_list import _person_name, people_chains
    names = set()
    try:
        # Only strings the name prior accepts: a leadership page's '副院长' is a post.
        names.update(name for name in (_person_name(_compact(candidate.name)) for candidate in
                     FacultyDirectoryExtractor.extract(html, url, executor=_NoCachedWrapper())) if name)
    except Exception:
        pass
    text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    for chain in people_chains(text):
        names.update(_compact(name) for name in chain.names)
    # A census reads pages no scout vetted, so it keeps only strong name
    # evidence: posts and headings such as '党委书记' score near 0.6.
    from iso_mining import name_prob
    return {name for name in names if name and name_prob(name) >= CENSUS_NAME_PRIOR}


def people_signals(html: str) -> dict[str, int]:
    """Counts that say whether a page lists people, without returning any name."""
    from iso_mining import name_prob
    from server.faculty_extractor import FacultyDirectoryExtractor, find_emails
    from server.name_list import people_chains
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    linked = set()
    for anchor in soup.find_all("a"):
        name, _title = FacultyDirectoryExtractor._clean_name(anchor.get_text(" ", strip=True))
        name = _compact(name)
        if 2 <= len(name) <= 4 and name_prob(name) >= CENSUS_NAME_PRIOR:
            linked.add(name)
    text = soup.get_text("\n", strip=True)
    listed = {name for chain in people_chains(text) for name in chain.names}
    return {"linked_names": len(linked), "listed_names": len(listed),
            "emails": len(set(find_emails(text))), "images": len(soup.find_all("img"))}


def pager_totals(roster_pages: Iterable[tuple[str, str, str]], observations: list[dict]) -> list[dict]:
    """A branch whose pager states a record total different from the cards captured."""
    stated: dict[str, set[int]] = {}
    for url, html, branch_url in roster_pages:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        values = {int(value) for value in _PAGER_TOTAL.findall(text)}
        if len(values) == 1:
            stated.setdefault(branch_url, set()).update(values)
    cards = Counter()
    page_branch = {url: branch_url for url, _, branch_url in roster_pages}
    for item in observations:
        branch_url = page_branch.get(item.get("url"))
        if branch_url and type(item.get("visible_cards")) is int:
            cards[branch_url] += item["visible_cards"]
    result = []
    for branch_url, values in stated.items():
        if len(values) != 1:
            continue
        total = next(iter(values))
        result.append({"branch_url": branch_url, "stated_total": total, "captured_cards": cards[branch_url],
                       "consistent": total == cards[branch_url]})
    return result


def stated_counts(pages: dict[str, str]) -> list[dict]:
    found = []
    for url, html in pages.items():
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        for label, value in _STATED.findall(text):
            item = {"label": label, "count": int(value), "url": url}
            if item not in found:
                found.append(item)
    return found


async def _open(context: Any, url: str) -> str | None:
    from server.site_limits import site_slot
    async with site_slot(url):
        page = await context.new_page()
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if response is not None and response.status >= 400:
                return None
            return await page.content()
        except Exception:
            return None
        finally:
            try:
                await page.close()
            except Exception:
                pass


async def census(context: Any, entry_url: str, plan: dict, *, limit: int = CENSUS_PAGE_LIMIT,
                 extra_urls: list[str] | tuple = ()) -> dict:
    """Open the site's menu pages and read every page that lists people.

    ``extra_urls`` are pages a person supplied with the task (roster links); they
    are read too, so a supplied list the scout left out is still compared.
    """
    inside = site_scope(entry_url)
    branch_urls = [_canonical(branch["url"]) for branch in plan.get("branches", []) if branch.get("url")]
    supplied = [_canonical(url) for url in extra_urls]
    seeds = list(dict.fromkeys([_canonical(entry_url), *branch_urls[:3]]))
    seed_results = dict(zip(seeds, await asyncio.gather(*(_open(context, url) for url in seeds))))
    seed_html = {url: html for url, html in seed_results.items() if html}
    failed_urls = [url for url, html in seed_results.items() if not html]
    if not seed_html:
        return {"status": "unavailable", "reason": "entry_unreachable", "pages": [],
                "failed_urls": failed_urls, "supplied": supplied}
    targets = list(dict.fromkeys([*branch_urls, *supplied, *menu_links(seed_html, inside)]))
    truncated = len(targets) > limit
    targets = targets[:limit]
    semaphore = asyncio.Semaphore(CENSUS_CONCURRENCY)

    async def read(url: str):
        if url in seed_results:
            return url, seed_results[url]
        async with semaphore:
            return url, await _open(context, url)

    opened = dict(await asyncio.gather(*(read(url) for url in targets)))
    pages = []
    for url in targets:
        html = opened.get(url)
        if not html:
            if url not in failed_urls:
                failed_urls.append(url)
            pages.append({"url": url, "opened": False})
            continue
        names = people_on_page(html, url)
        title = BeautifulSoup(html, "html.parser").title
        pages.append({"url": url, "opened": True, "title": (title.get_text(" ", strip=True) if title else "")[:60],
                      "names": sorted(names), "in_plan": url in branch_urls})
    status = "page_limit_reached" if truncated else ("partial" if failed_urls else "complete")
    return {"status": status, "pages": pages,
            "page_limit": limit, "supplied": supplied, "failed_urls": failed_urls,
            "html": {url: html for url, html in opened.items() if html}}


def compare(census_result: dict, harvested: Iterable[str], *, pager: list[dict] | None = None,
            stated: list[dict] | None = None) -> dict:
    """Differences between the harvest and the independent sources, with their pages."""
    harvested_records = list(harvested)
    harvested_count = len(harvested_records)
    people = {_compact(name) for name in harvested_records if name}
    roster_pages = [page for page in census_result.get("pages", [])
                    if page.get("opened") and len(page.get("names", [])) >= ROSTER_MIN_NAMES]
    missing: dict[str, list[str]] = {}
    for page in roster_pages:
        for name in page["names"]:
            if name not in people:
                missing.setdefault(name, []).append(page["url"])
    seen = {name for page in roster_pages for name in page["names"]}
    outside_plan = [{"url": page["url"], "names": len(page["names"]),
                     "not_harvested": sum(name not in people for name in page["names"])}
                    for page in roster_pages if not page["in_plan"]
                    and sum(name not in people for name in page["names"]) >= ROSTER_MIN_NAMES]
    pager = pager or []
    stated = stated or []
    teachers = [item for item in stated if item["label"] in {"专任教师", "在职教师"}]
    stated_gap = [item for item in teachers if item["count"] != harvested_count]
    differences = (len(missing) + len(outside_plan) + sum(not item["consistent"] for item in pager)
                   + len(stated_gap))
    by_page = sorted(({"url": page["url"], "title": page.get("title", ""),
                       "not_harvested": sum(name not in people for name in page["names"])}
                      for page in roster_pages), key=lambda item: -item["not_harvested"])
    by_page = [item for item in by_page if item["not_harvested"]]
    summary = []
    if missing:
        where = "、".join(f"{item['title'] or item['url']} {item['not_harvested']} 人" for item in by_page[:3])
        summary.append(f"导航普查另见 {len(missing)} 个姓名未入库（{where}）")
    if outside_plan:
        summary.append(f"{len(outside_plan)} 个名单页不在采集计划内")
    if any(not item["consistent"] for item in pager):
        summary.append("翻页器总条数与实采卡片数不一致")
    if stated_gap:
        summary.append("学院介绍的专任教师数与入库人数不同（介绍 " + "、".join(
            f"{count} 人" for count in dict.fromkeys(item["count"] for item in stated_gap)) + f"，入库 {harvested_count} 人）")
    pages_by_url = {page["url"]: page for page in census_result.get("pages", [])}
    supplied = []
    for url in census_result.get("supplied", []):
        page = pages_by_url.get(url, {})
        supplied.append({"url": url, "used": bool(page.get("in_plan")), "opened": bool(page.get("opened")),
                         "title": page.get("title", ""), "names": len(page.get("names", []))})
    status = census_result.get("status")
    if not summary:
        summary.append("未发现差额" if status == "complete" else "未发现差额（普查未完整）")
    if supplied:
        unused = [item for item in supplied if not item["used"]]
        text = f"给定链接 {len(supplied)} 条，采用 {len(supplied) - len(unused)} 条"
        if unused:
            text += "，未采用：" + "、".join(
                (item["title"] or item["url"]) + ("（打不开）" if not item["opened"] else "") for item in unused[:4])
        summary.insert(0, text)
    return {"status": "difference" if differences else ("consistent" if status == "complete" else "partial"),
            "summary": "；".join(summary),
            "harvested": harvested_count,
            "census": {"status": status, "pages_opened": sum(bool(page.get("opened"))
                                                            for page in census_result.get("pages", [])),
                       "roster_pages": len(roster_pages), "names_seen": len(seen),
                       "harvested_not_seen": len(people - seen)},
            "not_harvested": [{"name": name, "pages": urls[:3]}
                              for name, urls in sorted(missing.items())][:LISTED_NAMES_LIMIT],
            "not_harvested_count": len(missing),
            "not_harvested_by_page": by_page[:20],
            "roster_pages_outside_plan": outside_plan,
            "pager_totals": pager, "stated_counts": stated, "supplied_links": supplied}
