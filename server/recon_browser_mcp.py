"""Small MCP surface over a real Playwright browser for college reconnaissance.

Complete pages and tool results stay in local evidence. Navigation ranges are
explicit; an oversized result is never reported as a complete observation.
"""
from __future__ import annotations

import asyncio
import argparse
import base64
import json
import hashlib
import sys
import os
import re
import uuid
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

MAX_INLINE_BYTES = 8000
HERE = Path(__file__).resolve().parent


def tool(name: str, description: str, properties: dict, required: tuple = (), *, definitions: dict | None = None) -> dict:
    schema = {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}
    if definitions:
        schema['$defs'] = definitions
    return {"name": name, "description": description, "inputSchema": schema}


STRING = {"type": "string"}
SCREENSHOT_JPEG_QUALITY = 60
PAGE_PREVIEW_CHARS = 600
SURVEY_MAX_URLS = 8
SURVEY_PREVIEW_CHARS = 300
SURVEY_MAX_NEW_LINKS = 20
PROFILE_MAX_URLS = 3
PROFILE_TRANSIENT_REASONS = frozenset({"network_not_idle", "dom_unstable"})
# The second identical resubmission ends the session: the scout is not using the feedback.
UNCHANGED_RESUBMISSIONS_STOP = 2
PROFILE_RETRY_SECONDS = 1.5
PROFILE_CANDIDATES = 4
PROFILE_CANDIDATES_PER_SAMPLE = 3
PROFILE_ALTERNATIVES = 3
SCREENSHOT_MAX_WIDTH = 1280
SCREENSHOT_MAX_HEIGHT = 2000
RANGE = {"selector": STRING, "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 24}}
NAV_RANGE = {"selector": STRING, "offset": {"type": "integer", "minimum": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 64},
             "query": {"type": "string", "description": "Keywords separated by | or spaces; keep only links "
                       "whose label or URL contains one of them."}}
from server.page_types import PageTypeBindings  # noqa: E402
from server.recon_contract import Branch as StageBranch  # noqa: E402
_PAGE_TYPES_SCHEMA = PageTypeBindings.model_json_schema()
_PAGE_TYPES_DEFINITIONS = _PAGE_TYPES_SCHEMA.pop('$defs', {})
_STAGE_BRANCH_SCHEMA = StageBranch.model_json_schema()
_STAGE_BRANCH_DEFINITIONS = _STAGE_BRANCH_SCHEMA.pop('$defs', {})
TOOLS = [
    tool('page_type_catalog', 'Read the trusted Python page-type registry before selecting a mechanism. '
         'Returns registered role/ID/version, structure-only params schema and input/output/failure/applicability '
         'contracts. Follow nextOffset until covered, or filter role/type_id. No website or model access. '
         'Use browser evidence to establish applicability; a catalog entry does not qualify a source or prove '
         'coverage. If no registered mechanism applies, reject it for reviewed Python implementation maintenance.',
         {'role': {'type': 'string', 'enum': ['entrance', 'roster', 'pagination', 'detail']},
          'type_id': STRING, 'offset': {'type': 'integer', 'minimum': 0},
          'limit': {'type': 'integer', 'minimum': 1, 'maximum': 4}}),
    tool('browser_verify_stage', 'Run the selected registered entrance, pagination or detail handler and its '
         'dedicated structure-only proof contract. Choose the binding from page_type_catalog first. '
         'Supply the exact source-qualified branch and type parameters; for detail, urls are exact profile '
         'URLs linked by verified roster cards. Each profile keeps its own captured document and proof. '
         'A legacy proof or another type/version/parameter/URL/document cannot authorize this binding.',
         {'role': {'type': 'string', 'enum': ['entrance', 'pagination', 'detail']},
          'branch': _STAGE_BRANCH_SCHEMA,
          'urls': {'type': 'array', 'items': STRING, 'minItems': 1, 'maxItems': PROFILE_MAX_URLS}},
         ('role', 'branch'), definitions=_STAGE_BRANCH_DEFINITIONS),
    tool("browser_navigate", "Open official URL in the real browser. Returns a page summary (title, headings, common "
         "content containers with counts, and page text lines not yet shown in this session) plus navigation links "
         "not yet returned in this session.",
         {"url": STRING}, ("url",)),
    tool("browser_navigation", "Read navigation URL/label ranges; query filters by keyword. Follow nextOffset until "
         "relevant containers are covered.", NAV_RANGE),
    tool("browser_survey", "Open up to 8 official URLs one after another in the real browser and return, for each, "
         "its final URL, a page summary and links not yet returned in this session. Use it instead of separate "
         "navigate calls when several candidate pages need a look.",
         {"urls": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 8}}, ("urls",)),
    tool("browser_structure", "Read repeated DOM subtree containers and counts; inspect their returned CSS paths.", RANGE),
    tool('browser_verify_source', 'Capture the complete current source scope, actual page purpose title/current navigation and target-college context. '
         'A role enum alone does not prove faculty membership. Unknown/organizational sources remain auxiliary observations.',
         {'source_role': {'type': 'string', 'enum': ['faculty_roster', 'organizational_membership', 'unverified_people_source']},
          'source_container_selector': STRING, 'role_evidence_selector': STRING, 'source_context_selector': STRING,
          'unit_selector': STRING},
         ('source_role', 'source_container_selector', 'role_evidence_selector', 'source_context_selector')),
    tool("browser_verify", "Verify actual card and next-control selectors; returns counts, shapes, sample URLs and "
         "pagination_termination. Guessed current-page navigation links may be natively activated to prove a "
         "same-URL document navigation with an unchanged roster. pagination.total counts candidates; "
         "single_page requires confirmed termination. For explicit heterogeneous child_branches, set "
         "include_child_urls true on the parent's child_link_selector to prove the full selected URL set. "
         "For a registered roster mechanism, provide page_types.roster and its exact structural parameters; "
         "its trusted parser proves token disposition counts instead of assuming one person per cell.",
         {"card_selector": STRING, "next_selector": STRING,
          "kind": {'type': 'string', 'enum': ['roster', 'navigation', 'segmented', 'image_table']},
          "page_types": _PAGE_TYPES_SCHEMA,
          "container_selector": STRING, "record_start_selector": STRING,
          "include_child_urls": {"type": "boolean"},
          "empty_state_selector": STRING, "roster_container_selector": STRING}, ("card_selector",),
          definitions=_PAGE_TYPES_DEFINITIONS),
    tool("browser_verify_segments", "Verify a flat multi-person article: the unique body container and the element "
         "starting each person's record. Returns segment counts, shapes and pagination_termination only.",
         {"container_selector": STRING, "record_start_selector": STRING},
         ("container_selector", "record_start_selector")),
    tool("browser_verify_profile", "Verify a complete profile DOM container and optional direct-child start "
         "boundary; structure only. Pass urls (up to 3 linked profile samples) to open and check each in one call. "
         "Without profile_content_selector it returns candidate containers (CSS path, text size, images, static "
         "check) for each page and the candidates that pass on every page. A failed proof lists hidden_blockers "
         "and passing alternatives.",
         {"profile_content_selector": STRING, "profile_content_start_selector": STRING,
          "urls": {"type": "array", "items": STRING, "minItems": 1, "maxItems": 3}}),
    tool("browser_click", "Click a unique, verified CSS selector in the real browser.", {"selector": STRING}, ("selector",)),
    tool("browser_evaluate", "Run read-only JS only for questions probes cannot answer. Return compact structure. "
         "DOM edits invalidate verification evidence and require reloading the official URL.",
         {"function": STRING}, ("function",)),
    tool("browser_screenshot", "View the current viewport, or one element via selector, as a compressed image when roster "
         "shape cannot be judged from DOM. Images are costly; prefer structure/verify.", {"selector": STRING}),
    tool("browser_read_image", "Read a unique visible img in the current real browser with local OCR, using its "
         "loaded original resource. Returns observed text lines and confidence for source/shape judgment only; "
         "never transcribe them into a plan or treat OCR as roster approval. Follow nextOffset for remaining lines.",
         RANGE, ("selector",)),
]


