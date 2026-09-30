"""Conservative DOM grouping; every reused branch needs fresh browser proof."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from bs4 import BeautifulSoup


def structural_fingerprint(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    if soup.body is None or not soup.body.find("a", href=True):
        return None

    def shape(node):
        children = []
        for child in node.find_all(recursive=False):
            if child.name in {"script", "style", "noscript"}:
                continue
            value = shape(child)
            if not children or children[-1] != value:
                children.append(value)
        return (node.name, tuple(sorted(node.get("class", []))), node.get("id", ""),
                node.get("role", ""), tuple(children))

    return hashlib.sha256(repr(shape(soup.body)).encode()).hexdigest()


async def _inspect(urls: list[str], evidence_root: Path) -> dict[str, str]:
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.recon_browser_mcp import BrowserScout
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await new_browser_context(browser)
        try:
            async def inspect(index, url):
                page = await context.new_page()
                try:
                    response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    if response and response.status >= 400:
                        return url, None
                    await BrowserScout(page, evidence_root / str(index), "branch").snapshot()
                    return url, structural_fingerprint(await page.content())
                except Exception:
                    return url, None
                finally:
                    await page.close()
            return {url: digest for url, digest in await asyncio.gather(
                *(inspect(i, url) for i, url in enumerate(urls))) if digest}
        finally:
            await browser.close()


def inspect_templates(urls: list[str], evidence_root: Path, timeout: float) -> dict[str, str]:
    try:
        return asyncio.run(asyncio.wait_for(_inspect(urls, evidence_root), timeout=timeout))
    except Exception:
        return {}


def verify_template(branch: dict, target: dict, evidence_root: Path, timeout: float,
                    expected_template: str) -> dict:
    """The cache verifier proves card, next-page and profile-body selectors anew."""
    async def verify():
        from playwright.async_api import async_playwright
        from server.browser_context import new_browser_context
        from server.recon_plan_cache import validate_cached_plan
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await new_browser_context(browser)
            try:
                plan = {"school": target["school"], "college": target["college"],
                        "source_kind": "university_faculty_roster", "branches": [branch]}
                return await validate_cached_plan({"plan": plan}, context, evidence_root,
                                                  verify_discovery=False, expected_template=expected_template)
            finally:
                await browser.close()
    try:
        return asyncio.run(asyncio.wait_for(verify(), timeout=timeout))
    except Exception:
        return {"valid": False, "reason": "template_verification_failed", "model_calls": 0}
