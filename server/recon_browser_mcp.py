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


def tool(name: str, description: str, properties: dict, required: tuple = ()) -> dict:
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties, "required": list(required), "additionalProperties": False}}


STRING = {"type": "string"}
SCREENSHOT_JPEG_QUALITY = 60
PAGE_PREVIEW_CHARS = 600
SURVEY_MAX_URLS = 8
SURVEY_PREVIEW_CHARS = 300
SURVEY_MAX_NEW_LINKS = 20
SCREENSHOT_MAX_WIDTH = 1280
SCREENSHOT_MAX_HEIGHT = 2000
RANGE = {"selector": STRING, "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 24}}
NAV_RANGE = {"selector": STRING, "offset": {"type": "integer", "minimum": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 64},
             "query": {"type": "string", "description": "Keywords separated by | or spaces; keep only links "
                       "whose label or URL contains one of them."}}
TOOLS = [
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
    tool("browser_verify", "Verify actual card and next-control selectors; returns counts, shapes and sample URLs.",
         {"card_selector": STRING, "next_selector": STRING,
          "empty_state_selector": STRING, "roster_container_selector": STRING}, ("card_selector",)),
    tool("browser_verify_segments", "Verify a flat multi-person article: the unique body container and the element "
         "starting each person's record. Returns segment counts and shapes only.",
         {"container_selector": STRING, "record_start_selector": STRING},
         ("container_selector", "record_start_selector")),
    tool("browser_verify_profile", "Verify a complete profile DOM container and optional direct-child start boundary; structure only.",
         {"profile_content_selector": STRING, "profile_content_start_selector": STRING}, ("profile_content_selector",)),
    tool("browser_click", "Click a unique, verified CSS selector in the real browser.", {"selector": STRING}, ("selector",)),
    tool("browser_evaluate", "Run read-only JS only for questions probes cannot answer. Return compact structure. "
         "DOM edits invalidate verification evidence and require reloading the official URL.",
         {"function": STRING}, ("function",)),
    tool("browser_screenshot", "View the current viewport, or one element via selector, as a compressed image when roster "
         "shape cannot be judged from DOM. Images are costly; prefer structure/verify.", {"selector": STRING}),
]


def phase_tools(phase: str) -> list[dict]:
    from server.recon_contract import submission_schema
    submit = tool("submit_plan", "Submit this phase's structure-only plan. Schema and current-session browser "
                  "evidence are checked immediately. Fix reported paths/codes and retry; stop after accepted:true. "
                  "CSS selectors intentionally cannot contain Chinese. Never include teacher fields.",
                  {"plan": submission_schema(phase)}, ("plan",))
    definitions = submit["inputSchema"]["properties"]["plan"].pop("$defs", None)
    if definitions:
        submit["inputSchema"]["$defs"] = definitions
    if phase == "discovery":
        allowed = {"browser_navigate", "browser_navigation", "browser_survey", "browser_evaluate", "browser_screenshot"}
    elif phase == "branch":
        allowed = {"browser_navigate", "browser_structure", "browser_verify", "browser_verify_segments",
                   "browser_verify_profile", "browser_click", "browser_evaluate", "browser_screenshot"}
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
        """Keep only text lines not shown earlier in this session, as a short preview."""
        if not isinstance(summary, dict) or not isinstance(summary.get("lines"), list):
            return summary
        fresh, used, repeated = [], 0, 0
        for line in summary.pop("lines"):
            if line in self._seen_lines:
                repeated += 1
                continue
            self._seen_lines.add(line)
            if used < max_chars:
                fresh.append(line[:max_chars - used])
                used += len(fresh[-1])
        summary["new_text"] = "\n".join(fresh)
        summary["new_text_truncated"] = used >= max_chars
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
            issues = validate_submission(plan, self.phase, self.target, self.events)
            if issues:
                return {**self.text_result({"accepted": False, "issues": issues}), "isError": True}
            self.save("accepted.json", json.dumps(plan, ensure_ascii=False))
            return self.text_result({"accepted": True, "digest": plan_digest(plan)})
        reply = await self._browser_call(name, arguments)
        cid = str(len(self.events))
        self.events.extend([
            {"type": "tool/call", "data": {"callId": cid, "name": "mcp__playwright__" + name,
                                            "arguments": json.dumps(arguments)}},
            {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": cid, **reply}]}}},
        ])
        return reply

    async def _browser_call(self, name: str, arguments: dict) -> dict:
        if self._dom_modified and name in {
            "browser_verify", "browser_verify_segments", "browser_verify_profile", "browser_click"
        }:
            return {**self.text_result({"status": "browser_dom_modified",
                                       "retry": "Reload the official URL before collecting new verification evidence."}),
                    "isError": True}
        if name == "browser_navigate":
            url = arguments["url"]
            if not url.startswith(("http://", "https://")):
                raise ValueError("http(s) URL required")
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            self._dom_modified = False
            await self.snapshot()
            if self.phase == "discovery":
                value = await self.page.evaluate(
                    "() => ({url: location.href, page: __facultyRecon.summary(), navigation: __facultyRecon.navigation('body',0,64)})")
                value["navigation"] = self._omit_seen_links(value["navigation"])
                value["page"] = self._page_summary(value["page"])
            elif self.phase == "branch":
                value = await self.page.evaluate("() => ({url:location.href,page:__facultyRecon.summary(),structure:__facultyRecon.structure('body',0,8),pagination:__facultyRecon.pagination()})")
                value["page"] = self._page_summary(value["page"])
            else:
                value = await self.page.evaluate("() => ({url:location.href,navigation:__facultyRecon.navigation('body',0,24),structure:__facultyRecon.structure('body',0,8)})")
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
                    pages.append({"url": url, "final_url": self.page.url,
                                  "status": response.status if response is not None else None,
                                  "page": self._page_summary(probe["page"], SURVEY_PREVIEW_CHARS),
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
        elif name == "browser_verify":
            value = await self.page.evaluate("args => __facultyRecon.check(...args)",
                                            [arguments["card_selector"], arguments.get("next_selector")])
            if value.get("hiddenCardCount", 0) > 0:
                return {**self.text_result({**value, "status": "roster_cards_not_visible",
                    "retry": "Verify a selector for the visible roster rather than hidden or preload nodes."}),
                    "isError": True}
            cards = await self.page.locator(arguments["card_selector"]).evaluate_all("nodes => nodes.map(n=>n.outerHTML)")
            value["cardDigest"] = hashlib.sha256(json.dumps(cards).encode("utf-8")).hexdigest()
            value["pagination"] = await self.page.evaluate(
                "selector => __facultyRecon.pagination('body',0,12,false,selector)", arguments["card_selector"])
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
            await self.snapshot()
        elif name == "browser_verify_profile":
            from server.profile_structure import verify_profile_container
            captured = {}
            value = await verify_profile_container(self.page, arguments["profile_content_selector"],
                                                    arguments.get("profile_content_start_selector"),
                                                    local_capture=captured)
            if "html" in captured:
                self.save("html", captured["html"])
            else:
                await self.snapshot()
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
            phase = phase or os.environ.get("FACULTY_RECON_PHASE", "full")
            tools = phase_tools(phase)
            session = session or os.environ.get("FACULTY_RECON_SESSION", "standalone")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", session):
                session = hashlib.sha256(session.encode()).hexdigest()
            scout = BrowserScout(await context.new_page(), HERE / "outputs/recon-evidence" / session / uuid.uuid4().hex,
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
