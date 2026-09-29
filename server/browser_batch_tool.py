"""A bounded, browser-backed batch tool selected by a college Subagent.

The caller supplies verified roster branches and pagination controls. Every
website page is opened through Playwright; the existing extractor receives the
rendered HTML. Unresolved navigation or extraction blocks workbook delivery.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
from html import escape
import json
import os
import re
import shutil
import time
from collections import Counter
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urljoin, urlparse

from bs4 import BeautifulSoup

from server import excel_export
from server.faculty_extractor import (
    FacultyCandidate, FacultyDirectoryExtractor, FacultyHarvestPipeline, FacultyProfileExtractor,
    find_emails, is_personal_email_for_scholar,
)
from server.harvest_eligibility import classify_candidates
from server.harvest_workflow import capture_profiles, capture_sources, commit_records, reconcile_rosters


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    if not str(plan.get("school", "")).strip() or not str(plan.get("college", "")).strip():
        raise ValueError("plan requires school and college")
    branches = plan.get("branches")
    if not isinstance(branches, list) or not branches:
        raise ValueError("plan requires at least one branch")
    allowed_sources = {"university_faculty_roster", "hospital_doctor_roster"}
    if plan.get("source_kind") is not None and plan["source_kind"] not in allowed_sources:
        raise ValueError("invalid source_kind")
    for branch in branches:
        if not isinstance(branch, dict):
            raise ValueError("each branch must be an object")
        if branch.get("source_kind") is not None and branch["source_kind"] not in allowed_sources:
            raise ValueError("invalid branch source_kind")
        if "empty_state_selector" in branch or "roster_container_selector" in branch:
            from server.empty_roster import empty_roster_branch_valid
            if not empty_roster_branch_valid(branch):
                raise ValueError("empty selectors require an ordinary single-page roster")
        parsed = urlparse(str(branch.get("url", "")))
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("each branch needs an absolute HTTP(S) url")
        if branch.get("kind", "roster") == "navigation":
            if not str(branch.get("child_link_selector", "")).strip():
                raise ValueError("navigation branch requires child_link_selector")
            max_children = branch.get("max_children", 30)
            if not isinstance(max_children, int) or not 1 <= max_children <= 100:
                raise ValueError("max_children must be an integer from 1 to 100")
            child = branch.get("child_branch")
            if not isinstance(child, dict) or "url" in child:
                raise ValueError("navigation branch requires a child_branch without url")
            if "empty_state_selector" in child or "roster_container_selector" in child:
                raise ValueError("empty selectors are not supported in navigation children")
            if child.get("source_kind") is not None and child["source_kind"] not in allowed_sources:
                raise ValueError("invalid child source_kind")
            if bool(child.get("single_page")) == bool(child.get("next_selector")):
                raise ValueError("child_branch needs exactly one of single_page or next_selector")
            if not str(child.get("card_selector", "")).strip():
                raise ValueError("child_branch requires card_selector")
            continue
        if branch.get("kind", "roster") != "roster":
            raise ValueError("branch kind must be roster or navigation")
        if bool(branch.get("single_page")) == bool(branch.get("next_selector")):
            raise ValueError("each branch needs exactly one of single_page or next_selector")
        if "card_selector" in branch and not str(branch["card_selector"]).strip():
            raise ValueError("card_selector must be non-empty")
        max_pages = branch.get("max_pages", 20)
        if not isinstance(max_pages, int) or not 1 <= max_pages <= 100:
            raise ValueError("max_pages must be an integer from 1 to 100")
    concurrency = plan.get("profile_concurrency", 4)
    if not isinstance(concurrency, int) or not 1 <= concurrency <= 8:
        raise ValueError("profile_concurrency must be an integer from 1 to 8")
    if "profile_ready_selector" in plan and not str(plan["profile_ready_selector"]).strip():
        raise ValueError("profile_ready_selector must be non-empty")
    source_urls = {branch["url"] for branch in branches}
    verified = plan.get("verified_candidates", [])
    if not isinstance(verified, list):
        raise ValueError("verified_candidates must be a list")
    for item in verified:
        if not isinstance(item, dict) or not str(item.get("name", "")).strip():
            raise ValueError("each verified candidate needs a name")
        if item.get("source_url") not in source_urls:
            raise ValueError("verified candidate source_url must be a supplied branch")
        if not Path(str(item.get("evidence_path", ""))).is_file():
            raise ValueError("verified candidate needs an existing evidence_path")
        profile_url = item.get("profile_url")
        if profile_url:
            parsed = urlparse(str(profile_url))
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("verified candidate profile_url must be an absolute HTTP(S) URL")
    reviews = plan.get("poster_reviews", [])
    if not isinstance(reviews, list):
        raise ValueError("poster_reviews must be a list")
    seen_reviews = set()
    for review in reviews:
        if not isinstance(review, dict) or review.get("source_url") not in source_urls:
            raise ValueError("poster review source_url must be a supplied branch")
        if review["source_url"] in seen_reviews:
            raise ValueError("poster review source_url must be unique")
        seen_reviews.add(review["source_url"])
        count = review.get("expected_count")
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise ValueError("poster expected_count must be a positive integer")
    appointments = plan.get("verified_appointments", [])
    if not isinstance(appointments, list):
        raise ValueError("verified_appointments must be a list")
    for item in appointments:
        if not isinstance(item, dict) or any(not str(item.get(key, "")).strip()
                                               for key in ("name", "profile_url", "quote", "academic_title")):
            raise ValueError("verified appointment requires name, profile_url, quote and academic_title")
        parsed = urlparse(item["profile_url"])
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("appointment profile_url must be absolute HTTP(S)")
        if len(item["quote"]) > 300 or item["academic_title"] not in item["quote"]:
            raise ValueError("appointment quote must be short and contain academic_title")
        if plan["school"] not in item["quote"] and plan["college"] not in item["quote"]:
            raise ValueError("appointment quote must name target university or college")


class _PollingExecutor:
    """Bypass the persistent wrapper cache for transient AJAX snapshots."""

    queue = None

    def execute(self, _html: str, _url: str) -> tuple[list, str]:
        return [], "miss"


def _roster_signature(html: str, url: str) -> tuple:
    found = FacultyDirectoryExtractor.extract(html, url, executor=_PollingExecutor())
    return tuple((cand.name, cand.url, cand.title) for cand in found)


def _pagination_candidates(html: str, current_url: str) -> list[str | None]:
    """Find bounded pagination leads from browser-rendered structure, not names."""
    soup = BeautifulSoup(html, "html.parser")
    leads: list[str | None] = []
    for control in soup.select("a, button"):
        if (control.has_attr("disabled") or control.get("aria-disabled") == "true"
                or control.get("aria-current") == "page"):
            continue
        rel = control.get("rel", [])
        is_next = "next" in rel
        control_classes = " ".join(control.get("class", []))
        is_next = is_next or bool(re.search(r"(?:^|[-_\s])(?:next|nextpage)(?:$|[-_\s])",
                                             control_classes, re.I))
        ancestor = control
        in_pager = False
        while ancestor is not None and getattr(ancestor, "name", None):
            if ancestor.name in {"body", "html", "main"}:
                break
            tokens = " ".join(ancestor.get("class", [])) + " " + str(ancestor.get("id", ""))
            label = str(ancestor.get("aria-label", ""))
            if re.search(r"(?:^|[-_\s])(?:pagination|pager|pages)(?:$|[-_\s])", tokens, re.I) or "pagination" in label.lower():
                in_pager = True
                break
            ancestor = ancestor.parent
        if not (is_next or in_pager):
            continue
        href = control.get("href", "").strip()
        target = urljoin(current_url, href) if href and not href.startswith(("#", "javascript:")) else None
        if target == current_url:
            target = None  # Same-URL controls can update the roster through AJAX.
        leads.append(target)
        if len(leads) >= 8:
            break
    return leads


async def _probe_single_page(context: Any, html: str, url: str, card_selector: str) -> list[dict]:
    """Fail closed if an asserted single page has an unexplained pager lead."""
    if not card_selector:
        return []  # roster reconciliation handles this; reviewed posters are separate
    baseline = tuple(card.get_text(" ", strip=True) for card in BeautifulSoup(html, "html.parser").select(card_selector))
    unresolved: list[dict] = []
    for target in _pagination_candidates(html, url):
        if target is None:
            unresolved.append({"url": url, "status": "single_page_pagination_unverified"})
            continue
        if urlparse(target).hostname != urlparse(url).hostname:
            unresolved.append({"url": url, "target": target,
                               "status": "single_page_pagination_unverified",
                               "error": "pagination target is outside the verified host"})
            continue
        probe = await context.new_page()
        try:
            response = await probe.goto(target, wait_until="domcontentloaded", timeout=30000)
            if response is not None and response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}")
            other = await probe.content()
            cards = tuple(card.get_text(" ", strip=True) for card in BeautifulSoup(other, "html.parser").select(card_selector))
            unresolved.append({"url": url, "target": target,
                               "status": "single_page_has_next" if cards and cards != baseline
                               else "single_page_pagination_unverified"})
        except Exception as exc:
            unresolved.append({"url": url, "target": target,
                               "status": "single_page_pagination_unverified",
                               "error": f"{type(exc).__name__}: {exc}"})
        finally:
            await probe.close()
    return unresolved


async def _wait_for_change(page: Any, before: str, url: str, seconds: float = 3.5) -> str | None:
    """Wait for a changed roster, ignoring unrelated clocks and page chrome."""
    previous = _roster_signature(before, url)
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        current = await page.content()
        sig = _roster_signature(current, page.url)
        if current != before and sig and sig != previous:
            return current
        await asyncio.sleep(0.1)
def _native_chrome_fallback_disabled() -> bool:
    """True only for an explicit truthy value.

    A bare truthiness test would treat HARVEST_DISABLE_NATIVE_CHROME_FALLBACK=0
    as "disabled", which reads backwards for an operator trying to switch it off.
    """
    return os.environ.get("HARVEST_DISABLE_NATIVE_CHROME_FALLBACK", "").strip().lower() in {
        "1", "true", "yes", "on"}


def _native_chrome_executable() -> Path | None:
    """Resolve the configured native Chrome without assuming a Windows host."""
    configured = os.environ.get("CHROME_PATH", "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_file() else None
    if os.name == "nt":
        path = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        if path.is_file():
            return path
    for command in ("google-chrome", "chromium", "chromium-browser"):
        executable = shutil.which(command)
        if executable:
            return Path(executable)
    return None


_NATIVE_CHROME_FLAGS = ("--headless=new", "--no-first-run", "--no-default-browser-check",
                        "--disable-search-engine-choice-screen", "--remote-debugging-port=0")


def _launch_native_chrome(chrome_exe: Path, urls: list[str]) -> tuple[Any, Path, int | None]:
    """Start a private headless Chrome and return (process, profile dir, CDP port).

    Port 0 lets Chrome pick a free port and report it in DevToolsActivePort, so
    concurrent colleges never attach to each other's browser (fixed ports did).
    """
    import subprocess
    import tempfile

    user_dir = Path(tempfile.mkdtemp(prefix="chrome_native_"))
    proc = subprocess.Popen([str(chrome_exe), *_NATIVE_CHROME_FLAGS, f"--user-data-dir={user_dir}", *urls],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    port_file = user_dir / "DevToolsActivePort"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and proc.poll() is None:
        with contextlib.suppress(OSError, ValueError, IndexError):
            return proc, user_dir, int(port_file.read_text(encoding="ascii").splitlines()[0])
        time.sleep(0.1)
    return proc, user_dir, None


def _stop_native_chrome(proc: Any, user_dir: Path) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        with contextlib.suppress(Exception):
            proc.kill()
            proc.wait(timeout=5)
    shutil.rmtree(user_dir, ignore_errors=True)


def _collect_native_pages(port: int, started: float, settle_seconds: float) -> dict[str, str]:
    """Read every tab after it loads and at least ``settle_seconds`` after launch."""
    from playwright.sync_api import sync_playwright

    pages: dict[str, str] = {}
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}")
        try:
            for context in browser.contexts:
                for page in context.pages:
                    with contextlib.suppress(Exception):
                        page.wait_for_load_state("load", timeout=15000)
            # Keep the previous fixed post-launch settle time as a lower bound for
            # script-rendered content.
            remaining = settle_seconds - (time.monotonic() - started)
            if remaining > 0:
                time.sleep(remaining)
            for context in browser.contexts:
                for page in context.pages:
                    pages[page.url] = page.content()
        finally:
            with contextlib.suppress(Exception):
                browser.close()
    return pages


def _fetch_page_via_native_chrome(url: str) -> str | None:
    if _native_chrome_fallback_disabled():
        # Offline/fixture runs must never launch a browser against a live site.
        return None
    chrome_exe = _native_chrome_executable()
    if chrome_exe is None:
        return None
    started = time.monotonic()
    proc, user_dir, port = _launch_native_chrome(chrome_exe, [url])
    try:
        if port is None:
            return None
        pages = _collect_native_pages(port, started, settle_seconds=5)
        return next(iter(pages.values()), None)
    except Exception:
        return None
    finally:
        _stop_native_chrome(proc, user_dir)


def _batch_fetch_profiles_via_native_chrome(urls: list[str], batch_size: int = 8) -> dict[str, str]:
    if _native_chrome_fallback_disabled():
        # A fixture-backed context has no profiles to recover; launching real
        # Chrome here used to open visible windows against live hospital pages.
        return {}
    chrome_exe = _native_chrome_executable()
    if chrome_exe is None:
        return {}
    results: dict[str, str] = {}
    for i in range(0, len(urls), batch_size):
        chunk = urls[i:i + batch_size]
        started = time.monotonic()
        proc, user_dir, port = _launch_native_chrome(chrome_exe, chunk)
        try:
            if port is not None:
                results.update(_collect_native_pages(port, started, settle_seconds=6))
        except Exception:
            pass
        finally:
            _stop_native_chrome(proc, user_dir)
    return results


def _frontier_signature(html: str, branch: dict) -> tuple | None:
    """Compare the verified source and control DOM, ignoring unrelated chrome."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(branch.get("card_selector", "")) if branch.get("card_selector") else []
    if not cards:
        return None
    controls = soup.select(branch["next_selector"]) if branch.get("next_selector") else []
    return tuple(str(card) for card in cards), tuple(str(control) for control in controls)


