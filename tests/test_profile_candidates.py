"""Profile-container proof in one call: candidates, failure details, multi-sample proof."""
import json
from pagination_contract_fixture import termination
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from server.agent_recon import selector_evidence_issues
from server.browser_context import new_browser_context
from server.profile_content import hidden_blockers
from server.recon_browser_mcp import BrowserScout

# These components test profile scope, with an independently verified roster source.
pytestmark = pytest.mark.verified_source_boundary

# Two pages of one site: the CMS numbers the body id differently per page, the
# class is stable, and a hidden previous/next block sits inside the outer box.
PAGE = """<html><body><header><nav><a href="/">首页</a><a href="/list">师资队伍</a></nav></header>
<div class="container"><div class="article-box"><div class="article-title">{name}</div>
<div class="article-txt"><p><img src="/photo.png"></p>
<div id="{body_id}"><div class="v_news_content"><p>{name}，教授，研究方向为结构工程。</p>
<p>联系邮箱：teacher{n}@example.edu</p><p>主持国家自然科学基金项目两项，发表论文三十余篇。</p></div></div>
<div style="display: none">上一条：<a href="/p/9">某某</a> 下一条：<a href="/p/8">某某</a></div></div></div></div>
<footer>地址：某市某路 1 号 电话：0000-0000000</footer></body></html>"""


def _serve():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/old/1":
                self.send_response(302)
                self.send_header("Location", "/p/1")
                self.end_headers()
                return
            if self.path == "/photo.png":
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.end_headers()
                self.wfile.write(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                                               "1f15c4890000000a49444154789c6360000000020001e221bc330000"
                                               "000049454e44ae426082"))
                return
            n = self.path.rsplit("/", 1)[-1]
            body = PAGE.format(name=f"张老师{n}", n=n, body_id="vsb_content" if n == "1" else "vsb_content_4")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    return http


def _events(calls):
    events = []
    for index, (name, arguments, reply) in enumerate(calls):
        events.extend([
            {"type": "tool/call", "data": {"callId": str(index), "name": "mcp__playwright__" + name,
                                            "arguments": json.dumps(arguments)}},
            {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": str(index), **reply}]}}}])
    return events


def test_hidden_blockers_name_the_hidden_descendant_without_its_text():
    html = PAGE.format(name="张老师", n=1, body_id="vsb_content")
    blockers = hidden_blockers(html, "div.article-box")
    assert blockers == [{"where": "descendant", "path": "div.article-txt:nth-of-type(2) > div:nth-of-type(2)",
                         "text_chars": len("上一条：某某 下一条：某某".replace(" ", "")), "media": 0}]
    assert hidden_blockers(html, "div.v_news_content") == []


@pytest.mark.asyncio
@pytest.mark.browser
async def test_one_call_proposes_common_candidates_and_proves_both_samples(tmp_path):
    http = _serve()
    base = f"http://127.0.0.1:{http.server_port}"
    samples = [f"{base}/p/1", f"{base}/p/2"]
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await new_browser_context(browser)
            scout = BrowserScout(await context.new_page(), tmp_path, "branch")
            calls = []

            async def call(name, arguments):
                reply = await scout.call(name, arguments)
                calls.append((name, arguments, reply))
                text = reply["content"][0]["text"]
                assert "张老师" not in text and "@" not in text and "研究方向" not in text
                return json.loads(text)

            proposal = await call("browser_verify_profile", {"urls": samples})
            assert [item["requested_url"] for item in proposal["results"]] == samples
            # The stable class names the body on both pages; the numbered ids do not.
            assert proposal["common_candidates"][0] == "div.v_news_content"
            assert "#vsb_content" not in proposal["common_candidates"]
            first = proposal["results"][0]["candidates"]
            assert first[0]["static_check"] == "ok" and "#vsb_content" in first[0]["selectors"]
            assert {"div.article-box" in item["selectors"] and item["static_check"] for item in first} >= {
                "profile_content_selector_hidden"}

            rejected = await call("browser_verify_profile",
                                  {"profile_content_selector": "div.article-box", "urls": samples[:1]})
            failure = rejected["results"][0]
            assert failure["confirmed"] is False and failure["reason"] == "profile_content_selector_hidden"
            assert failure["live_checks"] == "not_run_after_static_issue" and "container_count" not in failure
            assert failure["hidden_blockers"][0]["where"] == "descendant"
            assert failure["alternatives"][0]["selectors"][0] == "div.v_news_content"

            proof = await call("browser_verify_profile",
                               {"profile_content_selector": "div.v_news_content",
                                "urls": [f"{base}/old/1", samples[1]]})
            assert proof["confirmed_count"] == 2
            assert proof["results"][0]["url"] == samples[0]
            await browser.close()
    finally:
        http.shutdown()
        http.server_close()

    branch = {"url": f"{base}/list", "card_selector": "ul > li", "single_page": True,
              "profile_content_selector": "div.v_news_content"}
    roster = ("browser_verify", {"card_selector": "ul > li"},
              {"content": [{"type": "text", "text": json.dumps({
                  "url": branch["url"], "cardCount": 2, "linkedSamples": [f"{base}/old/1", samples[1]],
                  "nextCount": 0, "pagination": {"total": 0}, "pagination_termination": termination(branch["url"], "ul > li")})}], "isError": False})
    plan = {"school": "某大学", "college": "某学院", "branches": [branch]}
    # The multi-sample proof counts per page, and the redirect it followed maps
    # the roster's link to the page it proved.
    assert selector_evidence_issues(plan, _events([roster, *calls[2:]])) == []
    assert selector_evidence_issues(plan, _events([roster, *calls[:2]]))[0]["status"] == "profile_content_not_verified"


def test_preview_only_marks_lines_it_returned(tmp_path):
    scout = BrowserScout(None, tmp_path)
    lines = [f"line {i} " + "x" * 40 for i in range(12)]
    first = scout._page_summary({"lines": list(lines)}, max_chars=120)
    assert first["new_text_truncated"] is True and first["unshown_lines"] >= 9
    # Opening the page again continues after what was actually shown.
    second = scout._page_summary({"lines": list(lines)}, max_chars=10_000)
    shown_first = [line for line in first["new_text"].split("\n") if line in lines]
    assert second["repeated_lines_omitted"] == len(shown_first)
    assert second["new_text"].split("\n") == [line for line in lines if line not in shown_first]
    assert second["new_text_truncated"] is False and second["unshown_lines"] == 0
