from pathlib import Path

import pytest
import yaml
from playwright.async_api import async_playwright

from server import agent_recon
from server.recon_browser_mcp import BrowserScout


@pytest.mark.asyncio
async def test_real_browser_probe_paginates_navigation_and_counts_all_cards():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.goto("about:blank")
            links = "".join(f'<a href="https://example.edu/branch/{i}">分支{i}</a>' for i in range(29))
            cards = "".join(f'<li class="card"><a href="https://example.edu/person/{i}">教师{i}</a></li>'
                            for i in range(37))
            await page.set_content(f'<nav>{links}</nav><main><ul>{cards}</ul></main>')
            await page.add_script_tag(path=str(Path(agent_recon.__file__).with_name("recon_dom_probe.js")))
            urls = []
            offset = 0
            while offset is not None:
                result = await page.evaluate("offset => __facultyRecon.navigation('nav',offset,8)", offset)
                assert result["total"] == 29 and len(result["items"]) <= 8
                urls.extend(item["url"] for item in result["items"])
                offset = result["nextOffset"]
            assert len(set(urls)) == 29
            check = await page.evaluate("() => __facultyRecon.check('main ul > li',null)")
            assert check["cardCount"] == 37 and len(check["linkedSamples"]) == 2
            assert check["nextCount"] == 0 and "教师" not in str(check)
            structure = await page.evaluate("() => __facultyRecon.structure('main')")
            assert structure["items"][0]["repeatedCount"] == 37
            selector = structure["items"][0]["container"] + " > li"
            assert await page.locator(selector).count() == 37
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_discovery_navigate_returns_medium_navigation_in_one_bounded_result(tmp_path):
    from server.browser_context import new_browser_context

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await new_browser_context(browser)
            page = await context.new_page()
            links = "".join(f'<a href="https://example.edu/branch/{i}">分支{i}</a>'
                            for i in range(57))
            await page.route('**/*', lambda route: route.fulfill(body=f'<nav>{links}</nav>',
                                                            content_type='text/html'))
            scout = BrowserScout(page, tmp_path, phase="discovery")
            result = await scout.call("browser_navigate", {"url": "https://example.edu/"})
            raw = result["content"][0]["text"]
            import json
            value = json.loads(raw)
            assert value["total"] == 57
            assert len(value["items"]) == 57
            assert value["nextOffset"] is None
            assert len(raw.encode("utf-8")) <= 8000
        finally:
            await browser.close()


def test_runtime_resolves_evidence_path_and_complete_browser_config(tmp_path):
    patch = tmp_path / "runtime.yml"
    agent_recon.write_runtime_patch(patch)
    layers = yaml.safe_load(patch.read_text(encoding="utf-8"))
    entries = {entry["id"]: entry for layer in layers for entry in layer.get("insert", [layer])}
    assert Path(entries["spill-local"]["config"]["root"]).is_absolute()
    config = entries["faculty-recon-browser"]["config"]
    assert config["serverName"] == "playwright" and config["transport"] == "stdio"
    assert Path(config["command"]).is_file() and Path(config["cwd"]).is_dir()
    assert config["args"] == ["-m", "server.recon_browser_mcp", "--phase", "full", "--session", "standalone"]
