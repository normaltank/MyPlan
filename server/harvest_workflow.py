"""Generic seams for a browser-assisted college harvest.

This module contains the workflow boundary, rather than a website adapter.  A
Subagent supplies a bounded plan and the browser driver supplies page capture;
the rest of the system receives a small, typed capture bundle that can be
reconciled and committed independently.  Site-specific selectors therefore
remain plan data and do not become branches in the core collector.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import re
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from server.faculty_extractor import FacultyCandidate, FacultyDirectoryExtractor, html_table_rows
from server.detail_mecc import mailto_address_conflicts
from server.source_qualification import SourceEvidence, captured_source_evidence


CaptureBranch = Callable[
    [Any, dict[str, Any]],
    Awaitable[tuple[list[tuple[str, str]], list[dict[str, Any]], dict[str, Any]]],
]
DiscoverChildren = Callable[
    [Any, dict[str, Any]],
    Awaitable[tuple[str | None, list[str], list[dict[str, Any]], dict[str, Any]]],
]


@dataclass(frozen=True)
class CapturedRosterPage:
    """One rendered roster page and the plan branch that authorized it."""

    url: str
    html: str
    branch: dict[str, Any]
    empty_roster_evidence: dict[str, Any] | None = None
    image_table_evidence: dict[str, Any] | None = None
    source_role_evidence: dict[str, Any] | None = None


@dataclass
class SourceCapture:
    """Browser output handed from source capture to reconciliation."""

    navigation_pages: list[tuple[str, str]] = field(default_factory=list)
    roster_pages: list[CapturedRosterPage] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)
    branches: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RosterReconciliation:
    """Deterministic page-level reconciliation before eligibility policy."""

    candidates: list[FacultyCandidate]
    candidate_sources: dict[tuple[str, str], set[str | SourceEvidence]]
    observations: list[dict[str, Any]]
    unresolved: list[dict[str, Any]]
    profile_content_selectors: dict[str, set[tuple[str, str | None]]] = field(default_factory=dict)
    profile_content_sources: dict[str, list[dict[str, str]]] = field(default_factory=dict)
    # 分段平铺名录: records named from their own text, records waiting for an
    # OCR name, and per-page addresses that no single record may claim.
    segment_records: list[Any] = field(default_factory=list)
    segment_pending: list[Any] = field(default_factory=list)
    segment_shared_emails: dict[str, set[str]] = field(default_factory=dict)
    segmented_pages: dict[str, str] = field(default_factory=dict)
    # 多人 PDF 名录: verified cards that hold only PDFs, read later in the
    # browser session by server/pdf_roster.py.
    pdf_rosters: list[dict[str, Any]] = field(default_factory=list)
    image_table_sources: list[dict[str, Any]] = field(default_factory=list)


# Page conditions that pass with time (flaky site, late requests, a settling
# layout). A profile failing for one of these is opened again, up to three times.
TRANSIENT_PROOF_REASONS = frozenset({"network_not_idle", "dom_unstable"})


async def capture_profiles(
    browser_context: Any,
    urls: Iterable[str],
    concurrency: int,
    ready_selector: str | None = None,
    *,
    state: Any = None,
    profile_content_selectors: dict[str, set[tuple[str, str | None]]] | None = None,
    profile_content_evidence: dict[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, str | None], list[dict[str, Any]]]:
    """Fetch candidate pages after roster reconciliation, with bounded fan-out."""

    import asyncio
    import contextlib
    import hashlib
    import time

    from server.site_limits import site_slot

    if concurrency < 1:
        raise ValueError("profile concurrency must be positive")

    semaphore = asyncio.Semaphore(concurrency)
    pages: dict[str, str | None] = {}
    unresolved: list[dict[str, Any]] = []

    async def capture(url: str) -> None:
        scope_pairs = (profile_content_selectors or {}).get(url, set())
        scoped = bool(scope_pairs)
        saved_record = state.load_page_record("profile", url) if state is not None else None
        if state is not None:
            saved = state.load_page("profile", url)
            if saved is not None and not scoped:
                pages[url] = saved
                return
        # Per-college fan-out first, then the site-wide slot shared with every
        # other college of the same university running in this process.
        async with semaphore, site_slot(url):
            recurring = None  # requests that failed on the previous open of this page
            recurring_missing = None
            for attempt in range(3):
                page = None
                retryable = True
                try:
                    page = await browser_context.new_page()
                    page_errors = []
                    if scoped:
                        from server.browser_readiness import install_request_tracking
                        install_request_tracking(page)
                        if callable(getattr(page, 'on', None)):
                            page.on('pageerror', lambda error: page_errors.append(
                                {'message': str(error), 'stack': getattr(error, 'stack', '')}))
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if response is not None and response.status >= 400:
                        retryable = response.status == 429 or response.status >= 500
                        raise RuntimeError(f"HTTP {response.status}")
                    if ready_selector:
                        await page.locator(ready_selector).wait_for(state="visible", timeout=10000)
                    if scoped:
                        with contextlib.suppress(Exception):
                            await page.wait_for_load_state("networkidle", timeout=4000)
                    html = await page.content()
                    if not html.strip():
                        raise RuntimeError("empty rendered page")
                    proof = None
                    if scoped:
                        from server.profile_structure import (resolve_equivalent_profile_selectors,
                                                              verify_profile_container)
                        pair = (next(iter(scope_pairs)) if len(scope_pairs) == 1 else
                                await resolve_equivalent_profile_selectors(page, scope_pairs))
                        if pair is None:
                            unresolved.append({"url": url, "status": "profile_content_selector_conflict",
                                               "selectors": [{"profile_content_selector": selector,
                                                              "profile_content_start_selector": start}
                                                             for selector, start in sorted(scope_pairs,
                                                                 key=lambda item: (item[0], item[1] or ""))]})
                        else:
                            selector, start_selector = pair
                            local_capture = {}
                            proof = await verify_profile_container(page, selector, start_selector,
                                                                   local_capture=local_capture,
                                                                   recurring_failures=recurring,
                                                                   recurring_missing_media=recurring_missing,
                                                                   allow_empty=True)
                            # Raw page errors remain in the local audit; structure
                            # proof still decides whether the profile is settled.
                            proof['page_errors'] = page_errors
                            if len(scope_pairs) > 1:
                                proof["equivalent_profile_content_selectors"] = sorted(
                                    selector for selector, _ in scope_pairs)
                            # Retain exactly the local snapshot hashed by the proof. The
                            # browser-facing JSON never includes this raw capture.
                            html = local_capture.get('html', html)
                            if (proof.get("confirmed") is not True
                                    and proof.get("reason") in TRANSIENT_PROOF_REASONS and attempt < 2):
                                # Requests still in flight or a page still settling: open
                                # the page again rather than judge it from one moment.
                                recurring = local_capture.get("failed_requests")
                                recurring_missing = local_capture.get('missing_media_failures')
                                await asyncio.sleep(1.5 * (attempt + 1))
                                continue
                            if (proof.get("confirmed") is not True
                                    or proof.get("html_sha256") != hashlib.sha256(html.encode("utf-8")).hexdigest()):
                                unresolved.append({"url": url, "status": "profile_content_browser_unverified",
                                                   "selector": selector,
                                                   "profile_content_start_selector": start_selector,
                                                   "browser_evidence": proof})
                                proof = None
                    same_capture = bool(saved_record and saved_record["html"] == html)
                    capture_record = saved_record if same_capture else None
                    if state is not None and not same_capture:
                        capture_record = state.save_page("profile", url, html,
                            meta={"final_url": getattr(page, "url", url)})
                    if proof is not None and profile_content_evidence is not None:
                        from server.missing_media_review import identity
                        for item in proof.get('missing_content_images', []):
                            item['requested_profile_url'] = url
                            item['id'] = identity(item)
                        profile_content_evidence[url] = {**proof,
                            "verified_at": time.time(),
                            "capture_origin": "checkpoint_revalidated" if same_capture else "fresh_browser_capture",
                            "captured_at": capture_record["captured_at"] if capture_record else time.time()}
                    pages[url] = html
                    return
                except Exception as exc:
                    if retryable and attempt < 2:
                        await asyncio.sleep(0.1 * (attempt + 1))
                        continue
                    pages[url] = None
                    unresolved.append({"url": url, "status": "profile_navigation_failed",
                                       "error": f"{type(exc).__name__}: {exc}", "attempts": attempt + 1})
                    return
                finally:
                    if page is not None:
                        with contextlib.suppress(Exception):
                            await page.close()

    await asyncio.gather(*(capture(url) for url in dict.fromkeys(urls)))
    return pages, unresolved


async def capture_sources(
    plan: dict[str, Any],
    browser_context: Any,
    *,
    capture_branch: CaptureBranch,
    discover_children: DiscoverChildren,
) -> SourceCapture:
    """Execute only the bounded source-navigation portion of a plan.

    The callbacks are the browser implementation.  Keeping them injected is
    intentional: the workflow is testable with a fake browser and does not
    need to know whether a page uses tabs, ordinary pagination, or child links.
    No arbitrary recursion is introduced here; the plan remains the boundary.
    """

    result = SourceCapture()
    for branch in plan["branches"]:
        if branch.get("kind") == "navigation":
            html, child_urls, errors, evidence = await discover_children(browser_context, branch)
            if html is not None:
                result.navigation_pages.append((branch["url"], html))
            result.unresolved.extend({**item, "branch_url": branch["url"]} for item in errors)
            introductions = FacultyDirectoryExtractor.profile_introduction_links(
                html, branch["url"], branch["child_link_selector"]) if html and branch.get("child_link_selector") else []
            if introductions:
                # A person's introduction cannot be silently treated as a child
                # roster. Mixed directories also need a new verified plan; no
                # selected links are filtered or converted into completed work.
                result.unresolved.append({"url": branch["url"], "branch_url": branch["url"],
                    "status": "child_roster_not_verified", "reason": "navigation_links_are_profiles",
                    "profile_intro_link_count": len(introductions), "sample_urls": introductions[:4]})
                evidence["profile_intro_link_count"] = len(introductions)
                result.branches.append(evidence)
                continue
            for child_url in child_urls:
                child_branch = {**branch["child_branch"], "url": child_url}
                if "profile_content_selector" in branch and "profile_content_selector" not in child_branch:
                    child_branch["profile_content_selector"] = branch["profile_content_selector"]
                    if "profile_content_start_selector" in branch:
                        child_branch["profile_content_start_selector"] = branch["profile_content_start_selector"]
                pages, child_errors, child_evidence = await capture_branch(browser_context, child_branch)
                result.roster_pages.extend(
                    CapturedRosterPage(url, page_html, child_branch, child_evidence.get("empty_roster"),
                                       source_role_evidence=child_evidence.get('source_roles', {}).get(url))
                    for url, page_html in pages
                )
                result.unresolved.extend({**item, "branch_url": branch["url"]} for item in child_errors)
                evidence.setdefault("child_visits", []).append(child_evidence)
            result.branches.append(evidence)
            continue

        pages, errors, evidence = await capture_branch(browser_context, branch)
        result.roster_pages.extend(
            CapturedRosterPage(url, page_html, branch, evidence.get("empty_roster"),
                               evidence.get("table_images", {}).get(url), evidence.get('source_roles', {}).get(url))
            for url, page_html in pages
        )
        result.unresolved.extend({**item, "branch_url": branch["url"]} for item in errors)
        result.branches.append(evidence)
    return result


def _compact(text: str) -> str:
    return re.sub(r"[\s\u3000\u200b\u200c\u200d\ufeff]+", "", text or "")


def candidates_inside_cards(
    html: str,
    url: str,
    selector: str,
    candidates: list[FacultyCandidate],
) -> tuple[list[FacultyCandidate], list[FacultyCandidate]]:
    """Split candidates into those found in a verified card and those outside every card.

    The reconnaissance model verified ``selector`` as the roster's cards; a name
    extracted from elsewhere on the page (a menu item such as a section title)
    is not a roster entry. Image cards carry no text, so they are left alone.
    """
    cards = BeautifulSoup(html, "html.parser").select(selector)
    if not cards or all(card.name == "img" for card in cards):
        return list(candidates), []
    links: set[str] = set()
    texts = []
    for card in cards:
        anchors = ([card] if card.name == "a" and card.has_attr("href") else []) + card.select("a[href]")
        links.update(urljoin(url, a["href"]) for a in anchors)
        texts.append(_compact(card.get_text(" ", strip=True)))
    inside, outside = [], []
    for candidate in candidates:
        located = ((candidate.url != url and candidate.url in links)
                   or any(_compact(candidate.name) in text for text in texts))
        (inside if located else outside).append(candidate)
    return inside, outside


_MEDIA_TAGS = ["img", "picture", "svg", "object", "embed", "iframe", "video", "canvas"]


def observe_roster(
    html: str,
    url: str,
    selector: str,
    candidates: list[FacultyCandidate],
) -> dict[str, Any]:
    """Compare visible source units with independently extracted candidates."""

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(selector)
    table_rows = html_table_rows(soup)
    candidate_urls = {item.url for item in candidates if item.url != url}
    unaccounted = []
    blank = 0
    structural_rows = []
    for index, card in enumerate(cards, 1):
        row = card if card.name == "tr" else card.find_parent("tr")
        kind = table_rows.get(id(row), {}).get("kind")
        if kind in ("header", "section"):
            structural_rows.append({"index": index, "kind": kind, "text": card.get_text(" ", strip=True)})
            continue
        anchors = ([card] if card.name == "a" and card.has_attr("href") else []) + card.select("a[href]")
        links = [urljoin(url, a["href"]) for a in anchors]
        card_text = card.get_text(" ", strip=True)
        # A grid's filler cell ('<td>&nbsp;</td>') carries no text, link or media,
        # so it cannot hold a person; count it apart instead of as a missed one.
        if not _compact(card_text) and not links and not (
                card.name in _MEDIA_TAGS or card.find(_MEDIA_TAGS)):
            blank += 1
            continue
        # A two-character name padded to three ('王 奔') is extracted as '王奔'.
        compact = _compact(card_text)
        if not any(link in candidate_urls for link in links) and not any(
            _compact(candidate.name) in compact for candidate in candidates
        ):
            unaccounted.append(
                {"index": index, "links": links[:3], "text": card_text[:80],
                 "mailto_conflicts": mailto_address_conflicts(card)}
            )
    return {
        "url": url,
        "card_selector": selector,
        "visible_cards": len(cards) - blank - len(structural_rows),
        "blank_cards": blank,
        "extracted_candidates": len(candidates),
        "unaccounted_cards": unaccounted,
        **({"structural_rows": structural_rows} if structural_rows else {}),
        **({"html_table_rows": [{"roster_url": url, "profile_url": person.url,
                                 "name": person.name, **person.table_row}
                                for person in candidates if person.table_row is not None]}
           if any(person.table_row is not None for person in candidates) else {}),
    }


_CARD_NAME = re.compile(r"^[\u4e00-\u9fff]{2,10}(?:[·•][\u4e00-\u9fff]{1,10})*$")


def unrecognized_card_names(observation: dict[str, Any], url: str,
                            known: list[FacultyCandidate]) -> list[FacultyCandidate]:
    """Unaccounted cards that hold one name-shaped text and link one personal page.

    The card selector was verified, so the card is a roster entry; only the
    reader did not recognise its text as a name. A person confirms it through the
    same review as rare-surname names; nothing is admitted unreviewed.
    """
    names = {_compact(candidate.name) for candidate in known}
    found = []
    for card in observation.get("unaccounted_cards", []):
        links = [link for link in dict.fromkeys(card.get("links") or []) if link != url]
        name, title = FacultyDirectoryExtractor._clean_name(card.get("text", ""))
        name = _compact(name)
        if len(links) == 1 and _CARD_NAME.match(name) and name not in names:
            found.append(FacultyCandidate(name=name, url=links[0], title=title, card_context=card["text"],
                                          mailto_conflicts=tuple(card.get("mailto_conflicts", ()))))
            names.add(name)
    return found


def _apply_name_decisions(
    roster_url: str,
    found: list[FacultyCandidate],
    review: list[FacultyCandidate],
    decisions: dict[str, dict[str, Any]],
    unresolved: list[dict[str, Any]],
) -> list[FacultyCandidate]:
    """Admit rare-surname names a person confirmed; return those still undecided.

    A name judged not a person is left out, so its card stays unaccounted and
    the roster card count still has to be explained.
    """
    from server.name_review import pending_item

    awaiting = []
    for person in review:
        item = pending_item(roster_url, person)
        decision = decisions.get(item["id"])
        if decision is None:
            unresolved.append(item)
            awaiting.append(person)
        elif decision["verdict"] == "person":
            found.append(replace(person, name=decision["verified_name"], name_review_reason=''))
    return awaiting


def roster_card_reading(html: str, url: str, card_selector: str) -> dict[str, int] | None:
    """What the harvest's own card check would say of this page, as counts only.

    The recon gate reads it when a plan is submitted, so a roster whose cards the
    harvest cannot read is sent back to the scout instead of failing after capture
    (集美大学海洋文化与法律学院, 10-09: 59 rows, a 导师 column, 3 people read).
    None when the harvest would not report a card mismatch.
    """
    result = reconcile_rosters([CapturedRosterPage(url, html, {"card_selector": card_selector})],
                               {"source_kind": "university_faculty_roster"})
    outside = next((item for item in result.unresolved
                    if item.get('status') == 'roster_profile_link_outside_card'), None)
    if outside is not None:
        return {'outside_profile_links':outside['outside_profile_links']}
    mismatch = next((item for item in result.unresolved if item.get("status") == "roster_card_mismatch"), None)
    if mismatch is None:
        return None
    return {"visible_cards": mismatch["visible_cards"], "extracted_candidates": mismatch["extracted_candidates"]}


def reconcile_rosters(
    captured: Iterable[CapturedRosterPage],
    plan: dict[str, Any],
    name_decisions: dict[str, dict[str, Any]] | None = None,
) -> RosterReconciliation:
    """Extract and reconcile roster pages without applying domain eligibility.

    ``name_decisions`` are a person's verdicts on rare-surname names
    (server/name_review.py); names without one block delivery until reviewed.
    """

    candidates: list[FacultyCandidate] = []
    candidate_sources: dict[tuple[str, str], set[str | SourceEvidence]] = {}
    observations: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    default_source = plan.get("source_kind") or "university_faculty_roster"
    profile_content_selectors: dict[str, set[tuple[str, str | None]]] = {}
    profile_content_sources: dict[str, list[dict[str, str]]] = {}

    segment_records: list[Any] = []
    segment_pending: list[Any] = []
    segment_shared: dict[str, set[str]] = {}
    segmented_pages: dict[str, str] = {}
    pdf_rosters: list[dict[str, Any]] = []
    image_tables: list[dict[str, Any]] = []
    captured_pages = list(captured)
    for page in captured_pages:
        source_evidence = captured_source_evidence(page.html, page.url, page.branch, plan, page.source_role_evidence)
        # Capturing people and qualifying their relationship are separate.
        # Even an unproven/organizational page retains every original observation.
        source_kind = page.branch.get('source_kind') or default_source
        if page.branch.get("kind") == "image_table":
            proof = page.image_table_evidence or {}
            if proof.get("valid") is not True or not proof.get("image_urls"):
                unresolved.append({"url": page.url, "status": "table_image_selector_unverified"})
            else:
                image_tables.append({"url": page.url, "media_urls": proof["image_urls"],
                                     "source_kind": source_kind, 'source_evidence': source_evidence})
            observations.append({"url": page.url, "kind": "image_table",
                                 "status": "image_table_pending", "visible_images": proof.get("image_count", 0)})
            continue
        if page.branch.get("kind") == "segmented":
            from server.segmented_roster import reconcile_page
            source_kind = page.branch.get("source_kind") or default_source
            result = reconcile_page(page.html, page.url, page.branch)
            observations.append(result.observation)
            unresolved.extend(result.unresolved)
            segmented_pages[page.url] = page.branch["container_selector"]
            segment_shared[page.url] = result.shared_emails
            for record in result.records + result.pending_ocr:
                record.source_kind = source_kind
                record.source_evidence = source_evidence
            segment_pending.extend(result.pending_ocr)
            for record in result.records:
                segment_records.append(record)
                person = FacultyCandidate(name=record.name, url=page.url, title=record.title)
                candidates.append(person)
                candidate_sources.setdefault((person.name, person.url), set()).add(source_evidence)
            if not result.records and not result.pending_ocr and not result.unresolved:
                unresolved.append({"url": page.url, "status": "no_candidates_on_roster_page"})
            continue
        if "profile_content_start_selector" in page.branch and "profile_content_selector" not in page.branch:
            unresolved.append({"url": page.url, "status": "profile_content_start_selector_without_container"})
        selector = page.branch.get("card_selector")
        empty_declared = "empty_state_selector" in page.branch or "roster_container_selector" in page.branch
        if selector and not empty_declared:
            outside_links = FacultyDirectoryExtractor.links_outside_cards(page.html,page.url,selector)
            if outside_links:
                unresolved.append({'url':page.url,'status':'roster_profile_link_outside_card',
                    'selector':selector,'outside_profile_links':len(outside_links),
                    'sample_urls':outside_links[:4]})
                continue
            from server.name_list import is_name_list_page, reconcile_name_list
            if is_name_list_page(page.html, selector):
                # 补充名单页：卡片是一串顿号分隔的姓名，逐项切分并逐项交代。
                listed = reconcile_name_list(page.html, page.url, selector)
                awaiting = _apply_name_decisions(page.url, listed.found, listed.review,
                                                 name_decisions or {}, unresolved)
                unresolved.extend(listed.unresolved)
                observations.append(listed.observation)
                source_kind = page.branch.get("source_kind") or default_source
                for person in listed.found:
                    candidate_sources.setdefault((person.name, person.url), set()).add(source_evidence)
                if not listed.found and not awaiting:
                    unresolved.append({"url": page.url, "status": "no_candidates_on_roster_page"})
                candidates.extend(listed.found)
                continue
        found, review = FacultyDirectoryExtractor.extract_with_review(
            page.html, page.url, card_selector=selector)
        if selector:
            found, outside = candidates_inside_cards(page.html, page.url, selector, found)
            review, outside_review = candidates_inside_cards(page.html, page.url, selector, review)
            if not found and not empty_declared:
                from server.image_biography import media_references
                cards_html = "".join(str(card) for card in
                                     BeautifulSoup(page.html, "html.parser").select(selector))
                # No person in any card: an embedded viewer or a download link
                # is the roster itself.  A download link's own label ('导师名单
                # 下载') is not a name to review.
                refs = media_references(cards_html, page.url)
                pdf_urls = list(dict.fromkeys(ref.url for ref in refs if ref.kind == "pdf"))
                linked_pdfs = {ref.url for ref in refs if ref.kind == "pdf" and ref.linked}
                if pdf_urls and all(person.url in linked_pdfs for person in review):
                    # The viewer's enclosing DIV is not a teacher record.
                    # Another selector cannot explain people inside a PDF;
                    # retain this source and stop paid structural repair.
                    observation = observe_roster(page.html, page.url, selector, [])
                    observation["status"] = "embedded_pdf_roster_requires_review"
                    observations.append(observation)
                    unresolved.append({"url": page.url, "status": "embedded_pdf_roster_requires_review",
                                       "selector": selector, "media_urls": pdf_urls})
                    pdf_rosters.append({"url": page.url, "selector": selector, "media_urls": pdf_urls,
                                        "source_kind": source_kind, 'source_evidence': source_evidence})
                    continue
            if outside or outside_review:
                # Kept out of the roster, and still reported: the page is not explained.
                unresolved.append({"url": page.url, "status": "candidate_outside_cards",
                                   "count": len(outside) + len(outside_review)})
            # A verified card the name reader cannot place (a transliterated
            # foreign name, say) is a person to confirm, not a selector to redo.
            review.extend(unrecognized_card_names(
                observe_roster(page.html, page.url, selector, found + review), page.url, found + review))
        # Undecided names still account for their cards: the review item, not a
        # card-count mismatch, is what blocks delivery until a person decides.
        awaiting = _apply_name_decisions(page.url, found, review, name_decisions or {}, unresolved)
        source_kind = page.branch.get("source_kind") or default_source
        for person in found:
            candidate_sources.setdefault((person.name, person.url), set()).add(source_evidence)
            if "profile_content_selector" in page.branch and person.url != page.url:
                scope = page.branch["profile_content_selector"]
                start = page.branch.get("profile_content_start_selector")
                if not isinstance(scope, str) or not scope.strip():
                    unresolved.append({"url": person.url, "status": "profile_content_selector_invalid"})
                    continue
                if start is not None and (not isinstance(start, str) or not start.strip()):
                    unresolved.append({"url": person.url, "status": "profile_content_start_selector_invalid"})
                    continue
                profile_content_selectors.setdefault(person.url, set()).add((scope, start))
                evidence = {"roster_url": page.url, "card_selector": page.branch.get("card_selector", ""),
                            "profile_content_selector": scope}
                if start is not None:
                    evidence["profile_content_start_selector"] = start
                sources = profile_content_sources.setdefault(person.url, [])
                if evidence not in sources:
                    sources.append(evidence)

        verified_empty = False
        if empty_declared:
            from server.empty_roster import empty_roster_proof_valid
            verified_empty = empty_roster_proof_valid(page.empty_roster_evidence, page.branch,
                                                     page.html, url=page.url)
            if found or awaiting:
                unresolved.append({"url": page.url, "status": "empty_roster_conflict"})
                verified_empty = False
            elif not verified_empty:
                unresolved.append({"url": page.url, "status": "empty_roster_not_verified"})
        if not selector:
            unresolved.append({"url": page.url, "status": "card_selector_missing"})
        else:
            observation = observe_roster(page.html, page.url, selector, found + awaiting)
            observations.append(observation)
            verified_empty = verified_empty and observation["visible_cards"] == 0
            if verified_empty:
                observation["status"] = "confirmed_empty"
            elif not observation["visible_cards"]:
                unresolved.append(
                    {"url": page.url, "status": "card_selector_empty", "selector": selector}
                )
            elif (
                observation["visible_cards"] != observation["extracted_candidates"]
                or observation["unaccounted_cards"]
            ):
                unresolved.append(
                    {
                        "url": page.url,
                        "status": "roster_card_mismatch",
                        "visible_cards": observation["visible_cards"],
                        "extracted_candidates": observation["extracted_candidates"],
                    }
                )
        if not found and not awaiting and not verified_empty:
            unresolved.append({"url": page.url, "status": "no_candidates_on_roster_page"})
        candidates.extend(found)

    unique = {}
    for candidate in candidates:
        key = (candidate.name, candidate.url)
        previous = unique.get(key)
        conflicts = tuple(dict.fromkeys((previous.mailto_conflicts if previous else ())
                                        + candidate.mailto_conflicts))
        unique[key] = replace(candidate, mailto_conflicts=conflicts)
    # A rare-surname name on a list without personal pages that is also a teacher
    # with a personal page in this college is that teacher (同名合并); the page
    # already carries the evidence, so nobody has to confirm the name again.
    page_urls = {page.url for page in captured_pages}
    with_page = {candidate.name for candidate in candidates if candidate.url not in page_urls}
    # A role-holder ('主任：王玮') collected as a member anywhere in this college
    # is that member (学院内同名合并); only role-line-only people need a person.
    from server.name_list import ROLE_LINE_REASON
    collected = {candidate.name for candidate in candidates}
    unresolved = [item for item in unresolved
                  if not (item.get("status") == "rare_surname_needs_review"
                          and item.get("profile_url") == item.get("url")
                          and (item.get("name") in with_page
                               or (item.get("reason") == ROLE_LINE_REASON
                                   and item.get("name") in collected)))]
    unresolved.extend({"url": url, "status": "profile_content_selector_conflict",
                       "selectors": [{"profile_content_selector": scope,
                                      "profile_content_start_selector": start}
                                     for scope, start in sorted(selectors, key=lambda pair: (pair[0], pair[1] or ''))]}
                      for url, selectors in profile_content_selectors.items() if len(selectors) > 1)
    return RosterReconciliation(
        candidates=list(unique.values()),
        candidate_sources=candidate_sources,
        observations=observations,
        unresolved=unresolved,
        profile_content_selectors=profile_content_selectors,
        profile_content_sources=profile_content_sources,
        segment_records=segment_records,
        segment_pending=segment_pending,
        segment_shared_emails=segment_shared,
        segmented_pages=segmented_pages,
        pdf_rosters=pdf_rosters,
        image_table_sources=image_tables,
    )


def commit_records(
    records: list[dict[str, Any]],
    destination: str,
    *,
    exporter: Callable[[list[dict[str, Any]], str], None],
) -> str:
    """Commit already-audited college records through an injected exporter."""

    if not isinstance(records, list):
        raise TypeError("records must be a list")
    exporter(records, destination)
    return destination
