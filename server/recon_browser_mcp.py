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
SCREENSHOT_MAX_WIDTH = 1280
SCREENSHOT_MAX_HEIGHT = 2000
RANGE = {"selector": STRING, "offset": {"type": "integer", "minimum": 0},
         "limit": {"type": "integer", "minimum": 1, "maximum": 24}}
NAV_RANGE = {"selector": STRING, "offset": {"type": "integer", "minimum": 0},
             "limit": {"type": "integer", "minimum": 1, "maximum": 64}}
TOOLS = [
    tool("browser_navigate", "Open official URL in the real browser; return first navigation range.", {"url": STRING}, ("url",)),
    tool("browser_navigation", "Read navigation URL/label ranges. Follow nextOffset until relevant containers are covered.", NAV_RANGE),
    tool("browser_structure", "Read repeated DOM subtree containers and counts; inspect their returned CSS paths.", RANGE),
    tool("browser_verify", "Verify actual card and next-control selectors; returns counts, shapes and sample URLs.",
         {"card_selector": STRING, "next_selector": STRING,
          "empty_state_selector": STRING, "roster_container_selector": STRING}, ("card_selector",)),
    tool("browser_verify_profile", "Verify a complete profile DOM container and optional direct-child start boundary; structure only.",
         {"profile_content_selector": STRING, "profile_content_start_selector": STRING}, ("profile_content_selector",)),
    tool("browser_click", "Click a unique, verified CSS selector in the real browser.", {"selector": STRING}, ("selector",)),
    tool("browser_evaluate", "Run a JS function in the current page only for questions probes cannot answer. Return compact structure.",
         {"function": STRING}, ("function",)),
    tool("browser_screenshot", "View the current viewport, or one element via selector, as a compressed image when roster "
         "shape cannot be judged from DOM. Images are costly; prefer structure/verify.", {"selector": STRING}),
]


def phase_tools(phase: str) -> list[dict]:
    if phase == "discovery":
        allowed = {"browser_navigate", "browser_navigation", "browser_evaluate", "browser_screenshot"}
    elif phase == "branch":
        allowed = {"browser_navigate", "browser_structure", "browser_verify", "browser_verify_profile", "browser_click", "browser_evaluate", "browser_screenshot"}
    else:
        return TOOLS
    return [entry for entry in TOOLS if entry["name"] in allowed]


class BrowserScout:
    def __init__(self, page: Any, evidence_dir: Path, phase: str = "full"):
        self.page = page
        self.evidence_dir = evidence_dir
        evidence_dir.mkdir(parents=True, exist_ok=True)
        self.sequence = 0
        self.phase = phase
        self._last_html_digest = None
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
        if name == "browser_navigate":
            url = arguments["url"]
            if not url.startswith(("http://", "https://")):
                raise ValueError("http(s) URL required")
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await self.snapshot()
            if self.phase == "discovery":
                value = await self.page.evaluate("() => __facultyRecon.navigation('body',0,64)")
            elif self.phase == "branch":
                value = await self.page.evaluate("() => ({url:location.href,structure:__facultyRecon.structure('body',0,8),pagination:__facultyRecon.pagination()})")
            else:
                value = await self.page.evaluate("() => ({url:location.href,navigation:__facultyRecon.navigation('body',0,24),structure:__facultyRecon.structure('body',0,8)})")
        elif name in ("browser_navigation", "browser_structure"):
            method = "navigation" if name == "browser_navigation" else "structure"
            values = [arguments.get("selector", "body"), arguments.get("offset", 0),
                      arguments.get("limit", 64 if name == "browser_navigation" else 24)]
            value = await self.page.evaluate(f"args => __facultyRecon.{method}(...args)", values)
            if self.phase == "discovery":
                await self.snapshot()
        elif name == "browser_verify":
            value = await self.page.evaluate("args => __facultyRecon.check(...args)",
                                            [arguments["card_selector"], arguments.get("next_selector")])
            cards = await self.page.locator(arguments["card_selector"]).evaluate_all("nodes => nodes.map(n=>n.outerHTML)")
            value["cardDigest"] = hashlib.sha256(json.dumps(cards).encode("utf-8")).hexdigest()
            value["pagination"] = await self.page.evaluate("() => __facultyRecon.pagination()")
            if "empty_state_selector" in arguments or "roster_container_selector" in arguments:
                from server.empty_roster import verify_empty_roster
                branch = {**arguments, "url": self.page.url, "single_page": True}
                value["empty_roster"] = await verify_empty_roster(self.page, branch, await self.page.content())
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
            value = await self.page.evaluate(arguments["function"])
            await self.snapshot()
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


async def serve(*, phase: str | None = None, session: str | None = None) -> None:
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
            scout = BrowserScout(await context.new_page(), HERE / "outputs/recon-evidence" / session / uuid.uuid4().hex, phase)
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
    args = parser.parse_args()
    asyncio.run(serve(phase=args.phase, session=args.session))
