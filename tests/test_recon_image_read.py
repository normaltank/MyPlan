"""Image observations must use loaded browser resources, without approving a roster."""
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from playwright.async_api import async_playwright

from server.browser_context import new_browser_context
from server.recon_browser_mcp import BrowserScout, phase_tools


@pytest.fixture
def image_site():
    buffer = BytesIO()
    Image.new("RGB", (80, 100), "white").save(buffer, format="PNG")
    original = buffer.getvalue()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/original.png":
                body, content_type = original, "image/png"
            elif self.path == "/missing.png":
                self.send_error(404)
                return
            else:
                body = (b'<main><p>Ordinary DOM text</p><img id="poster" src="/missing.png" '
                        b'srcset="/original.png 1x"><img id="hidden" style="display:none" '
                        b'src="/original.png"><img id="broken" src="/missing.png"></main>')
                content_type = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", original
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize("phase", ["discovery", "branch"])
async def test_original_image_ocr_ranges_evidence_and_rejected_selectors(image_site, tmp_path, monkeypatch, phase):
    from server import image_biography
    url, original = image_site
    calls = []
    lines = [{"text": f"Observed line {index}", "confidence": 0.98, "box": [[0, index], [10, index]]}
             for index in range(37)]

    def scan(data, kind):
        calls.append((data, kind))
        return {"text": "\n".join(line["text"] for line in lines), "pages": [{"lines": lines}]}

    monkeypatch.setattr(image_biography, "scan_media", scan)
    assert "browser_read_image" in {tool["name"] for tool in phase_tools(phase)}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await new_browser_context(browser)
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path, phase)
            await scout.call("browser_navigate", {"url": url})
            assert calls == []  # Ordinary HTML/navigation never triggers OCR.
            first = json.loads((await scout.call("browser_read_image", {"selector": "#poster", "limit": 24}))
                               ["content"][0]["text"])
            assert first["image"]["url"] == url + "original.png"  # currentSrc, not broken src.
            assert first["sha256"] == hashlib.sha256(original).hexdigest()
            assert first["source"] == "local_ocr" and first["nextOffset"] == 24
            assert Path(first["original_path"]).read_bytes() == original
            assert json.loads(Path(first["ocr_path"]).read_text(encoding="utf-8"))["pages"][0]["lines"] == lines
            last = json.loads((await scout.call("browser_read_image", {"selector": "#poster", "offset": 24}))
                              ["content"][0]["text"])
            assert first["items"] + last["items"] == lines and last["nextOffset"] is None
            past = json.loads((await scout.call("browser_read_image", {"selector": "#poster", "offset": 100}))
                              ["content"][0]["text"])
            assert past["items"] == [] and past["offset"] == past["end"] == past["total"]
            assert calls == [(original, "image")]  # Range reads reuse only identical original bytes.
            for selector in ("img", "#hidden", "p", "#absent"):
                with pytest.raises(ValueError):
                    await scout.call("browser_read_image", {"selector": selector})
            # A failed image is not OCR'd, even though it has a DOM node.
            with pytest.raises(Exception, match="Timeout"):
                await scout.call("browser_read_image", {"selector": "#broken"})
            assert len(calls) == 1
            # Reading an image supplies neither a card verification nor source approval.
            from server.agent_recon import selector_evidence_issues
            issues = selector_evidence_issues({"branches": [{"url": url, "card_selector": "#poster",
                                                           "single_page": True}]}, scout.events)
            assert issues
            await scout.call("browser_evaluate", {"function": "() => document.querySelector('#poster').id='edited'"})
            denied = await scout.call("browser_read_image", {"selector": "#edited"})
            assert denied["isError"] and len(calls) == 1
        finally:
            await browser.close()
