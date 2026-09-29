"""Page conditions no selector can change must not block a profile that reads the same for every visitor.

Real CMS pages seen in production: a Word-pasted <style style="display:none"> inside the
body, an editor's hidden 'invisible' placeholder, a theme stylesheet or script that the
server answers 404. Transient failures and hidden text that could be content still block.
"""
import json

import pytest
from playwright.async_api import async_playwright

from server.profile_content import scope_profile_html
from server.recon_browser_mcp import BrowserScout

URL = "https://scope.example.edu/p/1"
BODY = "<p>张三，教授，研究方向：机器人。邮箱 zhangsan@scope.example.edu</p>"


async def verify(tmp_path, html, assets=None, selector="article"):
    from server.browser_context import new_browser_context
    assets = assets or {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await (await new_browser_context(browser)).new_page()
            scout = BrowserScout(page, tmp_path)

            async def route(request):
                url = request.request.url
                if url == URL:
                    await request.fulfill(body=html, content_type="text/html; charset=utf-8")
                elif url in assets:
                    status, body, kind = assets[url]
                    if status == "abort":
                        await request.abort()
                    else:
                        await request.fulfill(status=status, body=body, content_type=kind)
                else:
                    await request.fulfill(status=404, body="")
            await page.route("**/*", route)
            await page.goto(URL)
            reply = await scout.call("browser_verify_profile", {"profile_content_selector": selector})
            return json.loads(reply["content"][0]["text"])
        finally:
            await browser.close()


def test_non_rendering_and_placeholder_elements_are_not_hidden_content():
    html = (f'<article><style style="display:none">p{{font-family:SimSun}}</style>{BODY}'
            '<div style="display: none">invisible</div></article>')
    scoped, evidence, issue = scope_profile_html(html, "article", URL)
    assert issue is None and "invisible" not in scoped and "zhangsan@" in scoped
    assert evidence["hidden_placeholders_removed"] == 1
    for hidden in ("李四 副教授", "lisi@x.edu", "2019年", "Prof. Wang Wei Li"):
        _, _, issue = scope_profile_html(f'<article>{BODY}<div style="display:none">{hidden}</div></article>',
                                         "article", URL)
        assert issue["status"] == "profile_content_selector_hidden", hidden


@pytest.mark.asyncio
@pytest.mark.browser
async def test_live_verification_accepts_style_and_placeholder_but_not_hidden_text(tmp_path):
    ok = await verify(tmp_path, f'<article><style style="display:none">p{{}}</style>{BODY}'
                                '<div style="display:none">invisible</div></article>')
    assert ok["confirmed"] is True and "reason" not in ok
    hidden = await verify(tmp_path, f'<article>{BODY}<div style="display:none">李四 副教授</div></article>')
    assert hidden["confirmed"] is False and hidden["reason"] == "profile_content_selector_hidden"


@pytest.mark.asyncio
@pytest.mark.browser
async def test_missing_theme_assets_do_not_block_a_server_rendered_profile(tmp_path):
    html = ('<link rel="stylesheet" href="/theme.css"><script src="/_sitegray.js"></script>'
            f'<div style="background:url(/bg.png)">x</div><article>{BODY}</article>')
    result = await verify(tmp_path, html)
    assert result["confirmed"] is True and result["network_idle"] is True
    assert result["server_rendered_content"] is True


@pytest.mark.asyncio
@pytest.mark.browser
async def test_transient_failures_and_script_built_content_still_block(tmp_path):
    aborted = await verify(tmp_path, f'<link rel="stylesheet" href="/theme.css"><article>{BODY}</article>',
                           {"https://scope.example.edu/theme.css": ("abort", "", "text/css")})
    assert aborted["confirmed"] is False and aborted["reason"] == "network_not_idle"
    assert aborted["blocking_requests"][0]["type"] == "stylesheet"
    server_error = await verify(tmp_path, f'<script src="/app.js"></script><article>{BODY}</article>',
                                {"https://scope.example.edu/app.js": (503, "", "text/javascript")})
    assert server_error["confirmed"] is False
    # the page rewrites the profile in the browser, so a missing script may have mattered
    rewritten = await verify(tmp_path, '<script src="/gone.js"></script><article><p>加载中</p></article>'
                                       f'<script>document.querySelector("article").innerHTML={json.dumps(BODY)}</script>')
    assert rewritten["confirmed"] is False and rewritten["server_rendered_content"] is False
    missing_portrait = await verify(tmp_path, f'<article><img src="/photo.jpg">{BODY}</article>')
    assert missing_portrait["confirmed"] is False
    assert missing_portrait["blocking_requests"][0]["type"] == "image"