def phase_tools(phase: str) -> list[dict]:
    from server.recon_contract import submission_schema
    submit = tool("submit_plan", "Submit this phase's structure-only plan. Schema and current-session browser "
                  "evidence are checked immediately. Fix reported paths/codes and retry; stop after accepted:true. "
                  "Query page_type_catalog to choose registered mechanisms and inspect their applicability. "
                  "CSS selectors intentionally cannot contain Chinese. Never include teacher fields.",
                  {"plan": submission_schema(phase)}, ("plan",))
    definitions = submit["inputSchema"]["properties"]["plan"].pop("$defs", None)
    if definitions:
        submit["inputSchema"]["$defs"] = definitions
    if phase == "discovery":
        allowed = {"browser_navigate", "browser_navigation", "browser_survey", "browser_evaluate", "browser_screenshot",
                   "browser_read_image", 'browser_verify_source'}
    elif phase == "branch":
        allowed = {"browser_navigate", "browser_structure", "browser_verify", "browser_verify_segments",
                   "browser_verify_profile", "browser_click", "browser_evaluate", "browser_screenshot", "browser_read_image", 'browser_verify_source', 'page_type_catalog', 'browser_verify_stage'}
    else:
        return [*TOOLS, submit]
    return [*[entry for entry in TOOLS if entry["name"] in allowed], submit]


