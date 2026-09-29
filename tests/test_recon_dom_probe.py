from pathlib import Path

import pytest
import yaml
from playwright.async_api import async_playwright

from server import agent_recon
from server.recon_browser_mcp import BrowserScout


@pytest.mark.asyncio
@pytest.mark.browser
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
@pytest.mark.browser
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
            value = json.loads(raw)["navigation"]
            assert value["total"] == 57
            assert len(value["items"]) == 57
            assert value["nextOffset"] is None
            assert len(raw.encode("utf-8")) <= 8000
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_discovery_navigate_sends_repeated_menus_and_text_once_per_session(tmp_path):
    import json
    from server.browser_context import new_browser_context

    menu = "".join(f'<a href="https://example.edu/m/{i}">栏目{i}</a>' for i in range(20))
    pages = {"https://example.edu/": f'<nav>{menu}</nav><main><h1>学院首页</h1><p>学院简介文字</p></main>',
             "https://example.edu/szdw.htm": (f'<nav>{menu}</nav><main><h1>师资队伍</h1>'
                                               '<a href="https://example.edu/t/1.htm">教授名录</a>'
                                               '<p>本页列出全部在职教师</p></main>')}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await new_browser_context(browser)).new_page()
            await page.route('**/*', lambda route: route.fulfill(
                body=pages.get(route.request.url, '<p>none</p>'), content_type='text/html; charset=utf-8'))
            scout = BrowserScout(page, tmp_path, phase="discovery")
            first = json.loads((await scout.call("browser_navigate", {"url": "https://example.edu/"}))["content"][0]["text"])
            second = json.loads((await scout.call("browser_navigate", {"url": "https://example.edu/szdw.htm"}))["content"][0]["text"])
            assert len(first["navigation"]["items"]) == 20 and first["page"]["title"] == ""
            assert "学院简介文字" in first["page"]["new_text"]
            # the second page only carries what is new on it
            assert [item["label"] for item in second["navigation"]["items"]] == ["教授名录"]
            assert second["navigation"]["seen_omitted"] == 20 and second["navigation"]["total"] == 21
            assert "本页列出全部在职教师" in second["page"]["new_text"] and "栏目3" not in second["page"]["new_text"]
            assert second["page"]["repeated_lines_omitted"] >= 1
            found = json.loads((await scout.call("browser_navigation", {"query": "名录|szdw"}))["content"][0]["text"])
            assert [item["label"] for item in found["items"]] == ["教授名录"] and found["query"] == ["名录", "szdw"]
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


@pytest.mark.asyncio
@pytest.mark.browser
async def test_survey_reads_several_candidate_pages_in_one_call(tmp_path):
    import json
    from server.browser_context import new_browser_context
    from server import agent_recon

    menu = '<a href="https://example.edu/">首页</a><a href="https://example.edu/szdw.htm">师资队伍</a>'
    pages = {"https://example.edu/szdw.htm": f'<nav>{menu}</nav><h2>师资队伍</h2><a href="https://example.edu/js/1.htm">教授</a><p>名录说明</p>',
             "https://example.edu/news.htm": f'<nav>{menu}</nav><h2>学院新闻</h2><p>新闻正文</p>'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await new_browser_context(browser)).new_page()

            await page.route('**/*', lambda route: route.fulfill(
                body=pages.get(route.request.url, '<p>none</p>'), content_type='text/html; charset=utf-8'))
            scout = BrowserScout(page, tmp_path, phase="discovery")
            raw = (await scout.call("browser_survey", {"urls": ["https://example.edu/szdw.htm",
                                                                 "https://example.edu/news.htm", "ftp://x"]}))["content"][0]["text"]
            items = json.loads(raw)["pages"]["items"]
            assert items[0]["page"]["headings"] == ["师资队伍"] and "名录说明" in items[0]["page"]["new_text"]
            assert [link["label"] for link in items[0]["new_links"]] == ["首页", "师资队伍", "教授"]
            assert items[1]["final_url"] == "https://example.edu/news.htm" and items[1]["new_links"] == []
            assert "新闻正文" in items[1]["page"]["new_text"] and "名录说明" not in items[1]["page"]["new_text"]
            assert items[2]["error"] == "http(s) URL required"
        finally:
            await browser.close()
    events = [{"type": "tool/call", "data": {"callId": "s1", "name": "mcp__playwright__browser_survey",
               "arguments": json.dumps({"urls": ["https://example.edu/szdw.htm", "https://example.edu/old.htm"]})}},
              {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": "s1",
               "content": [{"type": "text", "text": raw}]}]}}}]
    assert "https://example.edu/js/1.htm" in agent_recon.observed_browser_urls(events)
    redirected = json.loads(raw)
    redirected["pages"]["items"][1]["url"] = "https://example.edu/old.htm"
    events[1]["data"]["message"]["content"][0]["content"][0]["text"] = json.dumps(redirected)
    assert agent_recon.browser_url_aliases(events) == {"https://example.edu/old.htm": "https://example.edu/news.htm"}
