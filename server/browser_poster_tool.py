"""Capture a poster page in a real browser and run local OCR for agent review."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


async def capture_poster(url: str, browser_context: Any, output_dir: str | Path,
                         *, ocr_engine: Any = None) -> dict:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("poster URL must be an absolute HTTP(S) URL")
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    page = await browser_context.new_page()
    warnings: list[str] = []
    image_state = {"image_count": 0, "unloaded_image_count": 0}
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if response is not None and response.status >= 400:
            raise RuntimeError(f"poster page returned HTTP {response.status}")
        try:
            await page.wait_for_load_state("load", timeout=15000)
        except Exception:
            warnings.append("page_load_timeout")
        dimensions = await page.evaluate(
            "() => ({height: document.documentElement.scrollHeight, viewport: window.innerHeight})")
        height = max(0, int(dimensions.get("height", 0)))
        viewport = max(1, int(dimensions.get("viewport", 1)))
        steps = min(12, (height + viewport - 1) // viewport)
        for step in range(steps):
            await page.evaluate("y => window.scrollTo(0, y)", step * viewport)
            await page.wait_for_timeout(150)
        await page.evaluate("() => window.scrollTo(0, document.documentElement.scrollHeight)")
        await page.wait_for_timeout(300)
        if height > 12 * viewport:
            warnings.append("scroll_limit_reached")
        await page.evaluate("""async () => {
            await Promise.all(Array.from(document.images).map(img => img.complete
                ? Promise.resolve() : new Promise(resolve => {
                    img.addEventListener('load', resolve, {once: true});
                    img.addEventListener('error', resolve, {once: true});
                    setTimeout(resolve, 8000);
                })));
        }""")
        image_state = await page.evaluate("""() => ({
            image_count: document.images.length,
            unloaded_image_count: Array.from(document.images)
                .filter(img => !img.complete || img.naturalWidth === 0).length
        })""")
        if image_state["unloaded_image_count"]:
            warnings.append("images_unloaded")
        image_bytes = await page.screenshot(full_page=True)
    finally:
        await page.close()
    if ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR

        ocr_engine = RapidOCR()
    raw_lines, _ = ocr_engine(image_bytes)
    lines = []
    for item in raw_lines or []:
        if len(item) >= 3:
            lines.append({"text": str(item[1]), "confidence": float(item[2])})
    stem = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    screenshot_path = out / f"poster_{stem}.png"
    ocr_path = out / f"poster_{stem}_ocr.json"
    screenshot_path.write_bytes(image_bytes)
    ocr_path.write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"status": "agent_review_required", "url": url, "line_count": len(lines),
            "image_count": image_state["image_count"],
            "unloaded_image_count": image_state["unloaded_image_count"], "warnings": warnings,
            "screenshot_path": str(screenshot_path), "ocr_path": str(ocr_path)}


async def _run_cli(args: argparse.Namespace) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not args.headed)
        try:
            from server.browser_context import new_browser_context
            context = await new_browser_context(browser)
            try:
                return await capture_poster(args.url, context, args.output_dir)
            finally:
                await context.close()
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Browser screenshot and local OCR for a poster roster")
    parser.add_argument("--url", required=True)
    parser.add_argument("--output-dir", default="server/outputs")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run_cli(args)), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
