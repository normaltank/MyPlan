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

from bs4 import BeautifulSoup, Tag

from server import excel_export, name_review
from server.faculty_extractor import (
    FacultyCandidate, FacultyDirectoryExtractor, FacultyHarvestPipeline, FacultyProfileExtractor,
    find_emails, is_personal_email_for_scholar,
)
from server.harvest_eligibility import classify_candidates
from server.harvest_workflow import capture_profiles, capture_sources, commit_records, reconcile_rosters
from server.source_qualification import (SOURCE_FIELDS, SourceEvidence, captured_source_evidence,
                                         source_claim_valid, verify_source)


def _validate_plan(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        raise ValueError("plan must be a JSON object")
    if not str(plan.get("school", "")).strip() or not str(plan.get("college", "")).strip():
        raise ValueError("plan requires school and college")
    branches = plan.get("branches")
    if not isinstance(branches, list):
        raise ValueError("plan requires a branches array")
    from server.recon_contract import validate_source_dispositions
    from server.page_types import normalize_branch, validate_production_bindings
    plan = {**plan, "branches": [normalize_branch(branch) for branch in branches]}
    branches = plan["branches"]
    for branch in branches:
        validate_production_bindings(branch)
    validate_source_dispositions(plan)
    allowed_sources = {"university_faculty_roster", "hospital_doctor_roster"}
    if plan.get("source_kind") is not None and plan["source_kind"] not in allowed_sources:
        raise ValueError("invalid source_kind")
    for branch in branches:
        if not isinstance(branch, dict):
            raise ValueError("each branch must be an object")
        if not source_claim_valid(branch):
            raise ValueError('each branch requires current source relationship witness selectors')
        if branch.get("kind") == "image_table":
            parsed_image_url = urlparse(str(branch.get("url", "")))
            if (parsed_image_url.scheme not in ("http", "https") or not parsed_image_url.netloc
                    or parsed_image_url.username or parsed_image_url.password
                    or set(branch) - ({"url", "kind", "source_kind", "card_selector", "single_page", "page_types"} | SOURCE_FIELDS)
                    or branch.get("source_kind", "university_faculty_roster") not in allowed_sources):
                raise ValueError("image_table requires a structure-only official URL branch")
            if (branch.get("single_page") is not True or not branch.get("card_selector")
                    or any(key in branch for key in ("next_selector", "initial_click_selector", "child_branch",
                                                    "profile_content_selector", "empty_state_selector"))):
                raise ValueError("image_table requires only a verified single-page image selector")
            if not isinstance(branch["card_selector"], str) or not branch["card_selector"].strip():
                raise ValueError("image_table selector must be non-empty")
            continue
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
            max_children = branch.get("max_children")
            if max_children is not None and (type(max_children) is not int or max_children < 1):
                raise ValueError("max_children must be a positive integer")
            if ('child_branch' in branch) == ('child_branches' in branch):
                raise ValueError('navigation requires exactly one child binding form')
            if 'child_branches' in branch:
                pagination = branch['page_types']['pagination']
                if (branch.get('next_selector') or pagination['type_id'] != 'pagination.single_page'
                        or pagination['implementation_version'] != 1):
                    raise ValueError('explicit navigation pagination is not applicable; re-identify a supported entrance')
                children = branch['child_branches']
                if not isinstance(children, list) or not children or any(not isinstance(child, dict) for child in children):
                    raise ValueError('child_branches must be a nonempty list of complete branches')
                _validate_plan({**plan, 'branches': children, 'source_dispositions': []})
            else:
                child = branch.get("child_branch")
                if not isinstance(child, dict) or "url" in child:
                    raise ValueError("navigation branch requires a child_branch without url")
                if not source_claim_valid(child):
                    raise ValueError('navigation children require their own source witness selectors')
                if "empty_state_selector" in child or "roster_container_selector" in child:
                    raise ValueError("empty selectors are not supported in navigation children")
                if child.get("source_kind") is not None and child["source_kind"] not in allowed_sources:
                    raise ValueError("invalid child source_kind")
                if bool(child.get("single_page")) == bool(child.get("next_selector")):
                    raise ValueError("child_branch needs exactly one of single_page or next_selector")
                if not str(child.get("card_selector", "")).strip():
                    raise ValueError("child_branch requires card_selector")
            continue
        if branch.get("kind") == "segmented":
            from server.segmented_roster import branch_valid
            if not branch_valid(branch):
                raise ValueError("segmented branch requires container_selector, record_start_selector "
                                 "and exactly one of single_page or next_selector, without card selectors")
            max_pages = branch.get("max_pages")
            if max_pages is not None and (type(max_pages) is not int or max_pages < 1):
                raise ValueError("max_pages must be a positive integer")
            continue
        if branch.get("kind", "roster") != "roster":
            raise ValueError("branch kind must be roster, navigation or segmented")
        if bool(branch.get("single_page")) == bool(branch.get("next_selector")):
            raise ValueError("each branch needs exactly one of single_page or next_selector")
        if "card_selector" in branch and not str(branch["card_selector"]).strip():
            raise ValueError("card_selector must be non-empty")
        max_pages = branch.get("max_pages")
        if max_pages is not None and (type(max_pages) is not int or max_pages < 1):
            raise ValueError("max_pages must be a positive integer")
    concurrency = plan.get("profile_concurrency", 4)
    if not isinstance(concurrency, int) or not 1 <= concurrency <= 8:
        raise ValueError("profile_concurrency must be an integer from 1 to 8")
    if "profile_ready_selector" in plan and not str(plan["profile_ready_selector"]).strip():
        raise ValueError("profile_ready_selector must be non-empty")
    # Same rule as the server recon plan: names come from captured pages by
    # deterministic code (DOM, PDF text layer or local OCR), never from a plan.
    manual = sorted(key for key in ("verified_candidates", "poster_reviews", 'verified_appointments',
                                   'reviewed_exclusions') if key in plan)
    if manual:
        raise ValueError(f"plan fields {manual} are no longer accepted: roster names must be read "
                         "from the captured page by DOM, PDF or local OCR; an image roster that "
                         "cannot be read stays unresolved")
    # Shared public structure contract also closes arbitrary renamed teacher
    # JSON channels. The local operator label is the only non-model metadata.
    from pydantic import ValidationError
    from server.recon_contract import Plan
    try:
        Plan.model_validate({key: value for key, value in plan.items() if key != 'agent_name'})
    except ValidationError as exc:
        raise ValueError('plan must contain only supported structure fields and source witness selectors') from exc


def _ocr_cache_dir(out: Path) -> Path:
    production_outputs = (Path(__file__).resolve().parent / "outputs").resolve()
    return (production_outputs / "ocr_cache" if out.is_relative_to(production_outputs)
            else out / "ocr_cache")


class _PollingExecutor:
    """Bypass the persistent wrapper cache for transient AJAX snapshots."""

    queue = None

    def execute(self, _html: str, _url: str) -> tuple[list, str]:
        return [], "miss"


def _roster_signature(html: str, url: str) -> tuple:
    found = FacultyDirectoryExtractor.extract(html, url, executor=_PollingExecutor())
    return tuple((cand.name, cand.url, cand.title) for cand in found)


_BANNER_TOKENS = re.compile(r"slide|carous|banner|swiper", re.I)


def _is_banner_arrow(control: Tag, roster_nodes: list[Tag]) -> bool:
    """A script-only arrow whose nearby slide box holds images and no roster card.

    Real pagers are never matched: they sit in a pager container or carry a
    navigable href, and both are checked before this is consulted.
    """
    slider = control.parent
    for _ in range(3):
        if slider is None or getattr(slider, "name", None) in {None, "body", "html", "main", "[document]"}:
            return False
        tokens = " ".join(slider.get("class", [])) + " " + str(slider.get("id", ""))
        if _BANNER_TOKENS.search(tokens):
            break
        slider = slider.parent
    else:
        return False
    touches_roster = any(node is slider or slider in node.parents or node in slider.parents
                         for node in roster_nodes)
    if touches_roster:
        return False
    if len(slider.find_all("img")) >= 2:
        return True
    # One slide is still an image component. Require its paired script-only
    # arrows and media-only content, rather than relaxing the image threshold.
    arrows = slider.find_all('a', recursive=False)
    classes = {token for arrow in arrows for token in arrow.get('class', [])}
    frames = slider.select(':scope > .bd > ul > li')
    return (len(slider.find_all('img')) == 1 and {'prev', 'next'} <= classes
            and len(frames) == 1 and len(frames[0].find_all('img')) == 1
            and slider.select_one(':scope > .hd') is not None
            and all(not arrow.get('href', '').strip() or arrow['href'].strip().startswith(('#', 'javascript:'))
                    for arrow in arrows)
            and bool(re.fullmatch(r'[\d\s.·•]*', slider.get_text('', strip=True))))


def _in_pager(control: Tag) -> bool:
    from server.pagination_identity import pager_ancestor
    return pager_ancestor(control) is not None


def _banner_arrow_outside_roster(control: Tag, roster_nodes: list[Tag]) -> bool:
    href = control.get("href", "").strip()
    return bool(roster_nodes and (not href or href.startswith(("#", "javascript:")))
                and _is_banner_arrow(control, roster_nodes))


def without_banner_arrows(html: str, card_selector: str, pagination: dict) -> dict:
    """Recon's pagination candidates under this module's banner rule.

    The recon gate and the harvest must agree on what a pager is; otherwise a
    scout is asked to prove pagination the harvest would never follow.
    """
    items = pagination.get("items")
    if not isinstance(items, list) or not items:
        return pagination
    soup = BeautifulSoup(html, "html.parser")
    try:
        roster_nodes = soup.select(card_selector)
    except Exception:
        return pagination
    kept = []
    for item in items:
        try:
            nodes = soup.select(item.get("selector") or "")
        except Exception:
            nodes = []
        if not (len(nodes) == 1 and not _in_pager(nodes[0])
                and _banner_arrow_outside_roster(nodes[0], roster_nodes)):
            kept.append(item)
    dropped = len(items) - len(kept)
    return {**pagination, "items": kept, "total": pagination["total"] - dropped,
            **({"banner_arrows_ignored": dropped} if dropped else {})}


def _pagination_candidates(html: str, current_url: str,
                           card_selector: str | None = None, ignored_controls=()) -> list[str | None]:
    """Find bounded pagination leads from browser-rendered structure, not names."""
    soup = BeautifulSoup(html, "html.parser")
    roster_nodes = soup.select(card_selector) if card_selector else []
    ignored = [node for selector in ignored_controls for node in soup.select(selector)]
    leads: list[str | None] = []
    for control in soup.select('a, button, input[type="button"], input[type="submit"], [role="button"]'):
        if any(control is node for node in ignored):
            continue
        if ((control.name in {'button', 'input'} and control.has_attr("disabled"))
                or control.get("aria-current") == "page"):
            continue
        slider = control.parent
        # Only a fully rendered slide component outside the verified roster
        # proves that this arrow advances a banner. A "next" class alone does
        # not, and teacher cards inside a slider still need pagination proof.
        if (card_selector and roster_nodes and control.name == "button"
                and {"slick-next", "slick-prev"}.intersection(control.get("class", []))
                and {"slick-slider", "slick-initialized"}.issubset(slider.get("class", []))):
            track = slider.select_one(":scope > .slick-list > .slick-track")
            slides = track.find_all(recursive=False) if track else []
            contains_roster = any(node is slider or any(parent is slider for parent in node.parents)
                                  for node in roster_nodes)
            if (len(slides) >= 2 and all("slick-slide" in node.get("class", []) for node in slides)
                    and not contains_roster):
                continue
        rel = control.get("rel", [])
        is_next = "next" in rel
        control_classes = " ".join(control.get("class", []))
        is_next = is_next or bool(re.search(r"(?:^|[-_\s])(?:next|nextpage)(?:$|[-_\s])",
                                             control_classes, re.I))
        label = (control.get_text(" ", strip=True) or control.get("value")
                 or control.get("aria-label") or control.get("title") or "").strip()
        is_next = is_next or bool(re.fullmatch(
            r"(?:下一页|下页|后一页|更多(?:人员|教师|师资)|加载更多(?:人员|教师|师资)?|"
            r"next(?:\s+page)?|(?:load|show)\s+more(?:\s+(?:people|faculty|teachers))?)",
            label, re.I))
        in_pager = _in_pager(control)
        if not (is_next or in_pager):
            continue
        href = control.get("href", "").strip()
        if not in_pager and _banner_arrow_outside_roster(control, roster_nodes):
            continue
        target = urljoin(current_url, href) if href and not href.startswith(("#", "javascript:")) else None
        if target == current_url:
            target = None  # Same-URL controls can update the roster through AJAX.
        leads.append(target)
        if len(leads) >= 8:
            break
    return leads


def single_page_evidence(html: str, url: str, card_selector: str, ignored_controls=()) -> dict:
    """Observed completion, separate from the scout's bounded candidate count.

    Inspect the saved rendered document including input/value and labelled
    forward controls. Only independently proven terminal/banner controls are
    ignored. This is browser-produced evidence, never a field in a model plan.
    """
    leads = _pagination_candidates(html, url, card_selector, ignored_controls)
    return {"url": url, "unit_selector": card_selector,
            "source_sha256": hashlib.sha256(html.encode()).hexdigest(),
            "confirmed": not leads, "unresolved_forward_controls": len(leads),
            "basis": "rendered_forward_controls"}


async def _probe_single_page(context: Any, html: str, url: str, card_selector: str,
                             ignored_controls=()) -> list[dict]:
    """Fail closed if an asserted single page has an unexplained pager lead."""
    if not card_selector:
        return []  # roster reconciliation handles this; reviewed posters are separate
    baseline = tuple(card.get_text(" ", strip=True) for card in BeautifulSoup(html, "html.parser").select(card_selector))
    unresolved: list[dict] = []
    for target in _pagination_candidates(html, url, card_selector, ignored_controls):
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


def _unit_signature(html: str, url: str, selector: str) -> tuple:
    """What each verified card shows: its text, its links, and for a card
    without text (an image roster) the images it declares.

    Attribute churn is not a page turn: lazy loaders copy data-src into src,
    so the declared source is read first.
    """
    signature = []
    for card in BeautifulSoup(html, "html.parser").select(selector):
        if any(str(node.get('aria-busy', '')).lower() == 'true'
               for node in [card, *card.parents] if isinstance(node, Tag)):
            return ()
        anchors = ([card] if card.name == "a" else []) + card.find_all("a", href=True)
        links = tuple(urljoin(url, a["href"]) for a in anchors if a.get("href"))
        text = card.get_text(" ", strip=True)
        images = () if text else tuple(
            img.get("data-src") or img.get("data-original") or img.get("src") or ""
            for img in ([card] if card.name == "img" else card.find_all("img")))
        if not text and not links and not any(images):
            return ()
        signature.append((text, links, images))
    return tuple(signature)


async def _wait_for_change(page: Any, before: str, url: str, seconds: float = 3.5, *,
                           unit_selector: str | None = None) -> str | None:
    """Wait for a changed roster, ignoring unrelated clocks and page chrome.

    With a verified card (or segment start) selector the cards decide: the
    name miner cannot read every page (rare surnames await review), and an
    empty reading must not stop pagination as 'unchanged'.

    A control that is a link opens a new document. Its load time is the
    site's, not the roster's: the wait starts once that document has loaded,
    as a direct visit to the page would.
    """
    if unit_selector:
        def signature(html: str, current_url: str) -> tuple:
            return _unit_signature(html, current_url, unit_selector)
    else:
        signature = _roster_signature
    previous = signature(before, url)
    deadline = asyncio.get_running_loop().time() + seconds
    pending, stable_since = None, None
    navigated = False
    while asyncio.get_running_loop().time() < deadline:
        if not navigated and page.url != url:
            navigated = True
            with contextlib.suppress(Exception):
                await page.wait_for_load_state("domcontentloaded", timeout=30000)
            deadline = asyncio.get_running_loop().time() + seconds
        try:
            current = await page.content()
        except Exception:  # the new document is still replacing the old one
            await asyncio.sleep(0.1)
            continue
        sig = signature(current, page.url)
        if current != before and sig and sig != previous:
            if not unit_selector:
                return current
            now = asyncio.get_running_loop().time()
            if sig != pending:
                pending, stable_since = sig, now
            elif now - stable_since >= 0.2:
                return current
        else:
            pending, stable_since = None, None
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
# Chrome writes DevToolsActivePort once its fresh profile is initialised. That
# takes seconds on a loaded or small machine (three concurrent cold starts on a
# 2-core runner took over 15s), so a live process is waited for; the ceiling
# only stops a browser that never becomes ready.
_NATIVE_CHROME_START_SECONDS = 90


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
    deadline = time.monotonic() + _NATIVE_CHROME_START_SECONDS
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
    from server.site_limits import wait_site_turn
    wait_site_turn(url)
    started = time.monotonic()
    proc, user_dir, port = _launch_native_chrome(chrome_exe, [url])
    try:
        if port is None:
            return None
        pages = _collect_native_pages(port, started, settle_seconds=5)
        return _native_page_or_none(url, next(iter(pages.values()), None))
    except Exception:
        return None
    finally:
        _stop_native_chrome(proc, user_dir)


def _native_page_or_none(url: str, html: str | None) -> str | None:
    """A rate-limit notice is not the page: pause the site and report nothing."""
    from server.site_limits import SiteRateLimited, check_document
    if html is None:
        return None
    try:
        check_document(url, html)
    except SiteRateLimited:
        return None
    return html


def _batch_fetch_profiles_via_native_chrome(urls: list[str], batch_size: int = 8) -> dict[str, str]:
    if _native_chrome_fallback_disabled():
        # A fixture-backed context has no profiles to recover; launching real
        # Chrome here used to open visible windows against live hospital pages.
        return {}
    chrome_exe = _native_chrome_executable()
    if chrome_exe is None:
        return {}
    from server.site_limits import site_is_paced, wait_site_turn
    if any(site_is_paced(url) for url in urls):
        batch_size = 1  # tabs of one launch all load at once; keep a paced site's pace
    results: dict[str, str] = {}
    for i in range(0, len(urls), batch_size):
        chunk = urls[i:i + batch_size]
        for url in chunk:
            wait_site_turn(url)
        started = time.monotonic()
        proc, user_dir, port = _launch_native_chrome(chrome_exe, chunk)
        try:
            if port is not None:
                results.update({url: html for url, html in
                                _collect_native_pages(port, started, settle_seconds=6).items()
                                if _native_page_or_none(url, html) is not None})
        except Exception:
            pass
        finally:
            _stop_native_chrome(proc, user_dir)
    return results


def _unit_selector(branch: dict) -> str:
    """The verified selector for one record: a card, or a segment start."""
    return branch.get("card_selector") or branch.get("record_start_selector") or ""


def _frontier_signature(html: str, branch: dict) -> tuple | None:
    """Compare the verified source and control DOM, ignoring unrelated chrome."""
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(_unit_selector(branch)) if _unit_selector(branch) else []
    if not cards:
        return None
    controls = soup.select(branch["next_selector"]) if branch.get("next_selector") else []
    return tuple(str(card) for card in cards), tuple(str(control) for control in controls)


def _restorable_prefix(branch: dict, pages: list[dict]) -> bool:
    if not pages or branch.get("single_page") or branch.get("initial_click_selector"):
        return False
    host = urlparse(branch["url"]).netloc.lower()
    urls = [row["url"] for row in pages]
    return ((branch.get("max_pages") is None or len(urls) <= branch["max_pages"]) and len(set(urls)) == len(urls)
            and all(urlparse(url).scheme in {"http", "https"}
                    and urlparse(url).netloc.lower() == host for url in urls))


async def _capture_branch(context: Any, branch: dict[str, Any], *, on_page=None,
                          resume_pages=None, source_target=None) -> tuple[list[tuple[str, str]], list[dict], dict]:
    page = await context.new_page()
    from server.browser_readiness import install_request_tracking
    tracker = install_request_tracking(page)
    url = branch["url"]
    captured: list[tuple[str, str]] = []
    unresolved: list[dict] = []
    evidence = {"url": url, "visited_pages": 0, "next_clicks": 0, "end": "unresolved",
                "restored_pages": 0}
    from server.pagination_identity import next_identity, same_identity, displaced_controls
    forward_identity = None
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
                forward_identity = next_identity(saved[0]['html'],_unit_selector(branch),branch['next_selector'])
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
            if await _wait_for_change(page, before, page.url,
                                      unit_selector=_unit_selector(branch) or None) is None:
                unresolved.append({"url": url, "status": "initial_control_unchanged", "selector": selector})
                return captured, unresolved, evidence

        page_number = start_page
        seen_frontiers = {(row["url"], _frontier_signature(row["html"], branch)) for row in saved[:-1]} if evidence["restored_pages"] else set()
        while True:
            if branch.get("empty_state_selector"):
                await page.wait_for_load_state("networkidle", timeout=2000)
            else:
                # Scripts may insert the pager or cards after DOMContentLoaded;
                # read the DOM only once it settles, as the resume path does.
                with contextlib.suppress(Exception):
                    await page.wait_for_load_state("networkidle", timeout=3000)
            html = await page.content()
            if source_claim_valid(branch):
                captured_source = {}
                try:
                    source_proof = await verify_source(page, branch, source_target or {}, local_capture=captured_source)
                except Exception as exc:
                    # A failed purpose proof cannot erase an already readable
                    # source. Keep its native observations and the repair blocker.
                    source_proof = {'url': page.url, 'confirmed': False, 'reason': 'source_verification_failed',
                                    'error_type': type(exc).__name__}
                    unresolved.append({'url': page.url, 'status': 'source_role_evidence_unverified',
                                       'reason': 'source_verification_failed'})
                evidence.setdefault('source_roles', {})[page.url] = source_proof
                evidence.setdefault('related_source_originals', []).extend(captured_source.get('related_sources', []))
                html = captured_source.get('html') or html
            if branch.get("kind") == "image_table":
                from server.image_table_browser import verify_table_images
                proof = await verify_table_images(page, branch["card_selector"])
                evidence.setdefault("table_images", {})[page.url] = proof
                if not proof["valid"]:
                    unresolved.append({"url": page.url, "status": "table_image_selector_unverified"})
                html = await page.content()
            frontier = (page.url, _frontier_signature(html, branch))
            if frontier[1] is not None and frontier in seen_frontiers:
                unresolved.append({"url": page.url, "status": "pagination_cycle", "page": page_number})
                break
            seen_frontiers.add(frontier)
            if not (evidence["restored_pages"] and page_number == start_page):
                captured.append((page.url, html))
                if on_page:
                    on_page(page.url, html, page_number - 1)
            evidence["visited_pages"] = page_number
            if branch.get("single_page"):
                from server.pager_evidence import terminal_pagers, proof_valid
                proofs = await terminal_pagers(page, _unit_selector(branch))
                if any(p.get('reason') == 'pager_state_changed' for p in proofs):
                    unresolved.append({'url': url, 'status': 'single_page_pagination_unverified'})
                    evidence['end'] = 'single_page_pagination_unverified'
                    return captured, unresolved, evidence
                ignored = [p['control_selector'] for p in proofs
                           if proof_valid(p, html, _unit_selector(branch))]
                if proofs:
                    evidence['terminal_pagers'] = proofs
                evidence['pagination_termination'] = single_page_evidence(
                    html, page.url, _unit_selector(branch), ignored)
                probe_errors = await _probe_single_page(context, html, page.url,
                                                         _unit_selector(branch), ignored)
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
            if forward_identity is not None and count==0:
                visited_urls = {source_url for source_url, _ in captured}
                displaced = displaced_controls(html,_unit_selector(branch),forward_identity,page.url,
                    visited_urls=visited_urls if len(visited_urls) == len(captured) else None)
                if displaced:
                    unresolved.append({'url':page.url,'page':page_number,'status':'pagination_not_verified',
                        'reason':'next_control_moved_outside_selector','selector':next_selector,
                        'sample_urls':[target for target in displaced if target]})
                    evidence['end']='next_selector_not_verified'
                    evidence['pagination_identity_failure']={'source_sha256':hashlib.sha256(html.encode()).hexdigest(),
                        'page':page_number,'planned_control_matches':0,'displaced_control_count':len(displaced)}
                    break
            if count == 0 and page_number == 1:
                # Recon verified exactly one next control on the entry page; its
                # absence here is unproven, not the end of the roster.
                unresolved.append({"url": page.url, "page": page_number,
                                   "status": "pagination_not_verified", "selector": next_selector})
                break
            if count == 0:
                evidence['pagination_termination'] = {
                    'confirmed': True, 'url': page.url, 'unit_selector': _unit_selector(branch),
                    'source_sha256': hashlib.sha256(html.encode()).hexdigest(),
                    'basis': 'verified_forward_control_absent', 'next_selector': next_selector,
                    'forward_identity': forward_identity}
                evidence["end"] = "next_control_absent"
                break
            if count != 1:
                unresolved.append({"url": page.url, "page": page_number,
                                   "status": "next_control_not_unique", "selector": next_selector})
                break
            current_identity = next_identity(html,_unit_selector(branch),next_selector)
            if current_identity is None or current_identity.get('binding_count') != 1:
                unresolved.append({'url':page.url,'page':page_number,'status':'pagination_not_verified',
                    'reason':'next_control_binding_ambiguous','selector':next_selector})
                evidence['end']='next_selector_not_verified'
                break
            if forward_identity is None:
                forward_identity = current_identity
            elif not same_identity(forward_identity,current_identity):
                unresolved.append({'url':page.url,'page':page_number,'status':'pagination_not_verified',
                    'reason':'next_control_identity_changed','selector':next_selector})
                evidence['end']='next_selector_not_verified'
                break
            if not await next_control.is_enabled():
                if not await next_control.evaluate("node => node.matches(':disabled')"):
                    unresolved.append({'url': page.url, 'status': 'pagination_not_verified',
                        'reason': 'forward_control_not_natively_disabled', 'selector': next_selector})
                    evidence['end'] = 'next_control_not_verified'
                    break
                evidence['pagination_termination'] = {
                    'confirmed': True, 'url': page.url, 'unit_selector': _unit_selector(branch),
                    'source_sha256': hashlib.sha256(html.encode()).hexdigest(),
                    'basis': 'native_disabled_forward_control', 'next_selector': next_selector,
                    'forward_identity': current_identity}
                evidence["end"] = "next_control_disabled"
                break
            if branch.get("max_pages") is not None and page_number == branch["max_pages"]:
                unresolved.append({"url": page.url, "page": page_number,
                                   "status": "page_limit_reached", "selector": next_selector})
                break
            await next_control.click()
            evidence["next_clicks"] += 1
            if await _wait_for_change(page, html, page.url,
                                      unit_selector=_unit_selector(branch) or None) is None:
                unresolved.append({"url": page.url, "page": page_number,
                                   "status": "pagination_unchanged", "selector": next_selector})
                break
            page_number += 1
    except Exception as exc:
        if branch.get("single_page"):
            native_html = await asyncio.to_thread(_fetch_page_via_native_chrome, url)
            if native_html and len(native_html) > 1000:
                captured.append((url, native_html))
                if on_page:
                    on_page(url, native_html, 0)
                evidence["visited_pages"] = 1
                probe_errors = await _probe_single_page(context, native_html, url,
                                                         _unit_selector(branch))
                evidence['pagination_termination'] = single_page_evidence(native_html, url, _unit_selector(branch))
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
        # Child links a script inserts after DOMContentLoaded must be counted.
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("networkidle", timeout=3000)
        html = await page.content()
        evidence.update(discovery_scope="rendered_navigation_state",
                        source_html_sha256=hashlib.sha256(html.encode("utf-8")).hexdigest())
        if 'child_branches' in branch:
            proof = single_page_evidence(html, page.url, branch['child_link_selector'])
            evidence['pagination_termination'] = proof
            evidence['navigation_termination_verified'] = proof['confirmed'] and not branch.get('next_selector')
            if not evidence['navigation_termination_verified']:
                errors.append({'url': url, 'status': 'single_page_pagination_unverified',
                               'reason': 'navigation_parent_range_unverified'})
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
        if branch.get("max_children") is not None and len(links) > branch["max_children"]:
            errors.append({"url": url, "status": "child_limit_reached",
                           "discovered": len(links), "limit": branch.get("max_children")})
            links = links[:branch["max_children"]]
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


async def _capture_sources_with_state(plan, browser_context, state):
    restored_branches = 0
    resumed_branches = 0
    restored_roster_pages = 0

    async def capture_branch(context, branch):
        nonlocal restored_branches, resumed_branches, restored_roster_pages
        from server.page_types import requires_stage_evidence, stage_evidence_for_result
        key = json.dumps(branch, sort_keys=True, separators=(",", ":"))
        bundle = state.load_page_record("branch", branch["url"], key=key) if state and not branch.get("source_role") and not branch.get("empty_state_selector") and branch.get("kind") != "image_table" and not requires_stage_evidence('pagination', branch) else None
        if bundle:
            # A valid HTML digest alone cannot establish a completed branch.
            # Treat incomplete/corrupt cursor metadata as a cache miss; errors
            # from the new browser capture below must still propagate normally.
            refs = bundle["meta"].get("pages")
            proof = bundle["meta"].get("evidence")
            terminals = ({"single_page_no_pagination_control_seen"} if branch.get("single_page")
                         else {"next_control_absent", "next_control_disabled"})
            valid = (isinstance(refs, list) and 1 <= len(refs)
                     and (branch.get("max_pages") is None or len(refs) <= branch["max_pages"])
                     and isinstance(proof, dict) and proof.get("url") == branch["url"]
                     and isinstance(proof.get("end"), str)
                     and proof.get("end") in terminals
                     and type(proof.get("visited_pages")) is int and proof["visited_pages"] == len(refs)
                     and (not branch.get("single_page") or len(refs) == 1))
            saved = []
            if valid:
                for index, ref in enumerate(refs):
                    if (not isinstance(ref, dict) or set(ref) != {"url", "key"}
                            or not isinstance(ref["url"], str) or not ref["url"]
                            or ref["key"] != key + f"|{index}"):
                        valid = False
                        break
                    record = state.load_page_record("roster", ref["url"], key=ref["key"])
                    if (record is None or record["meta"].get("branch_url") != branch["url"]
                            or type(record["meta"].get("sequence")) is not int
                            or record["meta"]["sequence"] != index):
                        valid = False
                        break
                    saved.append(record)
            if valid and bundle["sha256"] == saved[0]["sha256"] and branch.get('single_page'):
                restored_branches += 1
                evidence = {**proof, "restored": True,
                            "captured_at": bundle["captured_at"]}
                return [(row["url"], row["html"]) for row in saved], [], evidence
        def checkpoint(url, html, sequence):
            state.save_roster_progress(branch, url, html, sequence)
        kwargs = {"on_page": checkpoint, "resume_pages": state.load_roster_progress(branch)
                  if branch.get("kind") != "image_table" and not branch.get('source_role') else []} if state else {}
        from server.page_types import invoke_async
        pages, errors, evidence = await invoke_async(
            'pagination', branch, _capture_branch, context, branch, source_target=plan, **kwargs)
        evidence['executed_page_type_roles'] = ['pagination']
        if requires_stage_evidence('pagination', branch):
            try:
                evidence['stage_evidence'] = await stage_evidence_for_result(
                    'pagination', branch, (pages, errors, evidence), requested_url=branch['url'])
            except ValueError:
                errors.append({'url': branch['url'], 'status': 'pagination_stage_evidence_unverified'})
        if evidence.get('url') != branch['url'] or not pages and not errors:
            errors.append({'url': branch['url'], 'status': 'page_type_output_invalid',
                           'reason': 'pagination_output_scope_or_pages_missing'})
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
    return source_capture, {"restored_branches": restored_branches, "resumed_branches": resumed_branches,
                            "restored_roster_pages": restored_roster_pages}


def _save_capture_snapshots(out, name, navigation_pages, all_pages, profile_html):
    snapshot_dir = out / f"{name}_snapshots"
    snapshot_dir.mkdir(exist_ok=True)
    snapshots = []
    for index, (url, html) in enumerate(navigation_pages, 1):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        path = snapshot_dir / f"navigation_{index:03d}_{digest}.html"
        path.write_bytes(html.encode('utf-8'))
        snapshots.append({"kind": "navigation", "url": url, "path": str(path)})
    for index, (url, html) in enumerate(all_pages, 1):
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        path = snapshot_dir / f"directory_{index:03d}_{digest}.html"
        path.write_bytes(html.encode('utf-8'))
        snapshots.append({"kind": "directory", "url": url, "path": str(path)})
    for index, (url, html) in enumerate(profile_html.items(), 1):
        if not html:
            continue
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
        path = snapshot_dir / f"profile_{index:03d}_{digest}.html"
        path.write_bytes(html.encode('utf-8'))
        snapshots.append({"kind": "profile", "url": url, "path": str(path)})
    return snapshot_dir, snapshots


def _scope_captured_profiles(profile_html, selectors_by_url, profile_live_evidence, selector_sources,
                             candidates=(), *, blocked_profile_urls=()):
    unresolved = []
    from server.profile_content import scope_profile_html
    scoped_profile_html = dict(profile_html)
    profile_scope_evidence = {}
    for url in blocked_profile_urls:
        scoped_profile_html[url] = ''
        if url not in selectors_by_url or not profile_html.get(url):
            unresolved.append({'url': url, 'status': 'profile_content_not_verified'})
    for url, selectors in selectors_by_url.items():
        if not profile_html.get(url):
            continue
        if len(selectors) == 1:
            selector, start_selector = next(iter(selectors))
        elif url in profile_live_evidence:
            selector = profile_live_evidence[url]["profile_content_selector"]
            start_selector = profile_live_evidence[url]["profile_content_start_selector"]
        else:
            scoped_profile_html[url] = ''
            unresolved.append({'url': url, 'status': (
                'profile_content_selector_conflict' if len(selectors) > 1 else 'profile_content_selector_invalid')})
            continue
        scoped, evidence, issue = scope_profile_html(profile_html[url], selector, url,
                                                   start_selector=start_selector)
        if issue and issue['status'] == 'profile_content_selector_empty':
            from server.empty_profile import EmptyProfileEvidence
            empty = EmptyProfileEvidence.verify(url, profile_html[url], selector,
                profile_live_evidence.get(url), candidates, selector_sources.get(url, []))
            # Keep the raw capture for review; never let a known empty subtree
            # turn into the full page's navigation in the observed record.
            scoped_profile_html[url], _, _ = scope_profile_html(profile_html[url], selector, url, allow_empty=True)
            if empty is not None:
                scoped, evidence, issue = scope_profile_html(profile_html[url], selector, url, allow_empty=True)
                evidence['empty_profile_evidence'] = empty.audit()
            else:
                issue['reason'] = 'empty_profile_evidence_unverified'
                issue['browser_evidence'] = profile_live_evidence.get(url)
        if issue:
            unresolved.append(issue)
            # The full failed page is retained in originals/snapshots for review.
            # It must not become parser/OCR input when the requested scope failed.
            scoped_profile_html[url] = ''
        elif url in blocked_profile_urls:
            unresolved.append({'url': url, 'status': 'profile_content_not_verified'})
            scoped_profile_html[url] = ''
        else:
            scoped_profile_html[url] = scoped
            profile_scope_evidence[url] = {**evidence,
                "attribution_scope": ("verified_profile_container" if url in profile_live_evidence
                                      else "unverified_profile_container"),
                "selector_sources": selector_sources.get(url, []),
                "profile_content_browser_evidence": profile_live_evidence.get(url)}
    return scoped_profile_html, profile_scope_evidence, unresolved


def _enrich_extracted_records(pipeline_records, segment_records, media_audit, out, scoped_profile_html, admitted,
                              verified_profile_urls=(), title_field_evidence=None):
    from server.detail_mecc import explicit_title_fields, declared_dom_title_fields
    from server.titles import select_title
    unresolved = []

    def text_fields(record, segment):
        # Use only this person's already scoped DOM. A shared page's other
        # profiles must never donate their explicit fields to this record.
        key = (record['主页'], record['姓名'])
        if (title_field_evidence is not None and key in title_field_evidence
                and (segment is None or title_field_evidence[key])):
            return title_field_evidence[key]
        sole_owner = (record['主页'] in verified_profile_urls
                      and sum(candidate.url == record['主页'] for candidate in admitted) == 1)
        html = (getattr(segment.segment, 'html', '') if segment is not None else
                scoped_profile_html.get(record['主页'], '') if sole_owner else '')
        if html:
            return declared_dom_title_fields(BeautifulSoup(html, 'lxml'), preserve_content=True)
        text = segment.segment.text if segment is not None else record['简介']
        return explicit_title_fields(text)

    for record in pipeline_records:
        segment = segment_records.get((record["主页"], record["姓名"]))
        if segment is None:
            continue
        # The record's own article text, verbatim; an image-named record keeps
        # document order, so its resume text is placed before the text below it.
        record["简介"] = segment.segment.text if segment.name_source != "ocr" else ""
        title_choice = select_title(explicit_fields=text_fields(record, segment),
                                    card_title=record['职称'], body_title=segment.title)
        record['职称'] = title_choice.title
        if title_choice.conflicts:
            unresolved.append({'url': record['主页'], 'name': record['姓名'],
                               'status': 'title_field_conflict', 'matches': list(title_choice.conflicts)})
        if segment.email and not record["邮箱"]:
            record["邮箱"] = segment.email
    for record in pipeline_records:
        image_bio = media_audit["assigned_biographies"].get((record["主页"], record["姓名"]))
        if image_bio:
            existing = record["简介"].strip()
            segment = segment_records.get((record['主页'], record['姓名']))
            existing_fields = text_fields(record, segment)
            record["简介"] = existing + ("\n\n" if existing else "") + image_bio
            parsed_image = FacultyProfileExtractor.extract(
                "<article>" + "".join(f"<p>{escape(line)}</p>" for line in image_bio.splitlines())
                + "</article>", record["主页"], fallback_name=record["姓名"])
            title_choice = select_title(explicit_fields=(*existing_fields, *explicit_title_fields(image_bio)),
                                        card_title=record['职称'], body_title=parsed_image.title)
            record['职称'] = title_choice.title
            if title_choice.conflicts:
                unresolved.append({'url': record['主页'], 'name': record['姓名'],
                                   'status': 'title_field_conflict', 'matches': list(title_choice.conflicts)})
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
    for record in pipeline_records:
        segment = segment_records.get((record["主页"], record["姓名"]))
        if segment is not None and segment.name_source == "ocr" and segment.segment.text:
            existing = record["简介"].strip()
            record["简介"] = existing + ("\n\n" if existing else "") + segment.segment.text
    profile_owners = Counter(cand.url for cand in admitted)
    for record in pipeline_records:
        url = record["主页"]
        html = scoped_profile_html.get(url)
        if not html or profile_owners[url] > 1:
            continue
        single = FacultyProfileExtractor.extract(
            html, url, fallback_name=record["姓名"], preserve_content=url in verified_profile_urls)
        selected_bio = " ".join(record["简介"].split())
        single_bio = " ".join(single.full_bio.split())
        if single_bio and selected_bio and len(single_bio) > len(selected_bio) and selected_bio in single_bio:
            if len(single_bio) - len(selected_bio) > 30:
                unresolved.append({"url": url, "name": record["姓名"],
                                   "status": "profile_body_disagreement",
                                   "selected_chars": len(selected_bio), "single_chars": len(single_bio)})
    return unresolved


def page_names_person(name: str, html: str, image_biography: str = "") -> bool:
    """The profile page (or its image biography) carries the roster name.

    Both sides are compared without whitespace: the page may space a Chinese
    name ('林  静'), and a Latin name keeps the spaces between its words
    ('KHASKHELI MUHAMMAD BILAWAL').
    """
    compact = re.sub(r"\s+", "", name)
    page = re.sub(r"\s+", "", BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    return compact in page or compact in re.sub(r"\s+", "", image_biography)


async def run_plan(plan: dict[str, Any], browser_context: Any, output_dir: str | Path, *, state=None,
                   task_id: str | None = None) -> dict[str, Any]:
    """Capture a Subagent-selected college plan using an existing browser context."""
    started = time.perf_counter()
    stage_started = started
    stage_seconds = {}

    def finish_stage(name):
        nonlocal stage_started
        current = time.perf_counter()
        stage_seconds[name] = round(current - stage_started, 6)
        stage_started = current

    _validate_plan(plan)
    if state is not None:
        canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if state.plan_digest != hashlib.sha256(canonical.encode("utf-8")).hexdigest():
            raise ValueError("checkpoint state belongs to a different plan")
    from server.page_types import normalize_branch
    plan = {**plan, "branches": [normalize_branch(branch) for branch in plan["branches"]]}
    out = Path(output_dir).resolve()
    task_id = task_id or (out.parent.name if out.name.startswith('attempt-') else 'local-' + hashlib.sha256(str(out).encode()).hexdigest()[:24])
    out.mkdir(parents=True, exist_ok=True)
    from server.source_qualification import capture_source_dispositions
    disposition_issues, disposition_evidence = await capture_source_dispositions(browser_context, plan, out)
    from server import source_disposition_review
    disposition_issues, source_disposition_reviews = source_disposition_review.apply_decisions(
        plan, disposition_evidence, disposition_issues, out, task_id)
    cached_profile_urls = {row["url"] for row in state.list_pages("profile")} if state else set()
    restored_profiles = len(cached_profile_urls)
    source_capture, source_checkpoints = await _capture_sources_with_state(plan, browser_context, state)
    finish_stage("source_capture")
    all_pages = [(page.url, page.html) for page in source_capture.roster_pages]
    image_roster_cards: dict[str, int] = {}
    for captured_page in source_capture.roster_pages:
        selector = captured_page.branch.get("card_selector")
        if selector and captured_page.branch.get("kind") != "image_table":
            cards = BeautifulSoup(captured_page.html, "html.parser").select(selector)
            if cards and all(card.name == "img" for card in cards):
                image_roster_cards[captured_page.url] = len(cards)
    navigation_pages = source_capture.navigation_pages
    unresolved: list[dict] = [*source_capture.unresolved, *disposition_issues]
    branches = source_capture.branches

    candidates = []
    candidate_sources: dict[tuple[str, str], set[str | SourceEvidence]] = {}
    roster_reconciliation = reconcile_rosters(
        source_capture.roster_pages, plan,
        name_decisions=name_review.load_decisions(out / name_review.DECISIONS_FILE))
    candidates.extend(roster_reconciliation.candidates)
    candidate_sources.update(roster_reconciliation.candidate_sources)
    segment_naming = {"admitted": [], "unresolved": [], "evidence": []}
    if roster_reconciliation.segment_pending:
        from server.image_biography import fetch_browser_asset
        from server.segmented_roster import name_image_segments

        async def fetch_segment_image(media_url: str) -> bytes:
            return await fetch_browser_asset(browser_context, media_url)

        segment_naming = await name_image_segments(
            roster_reconciliation.segment_pending, fetch_asset=fetch_segment_image,
            evidence_dir=out / f"{_safe_name(plan['school'] + '_' + plan['college'])}_snapshots" / "segment_names",
            cache_dir=_ocr_cache_dir(out), review_decisions_path=out / "image_bio_reviews.json",
            shared_emails=roster_reconciliation.segment_shared_emails)
        unresolved.extend(segment_naming["unresolved"])
        for record in segment_naming["admitted"]:
            person = FacultyCandidate.from_roster_record(record)
            candidates.append(person)
            if record.source_evidence is not None:
                candidate_sources.setdefault((person.name, person.url), set()).add(record.source_evidence)
    pdf_reading = {"records": [], "unresolved": [], "evidence": [], "name_evidence": [],
                   "observations": {}, "read_pages": set()}
    if roster_reconciliation.pdf_rosters:
        from server.image_biography import fetch_browser_asset
        from server.pdf_roster import read_pdf_rosters

        async def fetch_pdf(media_url: str) -> bytes:
            return await fetch_browser_asset(browser_context, media_url)

        pdf_reading = await read_pdf_rosters(
            roster_reconciliation.pdf_rosters, fetch_asset=fetch_pdf,
            evidence_dir=out / f"{_safe_name(plan['school'] + '_' + plan['college'])}_snapshots" / "pdf_rosters",
            cache_dir=_ocr_cache_dir(out), review_decisions_path=out / "image_bio_reviews.json",
            name_decisions_path=out / name_review.DECISIONS_FILE)
        unresolved.extend(pdf_reading["unresolved"])
        for record in pdf_reading["records"]:
            person = FacultyCandidate.from_roster_record(record)
            candidates.append(person)
            if record.source_evidence is not None:
                candidate_sources.setdefault((person.name, person.url), set()).add(record.source_evidence)
        for observation in roster_reconciliation.observations:
            pdf_observations = pdf_reading["observations"].get(observation["url"])
            if observation.get("status") != "embedded_pdf_roster_requires_review" or not pdf_observations:
                continue
            issues = any(item.get("url") == observation["url"] for item in pdf_reading["unresolved"])
            observation.update(
                status=("pdf_roster_reconciled" if observation["url"] in pdf_reading["read_pages"] and not issues
                        else "pdf_roster_unresolved"),
                pdf_rosters=pdf_observations,
                extracted_candidates=sum(item.get("records_admitted", 0) for item in pdf_observations))
    table_reading = {"records": [], "unresolved": [], "evidence": [], "observations": {}}
    if roster_reconciliation.image_table_sources:
        from server.image_biography import fetch_browser_asset
        from server.image_table_roster import read_image_table_rosters, REVIEW_FILE
        async def fetch_table(media_url):
            return await fetch_browser_asset(browser_context, media_url)
        table_reading = await read_image_table_rosters(
            roster_reconciliation.image_table_sources, fetch_asset=fetch_table,
            evidence_dir=out / f"{_safe_name(plan['school'] + '_' + plan['college'])}_snapshots" / "image_tables",
            reviews_path=out / REVIEW_FILE)
        unresolved.extend(table_reading["unresolved"])
        for record in table_reading["records"]:
            person = FacultyCandidate.from_roster_record(record)
            candidates.append(person)
            if record.source_evidence is not None:
                candidate_sources.setdefault((person.name, person.url), set()).add(record.source_evidence)
        for observation in roster_reconciliation.observations:
            tables = table_reading["observations"].get(observation["url"])
            if tables:
                observation.update(status="image_table_reconciled" if all(t["complete"] for t in tables)
                                   else "image_table_unresolved", image_tables=tables,
                                   extracted_candidates=sum(t["records_admitted"] for t in tables))
    segment_records = {(record.page_url, record.name): record for record in
                       roster_reconciliation.segment_records + segment_naming["admitted"]
                       + pdf_reading["records"] + table_reading["records"]}
    # Text duplicates are reported by the reconciler; a name read by OCR that
    # repeats another record of the same article must not collapse two people.
    # Two PDFs on one page with one name are two observations of that name.
    named = Counter((record.page_url, record.name) for record in
                    roster_reconciliation.segment_records + segment_naming["admitted"]
                    + pdf_reading["records"] + table_reading["records"])
    ocr_named = {(record.page_url, record.name) for record in
                 segment_naming["admitted"] + pdf_reading["records"] + table_reading["records"]}
    unresolved.extend({"url": url, "status": "segment_duplicate_name", "occurrences": count}
                      for (url, name), count in named.items() if count > 1 and (url, name) in ocr_named)
    roster_observations = roster_reconciliation.observations
    unresolved.extend(
        item for item in roster_reconciliation.unresolved
        # Every PDF on the page was read: its records and their own issues
        # now account for the roster instead of the page-level stop.
        if not (item.get("status") == "embedded_pdf_roster_requires_review"
                and item.get("url") in pdf_reading["read_pages"])
    )
    if (not candidates and roster_observations and not unresolved
            and all(item.get("status") == "confirmed_empty" for item in roster_observations)):
        unresolved.append({"url": plan["branches"][0]["url"],
                           "status": "zero_person_delivery_requires_review"})
    unique = {(cand.name, cand.url): cand for cand in candidates}
    candidates = list(unique.values())
    roster_urls = {url for url, _ in all_pages}
    # 同名合并：补充名单上的名字与学院里唯一的同名个人主页是同一人。
    from server.faculty_identity import merge_same_name_observations
    candidates, candidate_sources, identity_merges = merge_same_name_observations(
        candidates, candidate_sources, roster_urls)
    finish_stage("roster_reconciliation")
    profile_urls = sorted({cand.url for cand in candidates if cand.url and cand.url not in roster_urls})
    profile_args = {"profile_type_branches": roster_reconciliation.profile_type_branches}
    profile_type_evidence = {}
    profile_args['profile_type_evidence'] = profile_type_evidence
    if state:
        profile_args["state"] = state
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
    from server.faculty_identity import resolve_broken_profile_aliases, resolve_duplicate_profile_aliases
    aliases = resolve_broken_profile_aliases(
        candidates, profile_html, profile_errors, profile_live_evidence,
        roster_reconciliation.profile_content_sources)
    profile_aliases = []
    for dead, live in aliases.items():
        profile_aliases.append({"broken_url": dead, "verified_url": live,
                                "reason": "repeated_official_roster_link_and_unique_verified_identity",
                                "source_rosters": len({item.get("roster_url") for item in
                                                       roster_reconciliation.profile_content_sources.get(dead, [])})})
    duplicates = resolve_duplicate_profile_aliases(candidates, profile_html, profile_live_evidence, roster_urls)
    for duplicate, kept in duplicates.items():
        profile_aliases.append({"duplicate_url": duplicate, "verified_url": kept,
                                "reason": "identical_verified_profile_body"})
    aliases = {**aliases, **duplicates}
    for dead in aliases:
        candidates = [candidate for candidate in candidates if candidate.url != dead]
        candidate_sources = {key: value for key, value in candidate_sources.items() if key[1] != dead}
        roster_reconciliation.profile_content_selectors.pop(dead, None)
        profile_html.pop(dead, None)
    if aliases:
        profile_errors = [item for item in profile_errors if item.get("url") not in aliases]
        unresolved = [item for item in unresolved if item.get("url") not in aliases
                      or item.get("status") != "profile_content_selector_conflict"]
    # A person said a page that never names its roster teacher is not that
    # teacher's page: collect from the roster, or leave the name out.
    from server import profile_page_review
    candidates, candidate_sources, released, profile_page_reviews = profile_page_review.apply_decisions(
        candidates, candidate_sources, profile_html,
        profile_page_review.load_decisions(out / profile_page_review.DECISIONS_FILE))
    for url in released:
        roster_reconciliation.profile_content_selectors.pop(url, None)
        profile_html.pop(url, None)
        profile_live_evidence.pop(url, None)
    if released:
        profile_errors = [item for item in profile_errors if item.get("url") not in released]
        unresolved = [item for item in unresolved if item.get("url") not in released]
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
    name = _safe_name(f"{plan['school']}_{plan['college']}")
    snapshot_dir, snapshots = _save_capture_snapshots(out, name, navigation_pages, all_pages, profile_html)
    base_url = all_pages[0][0] if all_pages else (
        plan['branches'] or plan['source_dispositions'])[0]['url']
    # Preserve raw captures above; only the parser receives the verified subtree copy.
    scoped_profile_html, profile_scope_evidence, scope_issues = _scope_captured_profiles(
        profile_html, roster_reconciliation.profile_content_selectors,
        profile_live_evidence, roster_reconciliation.profile_content_sources, candidates,
        blocked_profile_urls={item['url'] for item in unresolved
                              if item.get('status') == 'profile_content_browser_unverified'})
    unresolved.extend(scope_issues)
    unverified_profile_urls = {issue['url'] for issue in scope_issues}
    finish_stage("profile_capture_and_scope")
    from server.image_biography import (audit_profile_media, browser_asset_size, fetch_browser_asset,
                                        media_references)
    async def fetch_original_media(url: str) -> bytes:
        return await fetch_browser_asset(browser_context, url)

    head_failed_hosts: set[str] = set()

    async def declared_media_size(url: str) -> int | None:
        # A host that leaves HEAD unanswered would cost the wait on every image.
        host = urlparse(url).netloc
        if host in head_failed_hosts:
            return None
        try:
            return await browser_asset_size(browser_context, url)
        except Exception:
            head_failed_hosts.add(host)
            return None

    pdf_roster_urls = set()
    roster_pdf_pages = {item["url"] for item in roster_reconciliation.pdf_rosters}
    for url, html in all_pages:
        if url in roster_pdf_pages or not any(candidate.url == url for candidate in candidates):
            continue
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("article") or soup.select_one("main") or soup.body or soup
        if any(ref.kind == "pdf" for ref in media_references(str(content), url)):
            pdf_roster_urls.add(url)
    media_pages = dict(scoped_profile_html)
    # Auxiliary sources remain original evidence even when none of their
    # observations qualify for export. Reading their media cannot donate fields
    # to accepted rows; enrichment below receives only the admitted pipeline.
    auxiliary_source_urls = set()
    for captured_page in source_capture.roster_pages:
        source = captured_source_evidence(captured_page.html, captured_page.url, captured_page.branch,
                                          plan, captured_page.source_role_evidence)
        if source.role == 'faculty_roster' and source.status == 'verified':
            continue
        scopes = BeautifulSoup(captured_page.html, 'html.parser').select(
            captured_page.branch['source_container_selector'])
        if len(scopes) == 1:
            media_pages[captured_page.url] = str(scopes[0])
            auxiliary_source_urls.add(captured_page.url)
    media_pages.update((url, html) for url, html in all_pages
                       if url not in auxiliary_source_urls and (url in image_roster_cards or url in pdf_roster_urls))
    # Known segments provide owned media; auxiliary sources keep the complete
    # original scope, including segments still waiting for a name.
    segment_media: dict[str, list[str]] = {}
    for (page_url, _name), record in sorted(segment_records.items(),
                                            key=lambda item: (item[0][0], item[1].segment.index)):
        if record.segment.images:
            segment_media.setdefault(page_url, []).append(record.segment.html)
    media_pages.update((url, "".join(parts)) for url, parts in segment_media.items() if url not in auxiliary_source_urls)
    segment_media_urls = set(segment_media)
    media_owners: dict[str, set[str]] = {}
    for candidate in candidates:
        if candidate.url in media_pages:
            media_owners.setdefault(candidate.url, set()).add(candidate.name)
    for url in auxiliary_source_urls:
        media_owners.setdefault(url, set())
    ocr_cache_dir = _ocr_cache_dir(out)
    media_audit = await audit_profile_media(
        media_pages, media_owners, snapshot_dir / "profile_media",
        profile_source_html=profile_html,
        profile_source_urls={url: proof.get('url',url) for url,proof in profile_live_evidence.items()},
        fetch_asset=fetch_original_media, scoped_urls=set(profile_scope_evidence) | segment_media_urls | auxiliary_source_urls,
        known_names={candidate.name for candidate in candidates},
        review_decisions_path=out / "image_bio_reviews.json",
        media_kind_decisions_path=out / "media_kind_reviews.json",
        cache_dir=ocr_cache_dir, asset_size=declared_media_size,
        unpublished_media={url: set((evidence.get("profile_content_browser_evidence") or {})
                                    .get("unpublished_images") or ())
                           for url, evidence in profile_scope_evidence.items()},
        missing_media_evidence={url: (evidence.get('profile_content_browser_evidence') or {})
                                    .get('missing_content_images',[])
                               for url,evidence in profile_scope_evidence.items()})
    profile_snapshots = {item['url']:item['path'] for item in snapshots if item['kind'] == 'profile'}
    for item in media_audit['missing_media_evidence']:
        item['snapshot_path'] = profile_snapshots[item['requested_profile_url']]
    unresolved.extend(media_audit["unresolved"])
    for url, scope_evidence in profile_scope_evidence.items():
        if not scope_evidence["image_only_candidate"]:
            continue
        for candidate_name in media_owners.get(url, set()):
            if (url, candidate_name) not in media_audit["assigned_biographies"]:
                unresolved.append({"url": url, "name": candidate_name,
                                   "status": "image_bio_unreadable_profile"})
    for record in segment_naming["admitted"]:
        if (record.page_url, record.name) not in media_audit["assigned_biographies"]:
            unresolved.append({"url": record.page_url, "name": record.name,
                               "segment": record.segment.index, "status": "segment_image_bio_unassigned"})
    for roster_url, card_count in image_roster_cards.items():
        roster_names = {candidate.name for candidate in candidates if candidate.url == roster_url}
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
    admitted, eligibility, eligibility_errors = classify_candidates(
        candidates, candidate_sources, profile_html, plan, profile_scope_evidence=profile_scope_evidence)
    from server import source_review
    source_review_items = source_review.build_items(candidates, candidate_sources, profile_html, plan, task_id,
        snapshots, media_audit['evidence'] + pdf_reading['evidence'] + segment_naming['evidence'] + table_reading['evidence'])
    admitted, eligibility, eligibility_errors, source_relationship_reviews = source_review.apply_decisions(
        candidates, admitted, eligibility, eligibility_errors, source_review_items, out, task_id)
    unresolved.extend(eligibility_errors)
    for candidate in admitted:
        html = profile_html.get(candidate.url)
        if html and not page_names_person(candidate.name, html,
                                          media_audit["biographies"].get(candidate.url, "")):
            unresolved.append({"url": candidate.url, "name": candidate.name,
                               "status": "profile_identity_not_found"})
    finish_stage("media_audit")
    title_field_evidence = {}
    extraction = await asyncio.to_thread(
        FacultyHarvestPipeline.harvest_candidates,
        admitted, profile_fetcher=lambda url: scoped_profile_html.get(url), base_url=base_url,
        school=plan["school"], college=plan["college"],
        agent_name=plan.get("agent_name", "Browser-assisted Subagent"),
        profileless_urls=roster_urls,
        verified_profile_urls=set(profile_scope_evidence),
        unverified_profile_urls=unverified_profile_urls,
        title_field_evidence=title_field_evidence,
        defer_homepage_projection=True,
    ) if admitted else {"sheet1_with_email": [], "sheet2_no_email": [],
                          "unresolved": [], "delivery_ready": False}
    unresolved.extend(extraction["unresolved"])
    pipeline_records = extraction["sheet1_with_email"] + extraction["sheet2_no_email"]
    unresolved.extend(await asyncio.to_thread(_enrich_extracted_records,
        pipeline_records, segment_records, media_audit, out, scoped_profile_html, admitted,
        verified_profile_urls=set(profile_scope_evidence), title_field_evidence=title_field_evidence))
    from server import html_email_review
    unresolved, html_email_items, html_email_reviews = html_email_review.apply_decisions(
        unresolved, pipeline_records, admitted, scoped_profile_html, out,
        raw_source_html=profile_html, source_evidence=profile_scope_evidence)
    remaining = {id(item) for item in unresolved}
    extraction_ready = bool(extraction["delivery_ready"] or (
        html_email_reviews and extraction.get("records_conserved") and pipeline_records
        and not any(id(item) in remaining for item in extraction["unresolved"])))
    finish_stage("deterministic_extraction")
    fields = ("序号", "姓名", "职称", "邮箱", "学校", "学院", "采集人", "采集日期", "简介", "主页")
    records = [{field: row.get(field, "") for field in fields} for row in pipeline_records]
    from server import identity_review
    source_hashes = {url: evidence.get("selected_html_sha256", "")
                     for url, evidence in profile_scope_evidence.items()}
    source_hashes.update({url: hashlib.sha256(html.encode("utf-8")).hexdigest()
                          for url, html in profile_html.items() if html and url not in source_hashes})
    source_hashes.update({url: hashlib.sha256(html.encode("utf-8")).hexdigest()
                          for url, html in all_pages if url not in source_hashes})
    observations_path = out / f"{name}_identity_observations.json"
    observations_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    decision_path = out / identity_review.DECISIONS_FILE
    if html_email_reviews:
        # A reviewed e-mail may move a row between sheets; numbering follows the rows.
        records = [{**row, "序号": index} for index, row in enumerate(records, 1)]
    disposition_pages = identity_review.disposition_sources(out, disposition_evidence)
    records, identity_items, identity_reviews = identity_review.apply_decisions(records, source_hashes, decision_path,
        source_html={**dict(all_pages), **disposition_pages, **profile_html},
        source_evidence=identity_review.source_scope_evidence(
            profile_scope_evidence, eligibility, media_audit['evidence'], disposition_evidence))
    reviewed_pairs = identity_review.validated_pairs(records, identity_reviews, decision_path)
    from server.faculty_identity import identity_review_issues
    unresolved.extend(identity_review_issues(records, reviewed_pairs))
    from server.excel_export import excel_cell_issues
    unresolved.extend(excel_cell_issues(records))
    from server.harvest_coverage import build_coverage_ledger
    coverage = build_coverage_ledger(plan, source_capture, roster_observations, candidates,
                                     profile_html, eligibility, snapshots)
    # The roster pages that linked each profile: a profile-page failure belongs
    # to those branches, whichever selector they happen to share.
    coverage["profile_sources"] = {url: sorted({source["roster_url"] for source in sources})
                                   for url, sources in roster_reconciliation.profile_content_sources.items()}
    unresolved.extend(coverage["issues"])
    coverage["issues"] = list(unresolved)
    coverage["complete"] = coverage["complete"] and not unresolved
    coverage["unresolved_count"] = len(unresolved)
    pdf_record_keys = {(record.page_url, record.name) for record in pdf_reading["records"]}
    table_sources = [{**source, "roster_snapshot": next((snap["path"] for snap in snapshots
                      if snap["kind"] == "directory" and snap["url"] == source["roster_url"]), None)}
                     for observation in roster_observations
                     for source in observation.get("html_table_rows", [])]

    def table_row_sources(row):
        urls = {row["主页"]}
        urls.update(dead for dead, live in aliases.items() if live == row["主页"])
        for merge in identity_merges:
            if merge["name"] == row["姓名"] and merge["kept_url"] == row["主页"]:
                urls.update(merge["merged_list_urls"])
        # Current source observations no longer merge on name alone. A validated
        # human identity resolution must retain the supplementary row provenance.
        for certificate in identity_reviews:
            urls.update(source_url for source_url, primary in certificate["resolution"].items()
                        if primary == row["主页"])
        return [source for source in table_sources
                if source["name"] == row["姓名"] and source["profile_url"] in urls]

    table_profileless = {(record.page_url, record.name) for record in table_reading["records"]}
    coverage["field_sources"] = [{"record_index": index, "profile_url": row["主页"],
        "profile_snapshot": next((snap["path"] for snap in snapshots
                                  if snap["kind"] == "profile" and snap["url"] == row["主页"]), None),
        "roster_snapshot_urls": sorted(roster_urls),
        "extraction": ("deterministic_dom_and_local_ocr"
                       if (row["主页"], row["姓名"]) in media_audit["assigned_biographies"]
                       else "deterministic_image_table_ocr"
                       if (row["主页"], row["姓名"]) in table_profileless
                       else "deterministic_pdf_text_layer_and_local_ocr"
                       if (row["主页"], row["姓名"]) in pdf_record_keys
                       else "deterministic_dom"),
        "attribution_scope": ("image_table_cells" if (row["主页"], row["姓名"]) in table_profileless
                              else "pdf_roster_record" if (row["主页"], row["姓名"]) in pdf_record_keys
                              else "segmented_article_record"
                              if (row["主页"], row["姓名"]) in segment_records else
                              "unverified_profile_container" if row["主页"] in unverified_profile_urls
                              else "candidate_and_profile"),
        **({"profile_fields_unread": True} if row["主页"] in unverified_profile_urls else {}),
        **({"segment_record": segment_records[(row["主页"], row["姓名"])].provenance()}
           if (row["主页"], row["姓名"]) in segment_records else {}),
        **({"html_table_rows": table_row_sources(row)} if table_row_sources(row) else {}),
        "image_biography_evidence": [item for item in media_audit["evidence"]
                                      if item["profile_url"] == row["主页"]
                                      and item.get("assigned_name") == row["姓名"]],
        **profile_scope_evidence.get(row["主页"], {})} for index, row in enumerate(records, 1)]
    coverage["profile_aliases"] = profile_aliases
    coverage["identity_merges"] = identity_merges
    coverage["identity_reviews"] = identity_reviews
    coverage_path = out / f"{name}_coverage.json"
    from server.delivery_fields import project_personal_homepages
    records = project_personal_homepages(records, roster_urls)
    for source, row in zip(coverage["field_sources"], records):
        source["receipt_sha256"] = identity_review.record_digest(row)
    # A reviewer can withdraw/replace a decision while the resumed native pass
    # reads assets. Recheck the authoritative task store before any export.
    latest_source_decisions = source_review.decisions_for(out, task_id)
    disposition_items = {item['id']: item for item in source_disposition_review.build_items(
        plan, disposition_evidence, out, task_id)}
    latest_dispositions = source_disposition_review.review_store.load(source_disposition_review.decision_path(out, task_id))
    for applied in source_disposition_reviews:
        item = disposition_items[applied['id']]
        if source_disposition_review.valid_decision(item, latest_dispositions.get(item['id'])) != applied:
            unresolved.append({'url': item['url'], 'status': item['reason'],
                               'reason': 'source_disposition_changed_during_capture'})
    for applied in source_relationship_reviews:
        item = next(item for item in source_review_items if item['id'] == applied['id'])
        latest = source_review.valid_decision(item, latest_source_decisions.get(item['id']), include_unknown=True)
        if latest != applied:
            unresolved.append({'url': item['profile_url'], 'name': item['name'], 'status': source_review.STATUS,
                               'reason': 'relationship_review_changed_during_capture'})
    coverage["issues"] = list(unresolved)
    coverage["complete"] = coverage["complete"] and not unresolved
    coverage["unresolved_count"] = len(unresolved)
    coverage_path.write_text(json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8")
    ready = bool(extraction_ready and not unresolved and coverage["complete"])
    records_path = out / f"{name}_records.json"
    records_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    excel_path = None
    biography_storage = None
    if ready:
        excel = out / f"{name}.xlsx"
        excel_path = commit_records(
            records,
            str(excel),
            exporter=excel_export.export_dual_sheet_excel,
        )
        biography_storage = excel_export.verify_biography_storage(excel_path, records)

    finish_stage("coverage_and_delivery")
    report = {
        **({'biography_storage': biography_storage} if biography_storage else {}),
        "stage_seconds": stage_seconds,
        "status": "completed" if ready else "needs_agent_review",
        "delivery_ready": ready,
        "school": plan["school"], "college": plan["college"],
        "source_kind": plan.get("source_kind", "university_faculty_roster"),
        "directory_pages": len(all_pages), "navigation_pages": len(navigation_pages),
        "profile_pages": sum(bool(html) for html in profile_html.values()),
        "profile_attempts": len(profile_urls), "elapsed_seconds": round(time.perf_counter() - started, 3),
        "candidate_count": len(candidates),
        "excluded_count": sum(item["status"] == "excluded" for item in eligibility),
        "eligibility": eligibility,
        'task_id': task_id, 'source_review_items': source_review_items,
        'source_dispositions': plan.get('source_dispositions', []),
        'source_disposition_evidence': disposition_evidence,
        'source_disposition_reviews': source_disposition_reviews,
        'source_relationship_reviews': source_relationship_reviews,
        'source_qualification_evidence': [
            {'source_url': page.url, 'source_role': page.branch.get('source_role', 'unverified_people_source'),
             'source_html_sha256': hashlib.sha256(page.html.encode('utf-8')).hexdigest(),
             'browser_proof': page.source_role_evidence}
            for page in source_capture.roster_pages],
        "total": len(records),
        "with_email": sum(bool(str(row.get("邮箱", "")).strip()) for row in records),
        "branches": branches, "roster_observations": roster_observations,
        "profile_page_type_evidence": profile_type_evidence,
        "profile_aliases": profile_aliases,
        "profile_page_reviews": profile_page_reviews,
        "email_alternates": extraction.get("email_alternates", []),
        "identity_merges": identity_merges,
        "identity_review_items": identity_items, "identity_reviews": identity_reviews,
        "identity_review_path": str(decision_path), "identity_observations_path": str(observations_path),
        "html_email_review_items": html_email_items, "html_email_reviews": html_email_reviews,
        "image_biography_evidence": media_audit["evidence"] + pdf_reading["evidence"],
        "unpublished_media": media_audit["unpublished_media"],
        "oversized_media": media_audit["oversized_media"],
        "segment_name_evidence": segment_naming["evidence"] + pdf_reading["name_evidence"],
        "pdf_roster_evidence": pdf_reading["evidence"],
        "missing_profile_media_evidence": media_audit['missing_media_evidence'],
        "missing_fields": [{"url": url, "name": evidence['empty_profile_evidence']['name'],
                            "field": "简介", "status": "browser_verified_empty_profile"}
                           for url, evidence in profile_scope_evidence.items()
                           if evidence.get('empty_profile_evidence')],
        "image_table_evidence": table_reading["evidence"],
        "image_table_reviews_sha256": hashlib.sha256((out / "image_table_reviews.json").read_bytes()).hexdigest()
                                      if (out / "image_table_reviews.json").is_file() else None,
        "unresolved": unresolved, "snapshots": snapshots,
        "records_path": str(records_path), "excel_path": excel_path,
        "coverage_scope": "supplied_roster_branches_only",
        "coverage": coverage, "coverage_path": str(coverage_path),
        "checkpoints": {"enabled": state is not None, "plan_digest": state.plan_digest if state else None,
                        **source_checkpoints, "restored_profiles": restored_profiles,
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
                harvest = lambda: run_plan(plan, context, args.output_dir, task_id=args.task_id or None)
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
