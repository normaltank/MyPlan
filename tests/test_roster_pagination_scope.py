"""Real browser checks of roster completion beside independent slide controls."""
import json

import pytest
from playwright.async_api import async_playwright

from server.browser_context import IsolatedPage
from server.recon_browser_mcp import BrowserScout


CAROUSEL = """<div class="banner slick-slider slick-initialized">
  <div class="slick-list"><div class="slick-track">
    <div class="slick-slide"><img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"></div>
    <div class="slick-slide"><img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"></div>
  </div></div><button class="slick-next">Next</button>
</div>"""
ROSTER = "<main><ul class='roster'><li>Record A</li><li>Record B</li></ul></main>"


@pytest.mark.asyncio
@pytest.mark.browser
async def test_single_page_roster_can_submit_beside_real_slide_component(tmp_path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("**/*", lambda route: route.fulfill(body=CAROUSEL + ROSTER,
                                                               content_type="text/html"))
        page = IsolatedPage(await context.new_page(), context)
        url = "https://fixture.example.edu/roster"
        scout = BrowserScout(page, tmp_path, "branch",
                             {"school": "Fixture", "college": "Department", "url": url})
        try:
            await scout.call("browser_navigate", {"url": url})
            verified = await scout.call("browser_verify", {"card_selector": ".roster > li"})
            assert json.loads(verified["content"][0]["text"])["cardCount"] == 2
            submission = await scout.call("submit_plan", {"plan": {
                "url": url, "card_selector": ".roster > li", "single_page": True}})
            assert json.loads(submission["content"][0]["text"])["accepted"] is True
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize("body", [
    CAROUSEL + ROSTER + "<nav><a rel='next' href='/page2'>Continue</a></nav>",
    CAROUSEL + ROSTER + "<nav class='pagination'><button>2</button></nav>",
    "<div class='slick-slider slick-initialized'><button class='slick-next'>Next</button></div>" + ROSTER,
    CAROUSEL.replace("</div></div><button", "</div>" + ROSTER + "</div><button"),
])
async def test_actual_or_unexplained_roster_controls_still_block_single_page(tmp_path, body):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.route("**/*", lambda route: route.fulfill(body=body, content_type="text/html"))
        page = IsolatedPage(await context.new_page(), context)
        url = "https://fixture.example.edu/roster"
        scout = BrowserScout(page, tmp_path, "branch",
                             {"school": "Fixture", "college": "Department", "url": url})
        try:
            await scout.call("browser_navigate", {"url": url})
            await scout.call("browser_verify", {"card_selector": ".roster > li"})
            submission = await scout.call("submit_plan", {"plan": {
                "url": url, "card_selector": ".roster > li", "single_page": True}})
            value = json.loads(submission["content"][0]["text"])
            assert value["accepted"] is False
            assert value["issues"][0]["status"] == "single_page_pagination_unverified"
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_deleting_pager_in_evaluate_cannot_create_submission_evidence(tmp_path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        body = ROSTER + "<nav class='pagination'><a rel='next' href='/page2'>Continue</a></nav>"
        await context.route("**/*", lambda route: route.fulfill(body=body, content_type="text/html"))
        page = IsolatedPage(await context.new_page(), context)
        url = "https://fixture.example.edu/roster"
        scout = BrowserScout(page, tmp_path, "branch",
                             {"school": "Fixture", "college": "Department", "url": url})
        try:
            await scout.call("browser_navigate", {"url": url})
            await scout.call("browser_verify", {"card_selector": ".roster > li"})
            changed = await scout.call("browser_evaluate", {
                "function": "() => {document.querySelector('.pagination').remove();return {remaining:0};}"})
            assert changed["isError"] is True
            verified = await scout.call("browser_verify", {"card_selector": ".roster > li"})
            assert verified["isError"] is True
            submission = await scout.call("submit_plan", {"plan": {
                "url": url, "card_selector": ".roster > li", "single_page": True}})
            assert json.loads(submission["content"][0]["text"])["accepted"] is False
            await scout.call("browser_navigate", {"url": url})
            verified = await scout.call("browser_verify", {"card_selector": ".roster > li"})
            assert json.loads(verified["content"][0]["text"])["pagination"]["total"] == 1
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_hidden_preload_links_cannot_certify_a_teacher_roster(tmp_path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        body = "<div hidden><a class='preload' href='/person'>Image preload</a></div>" + ROSTER
        await context.route("**/*", lambda route: route.fulfill(body=body, content_type="text/html"))
        page = IsolatedPage(await context.new_page(), context)
        url = "https://fixture.example.edu/roster"
        scout = BrowserScout(page, tmp_path, "branch",
                             {"school": "Fixture", "college": "Department", "url": url})
        try:
            await scout.call("browser_navigate", {"url": url})
            verified = await scout.call("browser_verify", {"card_selector": ".preload"})
            value = json.loads(verified["content"][0]["text"])
            assert value["visibleCardCount"] == 0
            assert value["hiddenCardCount"] == 1
            submission = await scout.call("submit_plan", {"plan": {
                "url": url, "card_selector": ".preload", "single_page": True}})
            assert json.loads(submission["content"][0]["text"])["accepted"] is False
        finally:
            await browser.close()
