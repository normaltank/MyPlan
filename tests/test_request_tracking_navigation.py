"""Request tracking belongs to the current document (集美大学诚毅学院管理系, 10-09).

The scout's tab carried five requests that never reported back. Every later
page in that tab, example.com included, counted them as still loading, so every
profile proof failed with network_not_idle and nothing named the requests. The
scout tried other selectors until the 900 s deadline.
"""
from __future__ import annotations

import asyncio

import pytest

from server.browser_readiness import install_request_tracking


class _Frame:
    pass


class _Request:
    def __init__(self, frame, navigation=False, url="https://x.edu.cn/a.js", kind="script"):
        self.frame, self._navigation, self.url, self.resource_type = frame, navigation, url, kind

    def is_navigation_request(self):
        return self._navigation


class _Page:
    def __init__(self):
        self.main_frame, self.handlers = _Frame(), {}

    def on(self, event, handler):
        self.handlers[event] = handler


def test_a_new_document_does_not_inherit_the_previous_documents_requests():
    page = _Page()
    tracker = install_request_tracking(page)
    silent = [_Request(page.main_frame) for _ in range(5)]
    for request in silent:
        page.handlers["request"](request)
    document = _Request(page.main_frame, navigation=True, url="https://example.com/", kind="document")
    page.handlers["request"](document)
    assert tracker.pending == {document}
    # One of them is aborted after the navigation: not a failure of the new page.
    page.handlers["requestfailed"](silent[0])
    assert tracker.failures == 0 and tracker.failed_requests == []
    page.handlers["requestfinished"](document)
    assert not tracker.pending


@pytest.mark.asyncio
@pytest.mark.browser
async def test_requests_aborted_by_leaving_a_page_are_not_failures_of_the_next():
    from playwright.async_api import async_playwright

    async def serve(route):
        if route.request.url.endswith("/hang.js"):
            await asyncio.sleep(20)
            return
        if route.request.url.endswith("/a.htm"):
            return await route.fulfill(body='<p>A</p><script src="/hang.js"></script>', content_type="text/html")
        await route.fulfill(body="<p>B</p>", content_type="text/html")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context()
            await context.route("**/*", serve)
            page = await context.new_page()
            tracker = install_request_tracking(page)
            await page.goto("https://x.test/a.htm", wait_until="commit")
            await asyncio.sleep(0.5)
            await page.goto("https://x.test/b.htm")
            await asyncio.sleep(0.5)
            assert not tracker.pending and tracker.failures == 0
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_a_request_still_open_is_named_in_the_proof():
    from playwright.async_api import async_playwright
    from server.profile_structure import verify_profile_container

    async def serve(route):
        if route.request.url.endswith("/counter.js"):
            await asyncio.sleep(20)
            return
        await route.fulfill(body='<div id="c"><p>陈旭，教授，从事课程与教学论研究。</p></div>'
                                 '<script async src="/system/counter.js"></script>', content_type="text/html")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context()
            await context.route("**/*", serve)
            page = await context.new_page()
            install_request_tracking(page)
            await page.goto("https://x.test/info/1.htm", wait_until="domcontentloaded")
            proof = await verify_profile_container(page, "#c")
            assert proof["reason"] == "network_not_idle"
            assert {"type": "script", "status": "pending", "path": "/system/counter.js"} in proof["blocking_requests"]
        finally:
            await browser.close()
