"""Page conditions no selector can change must not block a profile that reads the same for every visitor.

Real CMS pages seen in production: a Word-pasted <style style="display:none"> inside the
body, an editor's hidden 'invisible' placeholder, a theme stylesheet or script that the
server answers 404, or that fails the same way again when the page is opened anew.
Content requests, script-built bodies and hidden text that could be content still block.
Missing body images require media review even when the DOM scope is confirmed.
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
            opened = []

            async def route(request):
                url = request.request.url
                if url == URL:
                    await request.fulfill(body=html, content_type="text/html; charset=utf-8")
                elif url in assets:
                    status, body, kind = assets[url]
                    opened.append(url)
                    if status == "abort-once":  # fails on the first open only
                        status = "abort" if opened.count(url) == 1 else 200
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
async def test_failures_settled_by_reopening_the_page(tmp_path):
    # A stylesheet that fails once loads when the page is opened again.
    once = await verify(tmp_path, f'<link rel="stylesheet" href="/theme.css"><article>{BODY}</article>',
                        {"https://scope.example.edu/theme.css": ("abort-once", "", "text/css")})
    assert once["confirmed"] is True and once["failed_request_count"] == 0
    # One that fails on every open is how the page is published, like a 404
    # (a mixed-content block, a 404 page Chromium refuses as a stylesheet).
    always = await verify(tmp_path, f'<link rel="stylesheet" href="/theme.css?v=1"><article>{BODY}</article>',
                          {"https://scope.example.edu/theme.css?v=1": ("abort", "", "text/css")})
    assert always["confirmed"] is True and always["network_idle"] is True
    # A script failing on every open still needs the body the server sent.
    server_error = await verify(tmp_path, f'<script src="/app.js"></script><article>{BODY}</article>',
                                {"https://scope.example.edu/app.js": (503, "", "text/javascript")})
    assert server_error["confirmed"] is True and server_error["server_rendered_content"] is True


@pytest.mark.asyncio
@pytest.mark.browser
async def test_script_built_content_and_profile_media_still_block(tmp_path):
    built = await verify(tmp_path, '<script src="/app.js"></script><article><p>加载中</p></article>'
                                   f'<script>document.querySelector("article").innerHTML={json.dumps(BODY)}</script>',
                         {"https://scope.example.edu/app.js": ("abort", "", "text/javascript")})
    assert built["confirmed"] is False and built["server_rendered_content"] is False
    # the page rewrites the profile in the browser, so a missing script may have mattered
    rewritten = await verify(tmp_path, '<script src="/gone.js"></script><article><p>加载中</p></article>'
                                       f'<script>document.querySelector("article").innerHTML={json.dumps(BODY)}</script>')
    assert rewritten["confirmed"] is False and rewritten["server_rendered_content"] is False
    # Scope proof preserves the missing image evidence; the media audit blocks
    # delivery until a human establishes its role, regardless of its dimensions.
    from server.image_biography import BrowserAssetError, audit_profile_media

    html = f'<article><img src="/photo.jpg" width="120" height="160">{BODY}</article>'
    missing_portrait = await verify(tmp_path, html)
    assert missing_portrait["confirmed"] is True
    assert missing_portrait["unpublished_images"] == []
    [missing] = missing_portrait["missing_content_images"]
    media_url = "https://scope.example.edu/photo.jpg"
    assert missing["profile_url"] == URL and missing["media_url"] == media_url
    assert missing["http_status"] == 404 and missing["missing_on_two_opens"] is True
    scoped, _, issue = scope_profile_html(html, "article", URL)
    assert issue is None

    async def fetch_asset(url):
        assert url == media_url
        raise BrowserAssetError(404, url)

    audit = await audit_profile_media(
        {URL: scoped}, {URL: {"张三"}}, tmp_path / "media", fetch_asset=fetch_asset,
        scoped_urls={URL}, missing_media_evidence={URL: [missing]},
        profile_source_html={URL: html})
    assert [item["status"] for item in audit["unresolved"]] == ["missing_profile_media_needs_review"]
    assert audit["missing_media_evidence"][0]["id"] == missing["id"]
    assert audit["unpublished_media"] == []


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize('path', ['/index/flow', '/wm/api/visit/write/123'])
@pytest.mark.parametrize('method', ['fetch', 'xhr'])
async def test_gone_counter_endpoint_does_not_block_intact_profile(tmp_path, path, method):
    request = (f'fetch({json.dumps(path)})' if method == 'fetch' else
               f'const request=new XMLHttpRequest();request.open("GET", {json.dumps(path)});request.send()')
    html = f'<article>{BODY}</article><script>{request}</script>'
    result = await verify(tmp_path, html)
    assert result['confirmed'] is True, result
    assert result['server_rendered_content'] is True
    assert result['failed_request_count'] == 1
    assert result['blocking_failed_request_count'] == 0


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize('status', ['abort', 503])
async def test_transient_counter_failures_remain_blocking(tmp_path, status):
    html = f'<article>{BODY}</article><script>fetch("/index/flow")</script>'
    result = await verify(tmp_path, html, {"https://scope.example.edu/index/flow": (status, '', 'text/plain')})
    assert result['confirmed'] is False
    assert result['reason'] == 'network_not_idle'


@pytest.mark.asyncio
@pytest.mark.browser
async def test_gone_fetch_does_not_release_browser_built_body(tmp_path):
    html = ('<article><p>加载中</p></article>'
            f'<script>document.querySelector("article").innerHTML={json.dumps(BODY)};'
            'fetch("/biography")</script>')
    result = await verify(tmp_path, html)
    assert result['confirmed'] is False
    assert result['server_rendered_content'] is False


@pytest.mark.asyncio
@pytest.mark.browser
async def test_gone_fetch_does_not_release_browser_added_biography_media(tmp_path):
    html = (f'<article>{BODY}</article><script>fetch("/index/flow");'
            'document.querySelector("article").insertAdjacentHTML("beforeend", '
            '\'<img src="/cv.png">\');</script>')
    result = await verify(tmp_path, html, {'https://scope.example.edu/cv.png': (200, '', 'image/png')})
    assert result['confirmed'] is False
    assert result['server_rendered_content'] is False
