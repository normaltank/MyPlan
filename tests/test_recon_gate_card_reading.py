"""The recon gate checks a roster's cards with the harvest's own reading (badr 10-09).

集美大学海洋文化与法律学院: the scout added a supervisors' summary table (59 rows,
the name column headed 导师). The gate accepted it; the harvest read 3 people,
stopped on the card mismatch, and the repair returned the same plan.
"""
from __future__ import annotations
from pagination_contract_fixture import completed_result

import json

import pytest

from server.agent_recon import selector_evidence_issues
from server.harvest_workflow import roster_card_reading

# Card-reading accuracy is tested against an independently verified formal source.
pytestmark = pytest.mark.verified_source_boundary

URL = "https://chin.x.edu.cn/info/1831/62623.htm"
# The real rows: most link the same department page, one links a profile.
INTRO = ["http://chin.x.edu.cn/szdw/js.htm"] * 5 + ["https://chin.x.edu.cn/info/2141/58723.htm"]
SUPERVISORS = ("<div id='c'><table><tbody><tr><td>序号</td><td>专业/领域名称</td><td>导师</td><td>性别</td>"
               "<td>职称</td><td>师资介绍</td></tr>" + "".join(
                   f"<tr><td>{i}</td><td>法律硕士</td><td>{name}</td><td>男</td><td>教授</td>"
                   f"<td><a href='{link}'>{link}</a></td></tr>"
                   for i, (name, link) in enumerate(zip(["张克锋", "李时学", "陈曼君", "管雪莲", "罗伟文", "吴家煦"],
                                                        INTRO), 1))
               + "</tbody></table></div>")
ROSTER = "<ul class='t'>" + "".join(
    f"<li><a href='/info/{i}.htm'>{name}</a></li>"
    for i, name in enumerate(["张克锋", "李时学", "陈曼君"])) + "</ul>"
SELECTOR = "#c > table > tbody > tr:not(:first-child)"


def test_the_reading_reports_cards_the_harvest_cannot_read():
    assert roster_card_reading(SUPERVISORS, URL, SELECTOR) == {"visible_cards": 6, "extracted_candidates": 5}
    assert roster_card_reading(ROSTER, "https://chin.x.edu.cn/szdw/js.htm", "ul.t > li") is None


def _verify(result, selector=SELECTOR):
    result = completed_result(result, {"card_selector": selector})
    return [{"type": "tool/call", "data": {"callId": "v", "name": "mcp__playwright__browser_verify",
                                             "arguments": json.dumps({"card_selector": selector})}},
            {"type": "tool/result", "data": {"message": {"content": [
                {"toolCallId": "v", "content": [{"type": "text", "text": json.dumps(result)}]}]}}}]


def test_the_gate_sends_the_branch_back_with_the_counts():
    plan = {"school": "集美大学", "college": "海洋文化与法律学院",
            "branches": [{"url": URL, "single_page": True, "card_selector": SELECTOR}]}
    seen = {"url": URL, "cardCount": 6, "pagination": {"total": 0}, "linkedSamples": []}
    [issue] = selector_evidence_issues(plan, _verify({**seen, "card_mismatch": {
        "visible_cards": 6, "extracted_candidates": 5}}))
    assert issue["status"] == "roster_card_mismatch" and issue["failed_condition"] == "cards_not_read"
    assert issue["closest"] == {"visible_cards": 6, "extracted_candidates": 5} and "page_only" not in issue
    assert selector_evidence_issues(plan, _verify(seen)) == []


@pytest.mark.asyncio
@pytest.mark.browser
async def test_browser_verify_carries_the_reading(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.recon_browser_mcp import BrowserScout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser)
            await context.route("**/*", lambda route: route.fulfill(
                body=f"<html><body>{SUPERVISORS}</body></html>", content_type="text/html; charset=utf-8"))
            scout = BrowserScout(await context.new_page(), tmp_path, "branch",
                                 {"school": "集美大学", "college": "海洋文化与法律学院", "url": URL})
            await scout.call("browser_navigate", {"url": URL})
            reply = json.loads((await scout.call("browser_verify", {"card_selector": SELECTOR}))["content"][0]["text"])
            assert reply["cardCount"] == 6
            assert reply["card_mismatch"] == {"visible_cards": 6, "extracted_candidates": 5}
        finally:
            await browser.close()