class BrowserScout:
    def __init__(self, page: Any, evidence_dir: Path, phase: str = "full", target: dict | None = None):
        self.page = page
        self.evidence_dir = evidence_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        self.sequence = 0
        self.phase = phase
        self.target = target
        self.events: list[dict] = []
        self._last_html_digest = None
        self._seen_links: set[str] = set()
        self._seen_lines: set[str] = set()
        self._dom_modified = False
        self._image_reads: dict[str, dict] = {}
        self._observed_forward_selectors: dict[tuple[str, str], set[str]] = {}
        self._unproved_navigation_actions: set[tuple[str, str, str]] = set()
        # Rejected submissions by everything the gate reads: plan, evidence, DOM state.
        self._rejections: dict[tuple, tuple[int, list[dict]]] = {}
        self._unchanged_resubmissions = 0
        from server.empty_roster import install_empty_roster_tracking
        install_empty_roster_tracking(page)

    def save(self, suffix: str, content: str | bytes) -> Path:
        self.sequence += 1
        path = self.evidence_dir / f"{self.sequence:04d}.{suffix}"
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def _page_summary(self, summary: Any, max_chars: int = PAGE_PREVIEW_CHARS) -> Any:
        """Keep only text lines not shown earlier in this session, as a short preview.

        A line counts as shown only once it was returned whole. Lines past the
        preview budget stay unseen, so opening the page again continues where
        the last preview stopped instead of reporting an empty page.
        """
        if not isinstance(summary, dict) or not isinstance(summary.get("lines"), list):
            return summary
        fresh, used, repeated, unshown = [], 0, 0, 0
        for line in summary.pop("lines"):
            if line in self._seen_lines:
                repeated += 1
                continue
            if used >= max_chars:
                unshown += 1
                continue
            piece = line[:max_chars - used]
            fresh.append(piece)
            used += len(piece)
            if piece == line:
                self._seen_lines.add(line)
            else:
                unshown += 1
        summary["new_text"] = "\n".join(fresh)
        summary["new_text_truncated"] = unshown > 0
        summary["unshown_lines"] = unshown
        summary["repeated_lines_omitted"] = repeated
        return summary

    def _omit_seen_links(self, navigation: Any) -> Any:
        """Site menus repeat on every page; return each link once per session."""
        if not isinstance(navigation, dict) or not isinstance(navigation.get("items"), list):
            return navigation
        fresh = [item for item in navigation["items"] if item.get("url") not in self._seen_links]
        self._seen_links.update(item.get("url") for item in navigation["items"])
        omitted = len(navigation["items"]) - len(fresh)
        if omitted:
            navigation = {**navigation, "items": fresh, "seen_omitted": omitted}
        return navigation

    def text_result(self, value: Any) -> dict:
        full = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        evidence = self.save("json", full)
        if isinstance(value, dict):
            value = json.loads(full)
            ranges = [node for node in [value, *value.values()] if isinstance(node, dict) and isinstance(node.get("items"), list)]
            while len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > MAX_INLINE_BYTES:
                candidates = [node for node in ranges if len(node["items"]) > 1]
                if not candidates:
                    break
                node = max(candidates, key=lambda node: len(json.dumps(node["items"]).encode("utf-8")))
                node["items"].pop()
                node["end"] = node["offset"] + len(node["items"])
                node["nextOffset"] = node["end"] if node["end"] < node["total"] else None
            full = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if len(full.encode("utf-8")) > MAX_INLINE_BYTES:
            full = json.dumps({"status": "result_too_large", "complete": False,
                "evidence_path": str(evidence), "retry": "Use a narrower container, smaller limit, or compact JS result."})
        return {"content": [{"type": "text", "text": full}], "isError": False}

    async def snapshot(self) -> None:
        html = await self.page.content()
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        if digest != self._last_html_digest:  # repeated probes of one page kept 30+ identical copies
            self.save("html", html)
            self._last_html_digest = digest
        if self.phase == "discovery":
            from server.recon_plan_cache import navigation_evidence
            self.save("discovery.json", json.dumps(await navigation_evidence(self.page), ensure_ascii=False))

    async def call(self, name: str, arguments: dict) -> dict:
        if name == "submit_plan":
            from server.recon_contract import plan_digest, validate_submission
            plan = arguments.get("plan")
            if self._dom_modified:
                return {**self.text_result({"accepted": False, "issues": [
                    {"path": "$", "status": "browser_dom_modified"}]}), "isError": True}
            # The gate is a function of the plan and this session's evidence, so an
            # identical resubmission with no new browser call cannot pass.
            key = (plan_digest(plan), len(self.events))
            if key in self._rejections:
                self._unchanged_resubmissions += 1
                number, issues = self._rejections[key]
                reply = {"accepted": False, "unchanged_resubmission": number, "issues": issues,
                         "retry": "Same plan and no new browser evidence since this submission was rejected. "
                                  "Collect the proof each issue's need names before submitting again."}
                if self._unchanged_resubmissions >= UNCHANGED_RESUBMISSIONS_STOP:
                    reply["stop"] = "recon_gate_stalled"
                return {**self.text_result(reply), "isError": True}
            issues = validate_submission(plan, self.phase, self.target, self.events)
            if issues:
                self._rejections[key] = (len(self._rejections) + 1, issues)
                reply = {"accepted": False, "submission": len(self._rejections), "issues": issues,
                         "retry": "Each issue names its url, failed_condition, closest observation and the "
                                  "proof it still needs: collect that proof in the browser, then resubmit."}
                if all(item.get("page_only") for item in issues):
                    # Every failure is a page fact seen on two opens; no plan passes it.
                    reply["stop"] = "recon_page_unverifiable"
                return {**self.text_result(reply), "isError": True}
            self.save("accepted.json", json.dumps(plan, ensure_ascii=False))
            return self.text_result({"accepted": True, "digest": plan_digest(plan)})
        try:
            reply = await self._browser_call(name, arguments)
        except Exception as exc:
            if name != 'browser_navigate':
                raise
            reply = {**self.text_result({'requested_url': arguments['url'],
                                         'error': type(exc).__name__}), 'isError': True}
        cid = str(len(self.events))
        self.events.extend([
            {"type": "tool/call", "data": {"callId": cid, "name": "mcp__playwright__" + name,
                                            "arguments": json.dumps(arguments)}},
            {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": cid, **reply}]}}},
        ])
        return reply

    async def _people(self) -> dict | None:
        """Whether the page lists people, as counts only (names stay local)."""
        try:
            from server.headcount_check import people_signals
            return people_signals(await self.page.content())
        except Exception:
            return None

    async def _profile_here(self, selector: str | None, start: str | None, *, candidate_limit: int) -> dict:
        from server.profile_structure import failure_details, profile_candidates, verify_profile_container
        if not selector:
            await self.snapshot()
            return {"url": self.page.url, "candidates": await profile_candidates(
                self.page, html=await self.page.content(), limit=candidate_limit)}
        captured = {}
        value = await verify_profile_container(self.page, selector, start, local_capture=captured)
        if value.get("reason") in PROFILE_TRANSIENT_REASONS:
            # Late requests or a settling page pass with time; opening the page
            # again here saves the scout a round trip for a condition no selector
            # changes, and a request failing the same way twice is the published page.
            failed = captured.get("failed_requests")
            missing = captured.get('missing_media_failures')
            await asyncio.sleep(PROFILE_RETRY_SECONDS)
            await self.page.reload(wait_until="domcontentloaded", timeout=30000)
            captured = {}
            value = await verify_profile_container(self.page, selector, start, local_capture=captured,
                                                   recurring_failures=failed, recurring_missing_media=missing)
        if "html" in captured:
            self.save("html", captured["html"])
        else:
            await self.snapshot()
        if value.get("confirmed") is not True:
            html = captured.get("html") or await self.page.content()
            if value.get("scope_issue") and "settle_samples" not in value:
                # The live checks never ran; their initial values are not observations.
                for key in ("container_count", "container_visible", "dom_stable", "network_idle"):
                    value.pop(key, None)
                value["live_checks"] = "not_run_after_static_issue"
            value.update(await failure_details(self.page, html, value))
            alternatives = [item for item in await profile_candidates(self.page, selector, html=html)
                            if item.get("static_check") == "ok"]
            if alternatives:
                value["alternatives"] = alternatives[:PROFILE_ALTERNATIVES]
        return value

    async def _verify_profiles(self, arguments: dict) -> dict:
        selector = arguments.get("profile_content_selector") or None
        start = arguments.get("profile_content_start_selector") or None
        if start and not selector:
            raise ValueError("profile_content_start_selector requires profile_content_selector")
        urls = arguments.get("urls")
        if not urls:
            return await self._profile_here(selector, start, candidate_limit=PROFILE_CANDIDATES)
        results = []
        for url in list(urls)[:PROFILE_MAX_URLS]:
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                results.append({"requested_url": url, "error": "http(s) URL required"})
                continue
            try:
                await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                item = await self._profile_here(selector, start, candidate_limit=10)
            except Exception as exc:  # one unreachable sample must not hide the others
                item = {"url": self.page.url, "error": f"{type(exc).__name__}: {exc}"[:200]}
            results.append({"requested_url": url, **item})
        value = {"url": self.page.url, "results": results}
        if selector:
            value["confirmed_count"] = sum(item.get("confirmed") is True for item in results)
            return value
        # A selector string that names a passing container on every sample page;
        # an unreadable sample contributes no candidates, never partial support.
        pages = [[selector for entry in item.get("candidates", []) if entry.get("static_check") == "ok"
                  for selector in entry["selectors"]] for item in results]
        value["common_candidates"] = ([selector for selector in dict.fromkeys(pages[0])
                                       if all(selector in other for other in pages[1:])][:PROFILE_CANDIDATES]
                                      if pages else [])
        for item in results:
            if "candidates" in item:
                item["candidates"] = item["candidates"][:PROFILE_CANDIDATES_PER_SAMPLE]
        return value

    async def _browser_call(self, name: str, arguments: dict) -> dict:
        if name == 'page_type_catalog':
            from server.page_types import catalog
            entries = [item for item in catalog() if (
                not arguments.get('role') or item['role'] == arguments['role']) and (
                not arguments.get('type_id') or item['type_id'] == arguments['type_id'])]
            offset, limit = arguments.get('offset', 0), arguments.get('limit', 2)
            if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 4:
                raise ValueError('catalog requires a nonnegative offset and limit 1..4')
            end = min(offset + limit, len(entries))
            return self.text_result({'items': entries[offset:end], 'offset': offset, 'end': end,
                'total': len(entries), 'nextOffset': end if end < len(entries) else None})
        if self._dom_modified and name in {
            "browser_verify", "browser_verify_segments", "browser_verify_profile", "browser_click", "browser_read_image", 'browser_verify_source', 'browser_verify_stage'
        }:
            return {**self.text_result({"status": "browser_dom_modified",
                                       "retry": "Reload the official URL before collecting new verification evidence."}),
                    "isError": True}
        if name == 'browser_verify_stage':
            from server.agent_recon import _is_structure_url, validate_model_plan
            from server.page_types import normalize_branch, run_stage_probe
            from server.harvest_workflow import prepare_entrance_stage, capture_profile_stage
            from server.browser_batch_tool import _capture_branch
            branch = normalize_branch(arguments['branch'])
            validate_model_plan({'school': (self.target or {}).get('school', ''),
                'college': (self.target or {}).get('college', ''), 'branches': [branch]},
                school=(self.target or {}).get('school', ''), college=(self.target or {}).get('college', ''))
            role = arguments['role']
            if role not in {'entrance', 'pagination', 'detail'}:
                raise ValueError('unsupported stage role')
            urls = arguments.get('urls')
            if role == 'detail':
                if (not isinstance(urls, list) or not 1 <= len(urls) <= PROFILE_MAX_URLS
                        or not all(_is_structure_url(url) for url in urls) or len(urls) != len(set(urls))):
                    raise ValueError('detail requires exact unique profile URLs')
            elif urls is not None:
                raise ValueError('profile URLs apply only to detail')
            if hasattr(self.page, 'raw_page'):
                from server.browser_context import IsolatedBrowserContext
                context = IsolatedBrowserContext(self.page._context, polite=self.page.polite)
            else:
                context = self.page.context
            results = []
            for requested_url in urls if role == 'detail' else [branch['url']]:
                capture, proof = {}, None
                try:
                    if role == 'entrance':
                        proof = await run_stage_probe(role, branch, prepare_entrance_stage, context, branch,
                                                      requested_url=requested_url, local_capture=capture)
                    elif role == 'pagination':
                        proof = await run_stage_probe(role, branch, _capture_branch, context, branch,
                            source_target=self.target or {}, requested_url=requested_url, local_capture=capture)
                    else:
                        proof = await run_stage_probe(role, branch, capture_profile_stage, context, requested_url, branch,
                            scope_pairs={(branch['profile_content_selector'], branch.get('profile_content_start_selector'))},
                            page_ready_selector=branch.get('profile_ready_selector'),
                            requested_url=requested_url, local_capture=capture)
                except ValueError:
                    pass  # Keep the actual failed capture; never echo people from an exception.
                result = capture.get('result')
                if role == 'pagination':
                    documents = result[0] if isinstance(result, tuple) and len(result) == 3 else []
                else:
                    html = getattr(result, 'html', None)
                    meta = getattr(result, 'meta' if role == 'detail' else 'evidence', {})
                    final_url = meta.get('final_url') or meta.get('url') or requested_url
                    documents = [(final_url, html)] if isinstance(html, str) and html else []
                originals = [{'url': url, 'source_html_sha256': hashlib.sha256(html.encode()).hexdigest(),
                    'evidence_path': str(self.save(f'stage-{role}-source.html', html.encode()))}
                             for url, html in documents]
                metadata = None
                if result is not None:
                    if role == 'pagination':
                        data = {'role': role, 'errors': result[1], 'evidence': result[2]}
                    elif role == 'entrance':
                        data = {'role': role, 'source_urls': result.source_urls,
                                'errors': result.errors, 'evidence': result.evidence}
                    else:
                        data = {'role': role, 'errors': result.errors, 'proof': result.proof, 'meta': result.meta}
                    raw_metadata = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
                    metadata = {'sha256': hashlib.sha256(raw_metadata).hexdigest(),
                                'evidence_path': str(self.save(f'stage-{role}-capture.json', raw_metadata))}
                results.append({'requested_url': requested_url, 'originals': originals, 'capture_metadata': metadata,
                    'stage_evidence': proof, **({'status': 'stage_capture_unverified'} if proof is None else {})})
            packet = {'role': role, 'results': results}
            encoded = json.dumps(packet, ensure_ascii=False, allow_nan=False).encode()
            if len(encoded) > MAX_INLINE_BYTES:
                packet = {'role': role, 'full_stage_result': {
                    'sha256': hashlib.sha256(encoded).hexdigest(),
                    'evidence_path': str(self.save(f'stage-{role}-result.json', encoded))}}
            return self.text_result(packet)
        if name == "browser_navigate":
            url = arguments["url"]
            if not url.startswith(("http://", "https://")):
                raise ValueError("http(s) URL required")
            response = await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._dom_modified = False
            await self.snapshot()
            if self.phase == "discovery":
                value = await self.page.evaluate(
                    "() => ({url: location.href, page: __facultyRecon.summary(), navigation: __facultyRecon.navigation('body',0,64)})")
                value["navigation"] = self._omit_seen_links(value["navigation"])
                value["page"] = self._page_summary(value["page"])
                value["page"]["people"] = await self._people()
            elif self.phase == "branch":
                value = await self.page.evaluate("() => ({url:location.href,page:__facultyRecon.summary(),structure:__facultyRecon.structure('body',0,8),pagination:__facultyRecon.pagination()})")
                value["page"] = self._page_summary(value["page"])
            else:
                value = await self.page.evaluate("() => ({url:location.href,navigation:__facultyRecon.navigation('body',0,24),structure:__facultyRecon.structure('body',0,8)})")
            html = await self.page.content()
            value.update(requested_url=url, status=response.status if response else None,
                         source_html_sha256=hashlib.sha256(html.encode('utf-8')).hexdigest(),
                         original_path=str(self.save('source.html', html.encode('utf-8'))))
        elif name == "browser_survey":
            pages = []
            for url in list(arguments["urls"])[:SURVEY_MAX_URLS]:
                if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                    pages.append({"url": url, "error": "http(s) URL required"})
                    continue
                try:
                    response = await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    self._dom_modified = False
                    await self.snapshot()
                    probe = await self.page.evaluate(
                        "() => ({page: __facultyRecon.summary(), navigation: __facultyRecon.navigation('body',0,64)})")
                    links = self._omit_seen_links(probe["navigation"])
                    items = links.get("items", []) if isinstance(links, dict) else []
                    summary = self._page_summary(probe["page"], SURVEY_PREVIEW_CHARS)
                    if isinstance(summary, dict):
                        summary["people"] = await self._people()
                    html = await self.page.content()
                    pages.append({"url": url, "final_url": self.page.url,
                                  'source_html_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
                                  'original_path': str(self.save('source.html', html.encode('utf-8'))),
                                  "status": response.status if response is not None else None,
                                  "page": summary,
                                  "new_links": items[:SURVEY_MAX_NEW_LINKS],
                                  "more_new_links": max(0, len(items) - SURVEY_MAX_NEW_LINKS),
                                  "links_total": links.get("total") if isinstance(links, dict) else None})
                except Exception as exc:  # one unreachable page must not hide the others
                    pages.append({"url": url, "error": f"{type(exc).__name__}: {exc}"[:200]})
            value = {"url": self.page.url, "pages": {"offset": 0, "end": len(pages), "total": len(pages),
                                                     "nextOffset": None, "items": pages}}
        elif name in ("browser_navigation", "browser_structure"):
            method = "navigation" if name == "browser_navigation" else "structure"
            values = [arguments.get("selector", "body"), arguments.get("offset", 0),
                      arguments.get("limit", 64 if name == "browser_navigation" else 24)]
            if name == "browser_navigation" and arguments.get("query"):
                values.append(arguments["query"])
            value = await self.page.evaluate(f"args => __facultyRecon.{method}(...args)", values)
            if self.phase == "discovery":
                await self.snapshot()
        elif name == 'browser_verify_source':
            from server.source_qualification import verify_source
            captured = {}
            value = await verify_source(self.page, arguments, self.target or {}, local_capture=captured)
            if captured.get('html'):
                value['original_path'] = str(self.save('source.html', captured['html'].encode('utf-8')))
            witness_path = self.save('source-witness.json', json.dumps(value, ensure_ascii=False))
            value['evidence_path'] = str(witness_path)
            await self.snapshot()
        elif name == "browser_verify":
            from server.page_types import binding_for, normalize_branch, ROLE_PARAMETERS
            # This tool proves the roster mechanism, not the applicability of
            # unrelated stage bindings whose structural fields it does not take.
            refs = PageTypeBindings.model_validate(arguments.get('page_types') or {}).model_dump(exclude_none=True)
            proof_branch = normalize_branch({**{key: value for key, value in arguments.items()
                if key not in {'include_child_urls', 'page_types'}}, 'url': self.page.url,
                'page_types': {'roster': refs['roster']} if 'roster' in refs else {}})
            from server.agent_recon import check_plan_hygiene
            check_plan_hygiene({'branches': [proof_branch]},
                               school=(self.target or {}).get('school', ''),
                               college=(self.target or {}).get('college', ''))
            roster_binding = binding_for(proof_branch, 'roster')
            legacy_roster = (roster_binding.type_id, roster_binding.implementation_version) in {
                ('roster.cards', 1), ('roster.segmented', 1), ('roster.image_table', 1)}
            specialized_type = {'image_table': 'roster.image_table', 'segmented': 'roster.segmented'}.get(
                proof_branch.get('kind'))
            if proof_branch.get('empty_state_selector') or proof_branch.get('roster_container_selector'):
                specialized_type = 'roster.cards'
            if specialized_type and (roster_binding.type_id, roster_binding.implementation_version) != (specialized_type, 1):
                return {**self.text_result({'status': 'specialized_roster_version_not_applicable',
                    'retry': 'Dedicated image/segment/empty browser proof supports only its exact legacy '
                             'roster type at version 1; a new version requires a reviewed proof mechanism.'}),
                    'isError': True}
            # Binding parameters are the actual selectors, not merely metadata.
            arguments = {**arguments, **{key: value for key, value in proof_branch.items()
                                        if key in set().union(*ROLE_PARAMETERS.values())}}
            value = await self.page.evaluate("args => __facultyRecon.check(...args)",
                                            [arguments["card_selector"], arguments.get("next_selector")])
            forward_key = (self.page.url, arguments['card_selector'])
            known_forward = self._observed_forward_selectors.setdefault(forward_key, set())
            if arguments.get('next_selector') and value.get('nextCount', 0) > 0:
                known_forward.add(arguments['next_selector'])
            def visibility_error():
                return {**self.text_result({**value, "status": "roster_cards_not_visible",
                    "retry": "Verify a selector for the visible roster rather than hidden or preload nodes."}),
                    "isError": True}
            if value.get("hiddenCardCount", 0) > 0:
                return visibility_error()
            from server.nonpagination_controls import (nonpagination_evidence, nonpagination_evidence_valid,
                                                       observe_current_navigation, without_nonpagination_controls)
            pagination = await self.page.evaluate("selector => __facultyRecon.pagination('body',0,12,false,selector)",
                                                   arguments['card_selector'])
            navigations = {}
            candidates = known_forward | {item['selector'] for item in pagination.get('items', [])}
            for selector in sorted(candidates):
                action_key = (*forward_key, selector)
                if action_key in self._unproved_navigation_actions:
                    continue
                proof, activated = await observe_current_navigation(self.page, arguments['card_selector'], selector)
                if proof is not None:
                    navigations[selector] = proof
                elif activated:
                    # No repeat clicks on an unknown action: they may advance a
                    # real AJAX roster. Its remembered control remains blocking.
                    self._unproved_navigation_actions.add(action_key)
            if self.page.url != forward_key[0]:
                return {**self.text_result({'url':self.page.url, 'status':'roster_changed_during_verification',
                    'retry':'Verify the current source again; the navigation probe changed its URL.'}), 'isError':True}
            value = await self.page.evaluate('args => __facultyRecon.check(...args)',
                                            [arguments['card_selector'], arguments.get('next_selector')])
            if value.get('hiddenCardCount', 0) > 0:
                return visibility_error()
            forward_observations = []
            for selector in sorted(known_forward):
                current = await self.page.evaluate('args => __facultyRecon.check(...args)',
                                                  [arguments['card_selector'], selector])
                forward_observations.append({'selector':selector, 'count':current.get('nextCount'),
                                             'disabled':current.get('nextDisabled')})
            cards = await self.page.locator(arguments["card_selector"]).evaluate_all("nodes => nodes.map(n=>n.outerHTML)")
            if cards and all(card.lstrip().lower().startswith("<img") for card in cards):
                from server.image_table_browser import verify_table_images
                value["image_table"] = await verify_table_images(self.page, arguments["card_selector"])
            elif cards:
                # Picture cards are read from their images later; every other card
                # must be one the harvest reads. Counts only: names stay local.
                from server.harvest_workflow import roster_card_reading
                html = await self.page.content()
                from server.faculty_extractor import FacultyDirectoryExtractor
                introductions = FacultyDirectoryExtractor.profile_introduction_links(
                    html, self.page.url, arguments["card_selector"])
                value["profile_intro_link_count"] = len(introductions)
                mismatch = roster_card_reading(html, self.page.url, arguments["card_selector"], branch=proof_branch)
                if mismatch and (roster_binding.type_id, roster_binding.implementation_version) == ('roster.cards', 1):
                    value["card_mismatch"] = mismatch
            value["cardDigest"] = hashlib.sha256(json.dumps(cards).encode("utf-8")).hexdigest()
            value["pagination"] = without_nonpagination_controls(await self.page.content(), self.page.url, arguments["card_selector"],
                await self.page.evaluate("selector => __facultyRecon.pagination('body',0,12,false,selector)",
                                         arguments["card_selector"]), navigations)
            from server.pager_evidence import terminal_pagers, without_terminal_pagers
            terminal = await terminal_pagers(self.page, arguments['card_selector'])
            current_cards = await self.page.locator(arguments['card_selector']).evaluate_all(
                "nodes => nodes.map(n=>n.outerHTML)")
            if cards != current_cards or value['url'] != self.page.url:
                return {**self.text_result({'url': self.page.url, 'status': 'roster_changed_during_verification',
                    'retry': 'Verify the current roster again; the forward click changed its source.'}),
                    'isError': True}
            value['pagination'] = without_terminal_pagers(
                await self.page.content(), value['pagination'], terminal, arguments['card_selector'])
            from server.browser_batch_tool import single_page_evidence
            from server.pager_evidence import proof_valid
            html = await self.page.content()
            if not legacy_roster:
                # Bind the reading to the exact final original, including rank
                # headings outside the selected name cells.
                from server.harvest_workflow import roster_card_reading
                reading = roster_card_reading(html, self.page.url, arguments['card_selector'], branch=proof_branch)
                value['roster_type_evidence'] = {
                    'type_id': roster_binding.type_id,
                    'implementation_version': roster_binding.implementation_version,
                    'selectors': {key: proof_branch[key] for key in ROLE_PARAMETERS['roster'] if key in proof_branch},
                    'source_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
                    'reading': reading,
                }
                from server.page_types import implementation_digest
                value['roster_type_evidence']['implementation_digest'] = implementation_digest(roster_binding, 'roster')
            ignored = [p['control_selector'] for p in terminal if proof_valid(p, html, arguments['card_selector'])]
            for item in forward_observations:
                item['terminal'] = without_terminal_pagers(html, {
                    'items': [{'selector': item['selector']}], 'total': 1},
                    terminal, arguments['card_selector'])['total'] == 0
                nonpagination = nonpagination_evidence(html, self.page.url, arguments['card_selector'], item['selector'],
                                                       navigations.get(item['selector']))
                if nonpagination is not None:
                    item['nonpagination'] = nonpagination
            value['pagination_termination'] = single_page_evidence(html, self.page.url, arguments['card_selector'], ignored)
            value['pagination_termination']['observed_forward_controls'] = forward_observations
            value['pagination_termination']['evidence_path'] = str(self.save('pagination-source.html', html.encode('utf-8')))
            if arguments.get('include_child_urls') is True:
                # Match the actual navigation reader: selected nodes must carry
                # their own href. Samples cannot stand for an unbounded URL set.
                from bs4 import BeautifulSoup
                from urllib.parse import urljoin
                from server.agent_recon import _is_structure_url
                selected = BeautifulSoup(html, 'html.parser').select(arguments['card_selector'])
                child_urls, invalid = [], 0
                for node in selected:
                    href = node.get('href')
                    child_url = urljoin(self.page.url, href) if isinstance(href, str) and href.strip() else None
                    if not _is_structure_url(child_url):
                        invalid += 1
                    elif child_url not in child_urls:
                        child_urls.append(child_url)
                value['child_urls'] = child_urls
                value['child_url_evidence'] = {
                    'url': self.page.url, 'selector': arguments['card_selector'],
                    'source_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
                    'discovery_scope': 'current_rendered_navigation_links',
                    'complete': bool(selected) and invalid == 0,
                    'invalid_links': invalid, 'selected_count': len(selected),
                }
            if value['pagination'].get('total', 0) > 0 or any(
                    item['count'] != 0 and item['disabled'] is not True and not item['terminal']
                    and not nonpagination_evidence_valid(item, self.page.url, arguments['card_selector'],
                        value['pagination_termination']['source_sha256'])
                    for item in forward_observations):
                value['pagination_termination']['confirmed'] = False
                value['pagination_termination']['unresolved_forward_controls'] = max(
                    value['pagination_termination']['unresolved_forward_controls'], value['pagination']['total'], 1)
            if "empty_state_selector" in arguments or "roster_container_selector" in arguments:
                from server.empty_roster import verify_empty_roster
                branch = {**arguments, "url": self.page.url, "single_page": True}
                value["empty_roster"] = await verify_empty_roster(self.page, branch, await self.page.content())
                value["pagination"] = await self.page.evaluate(
                    "selector => __facultyRecon.pagination('body',0,12,true,selector)",
                    arguments.get("roster_container_selector") or arguments["card_selector"])
                await self.snapshot()
        elif name == "browser_verify_segments":
            from server.segmented_roster import verification_summary
            html = await self.page.content()
            value = verification_summary(html, self.page.url, arguments["container_selector"],
                                         arguments["record_start_selector"])
            value["pagination"] = await self.page.evaluate(
                "selector => __facultyRecon.pagination('body',0,12,false,selector)", arguments["container_selector"])
            from server.browser_batch_tool import single_page_evidence
            value['pagination_termination'] = single_page_evidence(html, self.page.url, arguments['container_selector'])
            value['pagination_termination']['evidence_path'] = str(self.save('pagination-source.html', html.encode('utf-8')))
            if value['pagination'].get('total', 0) > 0:
                value['pagination_termination']['confirmed'] = False
                value['pagination_termination']['unresolved_forward_controls'] = max(
                    value['pagination_termination']['unresolved_forward_controls'], value['pagination']['total'])
            await self.snapshot()
        elif name == "browser_verify_profile":
            value = await self._verify_profiles(arguments)
        elif name == "browser_read_image":
            from server.image_biography import fetch_browser_asset, scan_media
            target = self.page.locator(arguments["selector"])
            if await target.count() != 1 or not await target.is_visible():
                raise ValueError("image selector must match exactly one visible img")
            if await target.evaluate("node => node.tagName") != "IMG":
                raise ValueError("image selector must match an img")
            await target.scroll_into_view_if_needed(timeout=10000)
            await self.page.wait_for_function("selector => {const img=document.querySelector(selector); "
                "return img && img.complete && img.naturalWidth > 0;}",
                arg=arguments["selector"], timeout=10000)
            image = await target.evaluate("img => ({url: img.currentSrc || img.src, "
                                          "width: img.naturalWidth, height: img.naturalHeight})")
            if not image["url"].startswith(("http://", "https://")):
                raise ValueError("image original requires a browser-observed http(s) resource")
            await self.snapshot()
            data = await fetch_browser_asset(self.page.context, image["url"])
            digest = hashlib.sha256(data).hexdigest()
            if digest not in self._image_reads:
                original = self.save("image", data)
                scan = await asyncio.to_thread(scan_media, data, "image")
                evidence = self.save("ocr.json", json.dumps(scan, ensure_ascii=False))
                self._image_reads[digest] = {"scan": scan, "original_path": str(original), "ocr_path": str(evidence)}
            saved = self._image_reads[digest]
            lines = saved["scan"]["pages"][0]["lines"]
            offset = min(len(lines), max(0, int(arguments.get("offset", 0))))
            limit = min(24, max(1, int(arguments.get("limit", 24))))
            end = min(len(lines), offset + limit)
            value = {"url": self.page.url, "image": image, "source": "local_ocr", "sha256": digest,
                     "original_path": saved["original_path"], "ocr_path": saved["ocr_path"],
                     "total": len(lines), "offset": offset, "end": end,
                     "nextOffset": end if end < len(lines) else None, "items": lines[offset:end]}
        elif name == "browser_click":
            target = self.page.locator(arguments["selector"])
            if await target.count() != 1:
                raise ValueError("click selector must match exactly one element")
            await target.click(timeout=15000)
            await self.page.wait_for_load_state("domcontentloaded", timeout=15000)
            await self.snapshot()
            value = {"url": self.page.url}
        elif name == "browser_evaluate":
            observation = await self.page.evaluate("""async source => {
                const probe = window.__facultyRecon;
                let modified = false;
                const observer = new MutationObserver(records => {modified ||= records.length > 0;});
                observer.observe(document, {subtree:true,childList:true,attributes:true,characterData:true});
                let value, failed = false;
                try {
                    const fn = (0,eval)('(' + source + ')');
                    value = await (typeof fn === 'function' ? fn() : fn);
                } catch (_) {failed = true;}
                finally {
                    modified ||= observer.takeRecords().length > 0 || window.__facultyRecon !== probe;
                    observer.disconnect();
                }
                return {value, modified, failed};
            }""", arguments["function"])
            await self.snapshot()
            if observation["modified"]:
                # Previous selector/pagination proofs cannot certify an edited
                # browser document. Require an official reload and fresh proof.
                self._dom_modified = True
                self.events.clear()
                return {**self.text_result({"status": "browser_dom_modified",
                                           "retry": "Use read-only probes. Reload the official URL and verify again."}),
                        "isError": True}
            if observation["failed"]:
                return {**self.text_result({"status": "browser_evaluate_failed"}), "isError": True}
            value = observation.get("value")
        elif name == "browser_screenshot":
            # Full-page PNGs (e.g. 1280x19624, 3.7 MB) were sent to the model and then
            # re-sent with every later request of the session. Send one bounded JPEG.
            options = {"type": "jpeg", "quality": SCREENSHOT_JPEG_QUALITY, "scale": "css"}
            if arguments.get("selector"):
                target = self.page.locator(arguments["selector"]).first
                await target.scroll_into_view_if_needed(timeout=10000)
                box = await target.bounding_box()
                if box is None:
                    raise ValueError("screenshot selector is not visible")
                scroll = await self.page.evaluate("() => ({x: window.scrollX, y: window.scrollY})")
                options["clip"] = {"x": box["x"] + scroll["x"], "y": box["y"] + scroll["y"],
                                   "width": min(box["width"], SCREENSHOT_MAX_WIDTH),
                                   "height": min(box["height"], SCREENSHOT_MAX_HEIGHT)}
                options["full_page"] = True
            data = await self.page.screenshot(**options)
            evidence = self.save("jpg", data)
            return {"content": [{"type": "text", "text": json.dumps({"evidence_path": str(evidence)})},
                                {"type": "image", "mimeType": "image/jpeg", "data": base64.b64encode(data).decode()}]}
        else:
            raise ValueError("unknown browser tool")
        return self.text_result(value)


async def serve(*, phase: str | None = None, session: str | None = None, target: dict | None = None) -> None:
    from server.browser_context import new_browser_context
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await new_browser_context(browser)
            configured_phase = phase or os.environ.get("FACULTY_RECON_PHASE")
            phase = configured_phase or 'full'
            tools = phase_tools(phase)
            if not configured_phase:
                # Preserve the standalone MCP protocol's original tool surface.
                # Production runtime patches explicitly select branch/full and
                # retain the registry catalog for mechanism selection.
                tools = [entry for entry in tools if entry['name'] not in {'page_type_catalog', 'browser_verify_stage'}]
            session = session or os.environ.get("FACULTY_RECON_SESSION", "standalone")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", session):
                session = hashlib.sha256(session.encode()).hexdigest()
            from server.agent_recon import recon_evidence_root
            scout = BrowserScout(await context.new_page(), recon_evidence_root() / session / uuid.uuid4().hex,
                                 phase, target)
            while line := await asyncio.to_thread(sys.stdin.readline):
                request = json.loads(line)
                if "id" not in request:  # MCP initialization/cancellation notifications.
                    continue
                method, params = request.get("method"), request.get("params") or {}
                response = {"jsonrpc": "2.0", "id": request["id"]}
                try:
                    if method == "initialize":
                        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                                  "serverInfo": {"name": "faculty-browser", "version": "1.0.0"}}
                    elif method == "ping":
                        result = {}
                    elif method == "tools/list":
                        result = {"tools": tools}
                    elif method == "tools/call":
                        try:
                            if params["name"] not in {entry["name"] for entry in tools}:
                                raise ValueError("tool unavailable in this phase")
                            result = await scout.call(params["name"], params.get("arguments") or {})
                        except Exception as exc:
                            result = {"isError": True, "content": [{"type": "text", "text": json.dumps({
                                "status": "browser_tool_failed", "error_type": type(exc).__name__,
                                "retry": "Check current URL, DOM, and selector."})}]}
                    else:
                        response["error"] = {"code": -32601, "message": "Method not found"}
                        result = None
                    if "error" not in response:
                        response["result"] = result
                except (KeyError, TypeError, ValueError):
                    response["error"] = {"code": -32602, "message": "Invalid parameters"}
                print(json.dumps(response, ensure_ascii=False), flush=True)
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Real browser MCP for faculty structural reconnaissance")
    parser.add_argument("--phase", choices=("full", "discovery", "branch"))
    parser.add_argument("--session")
    parser.add_argument("--target", type=json.loads)
    args = parser.parse_args()
    asyncio.run(serve(phase=args.phase, session=args.session, target=args.target))
