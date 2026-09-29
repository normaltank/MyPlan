"""Keep observations distinct until source evidence resolves profile aliases."""
from collections import defaultdict
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


def identity_review_issues(records):
    groups = defaultdict(set)
    for record in records:
        name, url = field_value(record, "姓名"), field_value(record, "主页")
        if name and url:
            groups[name].add(url)
    issues = []
    for name, urls in groups.items():
        if len(urls) > 1:
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
