"""Keep observations distinct until source evidence resolves profile aliases."""
from collections import defaultdict
from dataclasses import replace
import hashlib
import re
from urllib.parse import urlsplit

ALIASES = {
    "姓名": ("name",), "主页": ("profile_url", "url", "主页链接"),
    "邮箱": ("email",), "职称": ("title",), "简介": ("bio", "full_bio"),
}


def field_value(record, field):
    for key in (field, *ALIASES.get(field, ())):
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def identity_review_issues(records, reviewed_pairs=None):
    groups = defaultdict(set)
    by_name = defaultdict(list)
    for record in records:
        name, url = field_value(record, "姓名"), field_value(record, "主页")
        if name and url:
            groups[name].add(url)
            by_name[name].append(record)
    issues = []
    for name, urls in groups.items():
        if len(urls) > 1:
            if reviewed_pairs:
                from itertools import combinations
                from server.identity_review import observation_key
                if all(frozenset((observation_key(a), observation_key(b))) in reviewed_pairs
                       for a, b in combinations(by_name[name], 2)
                       if field_value(a, "主页") != field_value(b, "主页")):
                    continue
            ordered = sorted(urls)
            issues.append({"status": "distinct_profile_identity_unverified", "url": ordered[0],
                "profile_urls": ordered, "observation_count": len(ordered),
                "identity_group": hashlib.sha256((name + "\n" + "\n".join(ordered)).encode()).hexdigest()})
    return issues


def resolve_broken_profile_aliases(candidates, profile_html, errors, profile_evidence,
                                   roster_sources):
    """Resolve an HTTP 404/410 alias only with repeated official roster evidence.

    The surviving URL must be the sole other URL for the exact same name on
    the same host, and its browser-verified profile body must contain that
    name. Otherwise the broken link remains an explicit delivery blocker.
    """
    from bs4 import BeautifulSoup
    from server.profile_content import scope_profile_html

    by_name = defaultdict(set)
    by_url = defaultdict(set)
    for candidate in candidates:
        by_name[candidate.name].add(candidate.url)
        by_url[candidate.url].add(candidate.name)
    broken = {item.get("url") for item in errors
              if item.get("status") == "profile_navigation_failed"
              and any(f"HTTP {status}" in str(item.get("error", "")) for status in (404, 410))}
    aliases = {}
    for dead in broken:
        if not dead or profile_html.get(dead) or len(by_url[dead]) != 1:
            continue
        sources = {item.get("roster_url") for item in roster_sources.get(dead, [])
                   if item.get("roster_url")}
        if len(sources) < 2:
            continue
        name = next(iter(by_url[dead]))
        other = by_name[name] - {dead}
        if len(other) != 1:
            continue
        live = next(iter(other))
        if urlsplit(dead).netloc != urlsplit(live).netloc or not profile_html.get(live):
            continue
        proof = profile_evidence.get(live) or {}
        if proof.get("confirmed") is not True:
            continue
        scoped, _, issue = scope_profile_html(
            profile_html[live], proof.get("profile_content_selector", ""), live,
            start_selector=proof.get("profile_content_start_selector"))
        if issue or not scoped:
            continue
        body = BeautifulSoup(scoped, "html.parser").get_text(" ", strip=True)
        if re.sub(r"\s+", "", name) not in re.sub(r"\s+", "", body):
            continue
        aliases[dead] = live
    return aliases


def resolve_duplicate_profile_aliases(candidates, profile_html, profile_evidence, list_urls):
    """One person published under several URLs (badr 2026-10-09).

    A name with two or more personal pages on one host is one teacher when the
    pages carry the same profile body: the body selector proven in the browser
    on one of them selects, on every page, the same non-empty text containing
    that name. The proven page is kept and the others become its aliases.
    Different bodies, a body the selector cannot isolate, or a page shared with
    another name keep the identity for review.
    """
    from bs4 import BeautifulSoup
    from server.profile_content import scope_profile_html

    by_name = defaultdict(list)
    by_url = defaultdict(set)
    for candidate in candidates:
        by_url[candidate.url].add(candidate.name)
        if candidate.url and candidate.url not in list_urls and candidate.url not in by_name[candidate.name]:
            by_name[candidate.name].append(candidate.url)
    aliases = {}
    for name, urls in by_name.items():
        if (len(urls) < 2 or len({urlsplit(url).netloc for url in urls}) != 1
                or any(len(by_url[url]) != 1 or not profile_html.get(url) for url in urls)):
            continue
        proven = [url for url in urls if (profile_evidence.get(url) or {}).get("confirmed") is True]
        if not proven:
            continue
        kept = proven[0]
        proof = profile_evidence[kept]
        bodies = set()
        for url in urls:
            scoped, _, issue = scope_profile_html(
                profile_html[url], proof.get("profile_content_selector", ""), url,
                start_selector=proof.get("profile_content_start_selector"))
            if issue or not scoped:
                break
            bodies.add(re.sub(r"\s+", "", BeautifulSoup(scoped, "html.parser").get_text(" ", strip=True)))
        else:
            body = next(iter(bodies)) if len(bodies) == 1 else ""
            if body and re.sub(r"\s+", "", name) in body:
                aliases.update({url: kept for url in urls if url != kept})
    return aliases


def merge_same_name_observations(candidates, candidate_sources, list_urls):
    """Legacy source-string coalescing; typed page witnesses never merge by name.

    ``list_urls`` are the roster pages themselves: a candidate whose URL is one
    of them has no personal page (a name on a supplementary list such as a
    教研室 roster or a 导师分专业名录). Within one college ticket:

    * exactly one personal page for a name: every no-page observation of that
      name is the same person; the personal-page candidate is kept and the
      lists are recorded as further sources;
    * no personal page: several no-page observations of one name are one person
      (kept once, first in plan order);
    * two or more personal pages: unchanged, the identity stays for review.
    """
    by_name = defaultdict(list)
    for candidate in candidates:
        by_name[candidate.name].append(candidate)
    dropped = set()
    merged_conflicts = {}
    merges = []
    sources = {key: set(value) for key, value in candidate_sources.items()}
    for name, group in by_name.items():
        from server.source_qualification import SourceEvidence
        if any(isinstance(source, SourceEvidence) for candidate in group
               for source in candidate_sources.get((candidate.name, candidate.url), ())):
            # A source witness belongs to this observation. Same-name lists
            # cannot inherit another person's faculty relationship or identity.
            continue
        listed = [c for c in group if c.url in list_urls]
        pages = list(dict.fromkeys(c.url for c in group if c.url not in list_urls))
        if len(pages) > 1 or not listed:
            continue
        if pages:
            kept_url, merged = pages[0], listed
            rule = "unique_personal_page"
        elif len({c.url for c in listed}) > 1:
            kept_url, merged = listed[0].url, [c for c in listed if c.url != listed[0].url]
            rule = "same_name_lists_without_pages"
        else:
            continue
        for candidate in merged:
            dropped.add((candidate.name, candidate.url))
            sources.setdefault((name, kept_url), set()).update(
                sources.pop((candidate.name, candidate.url), set()))
        merged_conflicts[(name, kept_url)] = tuple(dict.fromkeys(
            conflict for candidate in group for conflict in candidate.mailto_conflicts))
        merges.append({"name": name, "kept_url": kept_url, "merged_list_urls": sorted({c.url for c in merged}),
                       "rule": rule})
    kept = [replace(c, mailto_conflicts=merged_conflicts.get((c.name, c.url), c.mailto_conflicts))
            for c in candidates if (c.name, c.url) not in dropped]
    return kept, sources, merges