def _restorable_prefix(branch: dict, pages: list[dict]) -> bool:
    if not pages or branch.get("single_page") or branch.get("initial_click_selector"):
        return False
    host = urlparse(branch["url"]).netloc.lower()
    urls = [row["url"] for row in pages]
    return (len(urls) <= branch.get("max_pages", 20) and len(set(urls)) == len(urls)
            and all(urlparse(url).scheme in {"http", "https"}
                    and urlparse(url).netloc.lower() == host for url in urls))


async def _capture_branch(context: Any, branch: dict[str, Any], *, on_page=None,
                          resume_pages=None) -> tuple[list[tuple[str, str]], list[dict], dict]:
    page = await context.new_page()
    from server.browser_readiness import install_request_tracking
    tracker = install_request_tracking(page)
    url = branch["url"]
    captured: list[tuple[str, str]] = []
    unresolved: list[dict] = []
    evidence = {"url": url, "visited_pages": 0, "next_clicks": 0, "end": "unresolved",
                "restored_pages": 0}
    try:
        saved = resume_pages or []
        start_page = 1
        if _restorable_prefix(branch, saved):
            last = saved[-1]
            try:
                response = await page.goto(last["url"], wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_load_state("networkidle", timeout=10000)
                baseline_activity = tracker.activity
                initially_idle = tracker.available and tracker.navigation_observed and not tracker.pending
                current = await page.content()
                signature = _frontier_signature(last["html"], branch)
                await asyncio.sleep(0.3)
                stable = _frontier_signature(await page.content(), branch) == _frontier_signature(current, branch)
                matched = (not (response is not None and response.status >= 400)
                           and initially_idle and not tracker.pending and not tracker.failures
                           and tracker.activity == baseline_activity
                           and stable and page.url == last["url"] and signature is not None
                           and _frontier_signature(current, branch) == signature)
            except Exception:
                matched = False
            if matched:
                captured = [(row["url"], row["html"]) for row in saved]
                start_page = len(saved)
                evidence.update(restored_pages=len(saved), resumed_from_page=start_page)
            else:
                evidence["resume_rejected"] = "frontier_changed_or_unavailable"
        if not evidence["restored_pages"]:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if response is not None and response.status == 412:
            await page.wait_for_timeout(2500)
        elif response is not None and response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        selector = branch.get("initial_click_selector")
        if selector:
            target = page.locator(selector)
            if await target.count() != 1:
                unresolved.append({"url": url, "status": "initial_control_not_unique", "selector": selector})
                return captured, unresolved, evidence
            before = await page.content()
            await target.click()
            if await _wait_for_change(page, before, page.url) is None:
                unresolved.append({"url": url, "status": "initial_control_unchanged", "selector": selector})
                return captured, unresolved, evidence

        for page_number in range(start_page, branch.get("max_pages", 20) + 1):
            if branch.get("empty_state_selector"):
                await page.wait_for_load_state("networkidle", timeout=2000)
            html = await page.content()
            if not (evidence["restored_pages"] and page_number == start_page):
                captured.append((page.url, html))
                if on_page:
                    on_page(page.url, html, page_number - 1)
            evidence["visited_pages"] = page_number
            if branch.get("single_page"):
                probe_errors = await _probe_single_page(context, html, page.url,
                                                         branch.get("card_selector", ""))
                unresolved.extend(probe_errors)
                evidence["end"] = ("single_page_pagination_unverified" if probe_errors
                                   else "single_page_no_pagination_control_seen")
                if branch.get("empty_state_selector"):
                    from server.empty_roster import verify_empty_roster
                    proof = await verify_empty_roster(page, branch, html)
                    evidence["empty_roster"] = proof
                    if proof["confirmed"] and not probe_errors:
                        evidence["end"] = "confirmed_empty_roster"
                    else:
                        evidence["end"] = "empty_roster_unverified"
                        unresolved.append({"url": page.url, "status": "empty_roster_not_verified"})
                break

            next_selector = branch["next_selector"]
            next_control = page.locator(next_selector)
            count = await next_control.count()
            if count == 0:
                evidence["end"] = "next_control_absent"
                break
            if count != 1:
                unresolved.append({"url": page.url, "page": page_number,
                                   "status": "next_control_not_unique", "selector": next_selector})
                break
            if not await next_control.is_enabled():
                evidence["end"] = "next_control_disabled"
                break
            if page_number == branch.get("max_pages", 20):
                unresolved.append({"url": page.url, "page": page_number,
                                   "status": "page_limit_reached", "selector": next_selector})
                break
            await next_control.click()
            evidence["next_clicks"] += 1
            if await _wait_for_change(page, html, page.url) is None:
                unresolved.append({"url": page.url, "page": page_number,
                                   "status": "pagination_unchanged", "selector": next_selector})
                break
    except Exception as exc:
        if branch.get("single_page"):
            native_html = await asyncio.to_thread(_fetch_page_via_native_chrome, url)
            if native_html and len(native_html) > 1000:
                captured.append((url, native_html))
                if on_page:
                    on_page(url, native_html, 0)
                evidence["visited_pages"] = 1
                probe_errors = await _probe_single_page(context, native_html, url,
                                                         branch.get("card_selector", ""))
                evidence["end"] = ("single_page_pagination_unverified" if probe_errors
                                   else "single_page_no_pagination_control_seen")
                if branch.get("empty_state_selector"):
                    evidence["end"] = "empty_roster_unverified"
                    probe_errors.append({"url": url, "status": "empty_roster_not_verified"})
                return captured, probe_errors, evidence
        unresolved.append({"url": url, "status": "browser_navigation_failed",
                           "error": f"{type(exc).__name__}: {exc}"})
    finally:
        await page.close()
    return captured, unresolved, evidence


async def _discover_children(context: Any, branch: dict[str, Any]) -> tuple[str | None, list[str], list[dict], dict]:
    """Read one Agent-scoped navigation page; never recursively follow arbitrary links."""
    url = branch["url"]
    page = await context.new_page()
    errors: list[dict] = []
    links: list[str] = []
    html = None
    evidence = {"url": url, "kind": "navigation", "discovered_children": 0, "children": []}
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if response is not None and response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        html = await page.content()
        selected = BeautifulSoup(html, "html.parser").select(branch["child_link_selector"])
        if not selected:
            errors.append({"url": url, "status": "child_selector_empty"})
        seen = set()
        for node in selected:
            href = node.get("href")
            if not href:
                errors.append({"url": url, "status": "child_link_missing_href"})
                continue
            child_url = urljoin(url, href)
            parsed = urlparse(child_url)
            if parsed.scheme not in ("http", "https") or parsed.netloc != urlparse(url).netloc:
                errors.append({"url": child_url, "status": "child_outside_verified_host"})
                continue
            if child_url not in seen:
                links.append(child_url)
                seen.add(child_url)
        if len(links) > branch.get("max_children", 30):
            errors.append({"url": url, "status": "child_limit_reached",
                           "discovered": len(links), "limit": branch.get("max_children", 30)})
            links = links[:branch.get("max_children", 30)]
        evidence["discovered_children"] = len(links)
        evidence["children"] = links
    except Exception as exc:
        errors.append({"url": url, "status": "browser_navigation_failed",
                       "error": f"{type(exc).__name__}: {exc}"})
    finally:
        await page.close()
    return html, links, errors, evidence


def _safe_name(value: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "", value).strip() or "college"


async def run_plan(plan: dict[str, Any], browser_context: Any, output_dir: str | Path, *, state=None) -> dict[str, Any]:
    """Capture a Subagent-selected college plan using an existing browser context."""
    started = time.perf_counter()
    _validate_plan(plan)
    if state is not None:
        canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if state.plan_digest != hashlib.sha256(canonical.encode("utf-8")).hexdigest():
            raise ValueError("checkpoint state belongs to a different plan")
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    restored_branches = 0
    resumed_branches = 0
    restored_roster_pages = 0
    cached_profile_urls = {row["url"] for row in state.list_pages("profile")} if state else set()
    restored_profiles = len(cached_profile_urls)

    async def capture_branch(context, branch):
        nonlocal restored_branches, resumed_branches, restored_roster_pages
        key = json.dumps(branch, sort_keys=True, separators=(",", ":"))
        bundle = state.load_page_record("branch", branch["url"], key=key) if state and not branch.get("empty_state_selector") else None
        if bundle:
            refs = bundle["meta"].get("pages", [])
            saved = [state.load_page_record("roster", ref["url"], key=ref["key"]) for ref in refs]
            if refs and all(saved):
                restored_branches += 1
                evidence = {**bundle["meta"]["evidence"], "restored": True,
                            "captured_at": bundle["captured_at"]}
                return [(row["url"], row["html"]) for row in saved], [], evidence
        def checkpoint(url, html, sequence):
            state.save_roster_progress(branch, url, html, sequence)
        kwargs = {"on_page": checkpoint, "resume_pages": state.load_roster_progress(branch)} if state else {}
        pages, errors, evidence = await _capture_branch(context, branch, **kwargs)
        if evidence.get("restored_pages"):
            resumed_branches += 1
            restored_roster_pages += evidence["restored_pages"]
        if state and not errors and evidence.get("end") in {
                "single_page_no_pagination_control_seen", "next_control_absent", "next_control_disabled"}:
            refs = []
            for index, (url, html) in enumerate(pages):
                page_key = key + f"|{index}"
                refs.append({"url": url, "key": page_key})
            if refs:
                state.save_page("branch", branch["url"], pages[0][1], key=key,
                                meta={"pages": refs, "evidence": evidence})
        return pages, errors, evidence

    source_capture = await capture_sources(
        plan,
        browser_context,
        capture_branch=capture_branch,
        discover_children=_discover_children,
    )
    all_pages = [(page.url, page.html) for page in source_capture.roster_pages]
    image_roster_cards: dict[str, int] = {}
    for captured_page in source_capture.roster_pages:
        selector = captured_page.branch.get("card_selector")
        if selector:
            cards = BeautifulSoup(captured_page.html, "html.parser").select(selector)
            if cards and all(card.name == "img" for card in cards):
                image_roster_cards[captured_page.url] = len(cards)
    navigation_pages = source_capture.navigation_pages
    unresolved: list[dict] = list(source_capture.unresolved)
    branches = source_capture.branches

    candidates = []
    candidate_sources: dict[tuple[str, str], set[str]] = {}
    verified = plan.get("verified_candidates", [])
    manually_covered_urls = {item["source_url"] for item in verified}
    reviews = {item["source_url"]: item for item in plan.get("poster_reviews", [])}
    for source_url in manually_covered_urls:
        review = reviews.get(source_url)
        if not review or review.get("reviewed_full_image") is not True:
            unresolved.append({"url": source_url, "status": "poster_full_image_review_missing"})
            continue
        count = len({item["name"].strip() for item in verified if item["source_url"] == source_url})
        if count != review["expected_count"]:
            unresolved.append({"url": source_url, "status": "poster_coverage_mismatch",
                               "expected_count": review["expected_count"], "verified_count": count})
    roster_reconciliation = reconcile_rosters(source_capture.roster_pages, plan)
    candidates.extend(roster_reconciliation.candidates)
    candidate_sources.update(roster_reconciliation.candidate_sources)
    roster_observations = roster_reconciliation.observations
    unresolved.extend(
        item for item in roster_reconciliation.unresolved
        if item.get("status") not in {"no_candidates_on_roster_page", "card_selector_missing"}
        or item.get("url") not in manually_covered_urls
    )
    if (not candidates and roster_observations and not unresolved
            and all(item.get("status") == "confirmed_empty" for item in roster_observations)):
        unresolved.append({"url": plan["branches"][0]["url"],
                           "status": "zero_person_delivery_requires_review"})
    for item in verified:
        person = FacultyCandidate(name=item["name"].strip(),
                                  url=item.get("profile_url") or item["source_url"],
                                  title=item.get("title", "教师"))
        candidates.append(person)
        candidate_sources.setdefault((person.name, person.url), set()).add("agent_verified")
    unique = {(cand.name, cand.url): cand for cand in candidates}
    candidates = list(unique.values())
    roster_urls = {url for url, _ in all_pages}
    profile_urls = sorted({cand.url for cand in candidates if cand.url and cand.url not in roster_urls})
    profile_args = {"state": state} if state else {}
    profile_live_evidence = {}
    if roster_reconciliation.profile_content_selectors:
        profile_args.update(profile_content_selectors=roster_reconciliation.profile_content_selectors,
                            profile_content_evidence=profile_live_evidence)
    profile_html, profile_errors = await capture_profiles(
        browser_context, profile_urls, plan.get("profile_concurrency", 4),
        plan.get("profile_ready_selector"), **profile_args)
    unresolved = [item for item in unresolved
                  if item.get("status") != "profile_content_selector_conflict"
                  or item.get("url") not in profile_live_evidence]
    if profile_errors:
        failed_urls = [item["url"] for item in profile_errors if item.get("status") == "profile_navigation_failed"]
        if failed_urls:
            recovered = await asyncio.to_thread(_batch_fetch_profiles_via_native_chrome, failed_urls)
            profile_html.update(recovered)
            if state:
                for url, html in recovered.items():
                    if html:
                        state.save_page("profile", url, html, meta={"browser": "native_chrome"})
            profile_errors = [item for item in profile_errors if item["url"] not in recovered or not recovered.get(item["url"])]
    from server.faculty_identity import resolve_broken_profile_aliases
    aliases = resolve_broken_profile_aliases(
        candidates, profile_html, profile_errors, profile_live_evidence,
        roster_reconciliation.profile_content_sources)
    profile_aliases = []
    for dead, live in aliases.items():
        profile_aliases.append({"broken_url": dead, "verified_url": live,
                                "reason": "repeated_official_roster_link_and_unique_verified_identity",
                                "source_rosters": len({item.get("roster_url") for item in
                                                       roster_reconciliation.profile_content_sources.get(dead, [])})})
        candidates = [candidate for candidate in candidates if candidate.url != dead]
        candidate_sources = {key: value for key, value in candidate_sources.items() if key[1] != dead}
        roster_reconciliation.profile_content_selectors.pop(dead, None)
        profile_html.pop(dead, None)
    if aliases:
        profile_errors = [item for item in profile_errors if item.get("url") not in aliases]
        unresolved = [item for item in unresolved if item.get("url") not in aliases
                      or item.get("status") != "profile_content_selector_conflict"]
    unresolved.extend(profile_errors)
    restored_profiles = sum(url in cached_profile_urls and (
        not roster_reconciliation.profile_content_selectors.get(url)
        or profile_live_evidence.get(url, {}).get("capture_origin") == "checkpoint_revalidated")
        for url in profile_urls)
    # Native-browser fallback may retain observations, but cannot replace a live
    # scope proof. Every declared per-profile boundary must be verified this run.
    for url, pairs in roster_reconciliation.profile_content_selectors.items():
        if len(pairs) == 1 and url not in profile_live_evidence and not any(
                item.get("url") == url and item.get("status") == "profile_content_browser_unverified"
                for item in unresolved):
            unresolved.append({"url": url, "status": "profile_content_browser_unverified"})
    admitted, eligibility, eligibility_errors = classify_candidates(
        candidates, candidate_sources, profile_html, plan)
    unresolved.extend(eligibility_errors)
    name = _safe_name(f"{plan['school']}_{plan['college']}")
    snapshot_dir = out / f"{name}_snapshots"
    snapshot_dir.mkdir(exist_ok=True)
    snapshots = []
    for index, (url, html) in enumerate(navigation_pages, 1):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        path = snapshot_dir / f"navigation_{index:03d}_{digest}.html"
        path.write_text(html, encoding="utf-8")
        snapshots.append({"kind": "navigation", "url": url, "path": str(path)})
    for index, (url, html) in enumerate(all_pages, 1):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        path = snapshot_dir / f"directory_{index:03d}_{digest}.html"
        path.write_text(html, encoding="utf-8")
        snapshots.append({"kind": "directory", "url": url, "path": str(path)})
    for index, (url, html) in enumerate(profile_html.items(), 1):
        if not html:
            continue
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        path = snapshot_dir / f"profile_{index:03d}_{digest}.html"
        path.write_text(html, encoding="utf-8")
        snapshots.append({"kind": "profile", "url": url, "path": str(path)})
    base_url = all_pages[0][0] if all_pages else plan["branches"][0]["url"]
    # Preserve raw captures above; only the parser receives the verified subtree copy.
    from server.profile_content import scope_profile_html
    scoped_profile_html = dict(profile_html)
    profile_scope_evidence = {}
    for url, selectors in roster_reconciliation.profile_content_selectors.items():
        if not profile_html.get(url):
            continue
        if len(selectors) == 1:
            selector, start_selector = next(iter(selectors))
        elif url in profile_live_evidence:
            selector = profile_live_evidence[url]["profile_content_selector"]
            start_selector = profile_live_evidence[url]["profile_content_start_selector"]
        else:
            continue
        scoped, evidence, issue = scope_profile_html(profile_html[url], selector, url,
                                                   start_selector=start_selector)
        if issue:
            unresolved.append(issue)
        else:
            scoped_profile_html[url] = scoped
            profile_scope_evidence[url] = {**evidence,
                "attribution_scope": ("verified_profile_container" if url in profile_live_evidence
                                      else "unverified_profile_container"),
                "selector_sources": roster_reconciliation.profile_content_sources.get(url, []),
                "profile_content_browser_evidence": profile_live_evidence.get(url)}
    from server.image_biography import audit_profile_media, fetch_browser_asset, media_references
    async def fetch_original_media(url: str) -> bytes:
        return await fetch_browser_asset(browser_context, url)

    pdf_roster_urls = set()
    for url, html in all_pages:
        if not any(candidate.url == url for candidate in admitted):
            continue
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("article") or soup.select_one("main") or soup.body or soup
        if any(ref.kind == "pdf" for ref in media_references(str(content), url)):
            pdf_roster_urls.add(url)
    media_pages = dict(scoped_profile_html)
    media_pages.update((url, html) for url, html in all_pages
                       if url in manually_covered_urls or url in image_roster_cards
                       or url in pdf_roster_urls)
    media_owners: dict[str, set[str]] = {}
    for candidate in admitted:
        if candidate.url in media_pages:
            media_owners.setdefault(candidate.url, set()).add(candidate.name)
    production_outputs = (Path(__file__).resolve().parent / "outputs").resolve()
    ocr_cache_dir = (production_outputs / "ocr_cache" if out.is_relative_to(production_outputs)
                     else out / "ocr_cache")
    media_audit = await audit_profile_media(
        media_pages, media_owners, snapshot_dir / "profile_media",
        fetch_asset=fetch_original_media, scoped_urls=set(profile_scope_evidence),
        known_names={candidate.name for candidate in candidates},
        review_decisions_path=out / "image_bio_reviews.json",
        cache_dir=ocr_cache_dir)
    unresolved.extend(media_audit["unresolved"])
    for url, scope_evidence in profile_scope_evidence.items():
        if not scope_evidence["image_only_candidate"]:
            continue
        for candidate_name in media_owners.get(url, set()):
            if (url, candidate_name) not in media_audit["assigned_biographies"]:
                unresolved.append({"url": url, "name": candidate_name,
                                   "status": "image_bio_unreadable_profile"})
    for roster_url, card_count in image_roster_cards.items():
        roster_names = {candidate.name for candidate in admitted if candidate.url == roster_url}
        assigned_names = {name for url, name in media_audit["assigned_biographies"]
                          if url == roster_url}
        image_evidence = [item for item in media_audit["evidence"]
                          if item["profile_url"] == roster_url and item["kind"] == "image"]
        complete = (card_count == len(roster_names) == len(assigned_names)
                    == len(image_evidence) and not any(
                        item.get("profile_url") == roster_url for item in media_audit["unresolved"]))
        if complete:
            unresolved = [item for item in unresolved
                          if not (item.get("url") == roster_url
                                  and item.get("status") == "roster_card_mismatch")]
            for observation in roster_observations:
                if observation["url"] == roster_url:
                    observation.update(extracted_candidates=len(assigned_names),
                                       unaccounted_cards=[], media_reconciled=True)
        else:
            unresolved.append({"url": roster_url, "status": "image_roster_coverage_mismatch",
                               "visible_cards": card_count, "candidate_count": len(roster_names),
                               "assigned_images": len(assigned_names)})
    for candidate in admitted:
        html = profile_html.get(candidate.url)
        if (html and candidate.name not in BeautifulSoup(html, "html.parser")
                .get_text(" ", strip=True).replace(" ", "")
                and candidate.name not in media_audit["biographies"].get(candidate.url, "")):
            unresolved.append({"url": candidate.url, "name": candidate.name,
                               "status": "profile_identity_not_found"})
    extraction = FacultyHarvestPipeline.harvest_candidates(
        admitted, profile_fetcher=lambda url: scoped_profile_html.get(url), base_url=base_url,
        school=plan["school"], college=plan["college"],
        agent_name=plan.get("agent_name", "Browser-assisted Subagent"),
        profileless_urls=roster_urls,
    ) if admitted else {"sheet1_with_email": [], "sheet2_no_email": [],
                          "unresolved": [], "delivery_ready": False}
    unresolved.extend(extraction["unresolved"])
    pipeline_records = extraction["sheet1_with_email"] + extraction["sheet2_no_email"]
    for record in pipeline_records:
        image_bio = media_audit["assigned_biographies"].get((record["主页"], record["姓名"]))
        if image_bio:
            existing = record["简介"].strip()
            record["简介"] = existing + ("\n\n" if existing else "") + image_bio
            parsed_image = FacultyProfileExtractor.extract(
                "<article>" + "".join(f"<p>{escape(line)}</p>" for line in image_bio.splitlines())
                + "</article>", record["主页"], fallback_name=record["姓名"])
            if record["职称"] == "教师" and parsed_image.title != "教师":
                record["职称"] = parsed_image.title
            personal_emails = list(dict.fromkeys(
                email for email in find_emails(image_bio)
                if is_personal_email_for_scholar(email, record["姓名"])))
            if len(personal_emails) > 1 or (personal_emails and record["邮箱"]
                                             and record["邮箱"].lower() != personal_emails[0].lower()):
                from server.image_bio_review import email_conflict_id, resolve_email_conflict
                assigned_evidence = [item for item in media_audit["evidence"]
                                     if item.get("profile_url") == record["主页"]
                                     and item.get("assigned_name") == record["姓名"]]
                choices = list(dict.fromkeys(personal_emails +
                                             ([record["邮箱"]] if record["邮箱"] else [])))
                review_id = email_conflict_id(record["主页"], record["姓名"], image_bio,
                                              assigned_evidence, record["邮箱"], choices)
                selected = resolve_email_conflict(out / "image_bio_email_reviews.json",
                                                  review_id, choices)
                if selected:
                    record["邮箱"] = selected
                    for item in assigned_evidence:
                        item["human_reviewed_email"] = True
                else:
                    unresolved.append({"url": record["主页"], "name": record["姓名"],
                                       "status": "image_bio_email_conflict"})
            elif personal_emails and not record["邮箱"]:
                record["邮箱"] = personal_emails[0]
    profile_owners = Counter(cand.url for cand in admitted)
    for record in pipeline_records:
        url = record["主页"]
        html = scoped_profile_html.get(url)
        if not html or profile_owners[url] > 1:
            continue
        single = FacultyProfileExtractor.extract(html, url, fallback_name=record["姓名"])
        selected_bio = " ".join(record["简介"].split())
        single_bio = " ".join(single.full_bio.split())
        if single_bio and selected_bio and len(single_bio) > len(selected_bio) and selected_bio in single_bio:
            if len(single_bio) - len(selected_bio) > 30:
                unresolved.append({"url": url, "name": record["姓名"],
                                   "status": "profile_body_disagreement",
                                   "selected_chars": len(selected_bio), "single_chars": len(single_bio)})
    fields = ("序号", "姓名", "职称", "邮箱", "学校", "学院", "采集人", "采集日期", "简介", "主页")
    records = [{field: row.get(field, "") for field in fields} for row in pipeline_records]
    from server.faculty_identity import identity_review_issues
    unresolved.extend(identity_review_issues(records))
    from server.harvest_coverage import build_coverage_ledger
    coverage = build_coverage_ledger(plan, source_capture, roster_observations, candidates,
                                     profile_html, eligibility, snapshots)
    unresolved.extend(coverage["issues"])
    coverage["issues"] = list(unresolved)
    coverage["complete"] = coverage["complete"] and not unresolved
    coverage["unresolved_count"] = len(unresolved)
    coverage["field_sources"] = [{"record_index": index, "profile_url": row["主页"],
        "profile_snapshot": next((snap["path"] for snap in snapshots
                                  if snap["kind"] == "profile" and snap["url"] == row["主页"]), None),
        "roster_snapshot_urls": sorted(roster_urls),
        "extraction": ("deterministic_dom_and_local_ocr"
                       if (row["主页"], row["姓名"]) in media_audit["assigned_biographies"]
                       else "deterministic_dom"),
        "attribution_scope": "candidate_and_profile",
        "image_biography_evidence": [item for item in media_audit["evidence"]
                                      if item["profile_url"] == row["主页"]
                                      and item.get("assigned_name") == row["姓名"]],
        **profile_scope_evidence.get(row["主页"], {})} for index, row in enumerate(records, 1)]
    coverage["profile_aliases"] = profile_aliases
    coverage_path = out / f"{name}_coverage.json"
    coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    ready = bool(extraction["delivery_ready"] and not unresolved and coverage["complete"])
    records_path = out / f"{name}_records.json"
    records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    excel_path = None
    if ready:
        excel = out / f"{name}.xlsx"
        excel_path = commit_records(
            records,
            str(excel),
            exporter=excel_export.export_dual_sheet_excel,
        )

    report = {
        "status": "completed" if ready else "needs_agent_review",
        "delivery_ready": ready,
        "school": plan["school"], "college": plan["college"],
        "source_kind": plan.get("source_kind", "university_faculty_roster"),
        "directory_pages": len(all_pages), "navigation_pages": len(navigation_pages),
        "profile_pages": sum(bool(html) for html in profile_html.values()),
        "profile_attempts": len(profile_urls), "elapsed_seconds": round(time.perf_counter() - started, 3),
        "candidate_count": len(candidates), "verified_candidate_count": len(verified),
        "excluded_count": sum(item["status"] == "excluded" for item in eligibility),
        "eligibility": eligibility,
        "verified_candidates": verified, "poster_reviews": list(reviews.values()), "total": len(records),
        "with_email": sum(bool(str(row.get("邮箱", "")).strip()) for row in records),
        "branches": branches, "roster_observations": roster_observations,
        "profile_aliases": profile_aliases,
        "image_biography_evidence": media_audit["evidence"],
        "unresolved": unresolved, "snapshots": snapshots,
        "records_path": str(records_path), "excel_path": excel_path,
        "coverage_scope": "supplied_roster_branches_only",
        "coverage": coverage, "coverage_path": str(coverage_path),
        "checkpoints": {"enabled": state is not None, "plan_digest": state.plan_digest if state else None,
                        "restored_branches": restored_branches, "restored_profiles": restored_profiles,
                        "resumed_branches": resumed_branches, "restored_roster_pages": restored_roster_pages,
                        "evidence_captured_at": [row["captured_at"] for row in state.list_pages("profile")] if state else []},
    }
    audit_path = out / f"{name}_audit.json"
    report["audit_path"] = str(audit_path)
    report["no_email"] = report["total"] - report["with_email"]
    audit_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


LEASE_SECONDS = 300
# A transient renewal failure (busy database, Web restart, timeout) is retried
# at this pace until the stored lease would actually have lapsed.
_TRANSIENT_RENEW_RETRY_SECONDS = 10.0

LeaseRenewer = Callable[[], Awaitable[bool]]


def http_lease_renewer(client: Any, task_id: str, worker_id: str, lease_token: str,
                       lease_seconds: float = LEASE_SECONDS) -> LeaseRenewer:
    """Renew through the local Web API (IDE/CLI path).

    Returns True when renewed and False when ownership is definitively gone
    (409/404 or another client error). Server errors and connection failures
    raise, so the caller retries them instead of abandoning a valid lease.
    """
    async def renew() -> bool:
        response = await client.post(
            f"/api/queue/renew/{quote(task_id, safe='')}",
            json={"worker_id": worker_id, "lease_seconds": lease_seconds, "lease_token": lease_token},
        )
        if response.status_code == 200:
            return True
        if 400 <= response.status_code < 500 and response.status_code not in {408, 429}:
            return False
        raise RuntimeError(f"HTTP {response.status_code}")
    return renew


async def _keep_lease(renew: LeaseRenewer, state: dict[str, Any], *,
                      interval_seconds: float, lease_seconds: float) -> None:
    retry_seconds = min(interval_seconds, _TRANSIENT_RENEW_RETRY_SECONDS)
    delay = interval_seconds
    while True:
        await asyncio.sleep(delay)
        try:
            renewed = await renew()
        except Exception as exc:  # transient: keep trying while the stored lease is still valid
            state["last_transient_error"] = f"{type(exc).__name__}: {exc}"
            if time.monotonic() - state["last_success"] >= lease_seconds:
                state["error"] = f"renewal failed until lease expiry: {state['last_transient_error']}"
                return
            delay = retry_seconds
            continue
        if not renewed:
            state["error"] = "lease no longer owned by worker"
            return
        state["last_success"] = time.monotonic()
        delay = interval_seconds


@contextlib.asynccontextmanager
async def lease_keepalive(renew: LeaseRenewer, *, interval_seconds: float = 60,
                          lease_seconds: float = LEASE_SECONDS):
    """Keep a claimed task's lease alive for the duration of the block.

    Yields a state dict; ``state["error"]`` is set once ownership is lost or
    renewal kept failing until the lease lapsed. A single transient failure no
    longer stops the heartbeat.
    """
    if interval_seconds <= 0 or lease_seconds <= 0:
        raise ValueError("positive renewal interval and lease length required")
    state: dict[str, Any] = {"error": None, "last_success": time.monotonic()}
    keeper = asyncio.create_task(_keep_lease(renew, state, interval_seconds=interval_seconds,
                                             lease_seconds=lease_seconds))
    try:
        yield state
    finally:
        keeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keeper


async def run_with_lease(harvest: Callable[[], Awaitable[dict[str, Any]]], task_id: str,
                         worker_id: str, lease_token: str, *, client: Any = None,
                         renew: LeaseRenewer | None = None,
                         interval_seconds: float = 60,
                         lease_seconds: float = LEASE_SECONDS) -> dict[str, Any]:
    """Renew an owned local queue task while a browser batch is running.

    ``renew`` renews ownership directly (the server worker uses the queue
    database). Without it, ``client`` renews through the local Web API.
    """
    if not task_id or not worker_id or not lease_token or interval_seconds <= 0:
        raise ValueError("task_id, worker_id, lease_token and positive interval required")
    if renew is None:
        if client is None:
            raise ValueError("a lease renewer or local API client is required")
        renew = http_lease_renewer(client, task_id, worker_id, lease_token, lease_seconds)

    async with lease_keepalive(renew, interval_seconds=interval_seconds,
                               lease_seconds=lease_seconds) as lease:
        report = await harvest()
    renewal_error: str | None = lease["error"]
    if renewal_error is None:
        # Confirm ownership before the caller syncs. A transient failure here is
        # not a loss: the stored lease is still valid and sync re-checks it atomically.
        try:
            if not await renew():
                renewal_error = "lease no longer owned by worker"
        except Exception as exc:
            if time.monotonic() - lease["last_success"] >= lease_seconds:
                renewal_error = f"{type(exc).__name__}: {exc}"
    if renewal_error is not None:
        report["status"] = "needs_agent_review"
        report["delivery_ready"] = False
        report["excel_path"] = None
        report.setdefault("unresolved", []).append(
            {"status": "lease_renewal_failed", "error": renewal_error})
        if report.get("audit_path"):
            Path(report["audit_path"]).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


async def _run_cli(args: argparse.Namespace) -> dict[str, Any]:
    from playwright.async_api import async_playwright
    import httpx

    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    _validate_plan(plan)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headed)
        try:
            from server.browser_context import new_browser_context
            context = await new_browser_context(browser)
            try:
                harvest = lambda: run_plan(plan, context, args.output_dir)
                if args.task_id:
                    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=15) as client:
                        return await run_with_lease(
                            harvest, args.task_id, args.worker_id,
                            os.environ["HARVEST_LEASE_TOKEN"], client=client,
                        )
                return await harvest()
            finally:
                await context.close()
        finally:
            await browser.close()


def main() -> None:
    import sys
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Browser-backed college batch selected by a Subagent")
    parser.add_argument("--plan", required=True, help="JSON plan with verified roster branches")
    parser.add_argument("--output-dir", default="server/outputs")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--task-id", help="claimed local queue task ID; requires --worker-id and HARVEST_LEASE_TOKEN")
    parser.add_argument("--worker-id", help="worker that claimed the local queue task")
    args = parser.parse_args()
    if bool(args.task_id) != bool(args.worker_id):
        parser.error("--task-id and --worker-id must be supplied together")
    if args.task_id and not os.environ.get("HARVEST_LEASE_TOKEN"):
        parser.error("HARVEST_LEASE_TOKEN is required for a claimed task")
    result = asyncio.run(_run_cli(args))
    compact = {key: result[key] for key in (
        "status", "delivery_ready", "directory_pages", "profile_pages", "candidate_count",
        "total", "with_email", "excel_path", "records_path", "audit_path",
    )}
    compact["unresolved_count"] = len(result["unresolved"])
    print(json.dumps(compact, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
