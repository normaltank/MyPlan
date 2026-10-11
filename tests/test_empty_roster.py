"""Explicit empty states require fresh browser evidence, never just zero cards."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from server import agent_recon
from server.faculty_extractor import FacultyCandidate
from server.harvest_coverage import build_coverage_ledger
from server.harvest_workflow import CapturedRosterPage, capture_sources, reconcile_rosters


BRANCH = {"url": "https://example.edu/list", "card_selector": "#roster > .card",
          "single_page": True, "empty_state_selector": "#roster > .empty",
          "roster_container_selector": "#roster"}
HTML = '<main><div id="roster"><p class="empty">暂无教师</p></div></main>'


def proof(html=HTML):
    return {"version": 1, "confirmed": True, "url": BRANCH["url"],
            **{key: BRANCH[key] for key in ("card_selector", "empty_state_selector", "roster_container_selector")},
            "html_sha256": hashlib.sha256(html.encode()).hexdigest(), "captured_at": 1.0,
            "network_idle": True, "dom_stable": True, "container_count": 1, "marker_count": 1,
            "container_visible": True, "marker_visible": True, "marker_contained": True,
            "marker_has_content": True, "card_count": 0, "other_content_count": 0,
            "media_count": 0, "busy_count": 0, "pagination_count": 0}


def events(evidence):
    args = {key: BRANCH[key] for key in ("card_selector", "empty_state_selector", "roster_container_selector")}
    value = {"url": BRANCH["url"], "cardCount": 0, "pagination": {"total": 0}, "empty_roster": evidence}
    return [{"type": "tool/call", "data": {"callId": "empty", "name": "mcp__playwright__browser_verify",
             "arguments": json.dumps(args)}},
            {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": "empty",
             "content": [{"type": "text", "text": json.dumps(value)}]}]}}}]


def test_empty_plan_schema_and_evidence_require_the_pair_and_exact_selectors():
    plan = {"school": "样本大学", "college": "样本学院", "branches": [BRANCH]}
    agent_recon.validate_model_plan(plan, school=plan["school"], college=plan["college"])
    assert agent_recon.selector_evidence_issues(plan, events(proof())) == []
    for change in ({"empty_state_selector": None}, {"single_page": False}, {"kind": "navigation"},
                   {"next_selector": "a.next"}):
        bad = {**plan, "branches": [{**BRANCH, **change}]}
        with pytest.raises(agent_recon.PlanHygieneError):
            agent_recon.validate_model_plan(bad, school=plan["school"], college=plan["college"])
    for change in ({"confirmed": False}, {"media_count": 1}, {"empty_state_selector": ".invented"},
                   {"card_count": 1}, {"network_idle": False}):
        assert agent_recon.selector_evidence_issues(plan, events({**proof(), **change}))


def test_proof_binding_and_reconciliation_only_exempt_verified_empty_page(monkeypatch):
    from server.empty_roster import empty_roster_proof_valid
    evidence = proof()
    assert empty_roster_proof_valid(evidence, BRANCH, HTML)
    assert not empty_roster_proof_valid(evidence, BRANCH, HTML + '<span>changed</span>')
    assert not empty_roster_proof_valid({"confirmed": True}, BRANCH, HTML)
    assert not empty_roster_proof_valid({**evidence, "captured_at": 10 ** 10000}, BRANCH, HTML)
    page = CapturedRosterPage(BRANCH["url"], HTML, BRANCH, empty_roster_evidence=evidence)
    result = reconcile_rosters([page], {"branches": [BRANCH]})
    assert result.unresolved == [] and result.candidates == []
    assert result.observations[0]["status"] == "confirmed_empty"
    missing = reconcile_rosters([CapturedRosterPage(BRANCH["url"], HTML, BRANCH)], {})
    assert missing.unresolved
    # A verified card selector is now the extraction boundary, so the extractor
    # stands in at extract_with_review; the empty proof must still not win.
    monkeypatch.setattr("server.harvest_workflow.FacultyDirectoryExtractor.extract_with_review",
                        lambda *_, **__: ([FacultyCandidate(name="真实教师", url="https://example.edu/person")], []))
    conflicting = reconcile_rosters([page], {})
    assert len(conflicting.candidates) == 1
    assert any(item["status"] == "empty_roster_conflict" for item in conflicting.unresolved)


@pytest.mark.asyncio
async def test_source_capture_transfers_proof_and_coverage_rechecks_binding():
    async def capture(_context, branch):
        return [(branch["url"], HTML)], [], {"url": branch["url"], "end": "confirmed_empty_roster",
                                          "empty_roster": proof()}
    result = await capture_sources({"branches": [BRANCH]}, None, capture_branch=capture, discover_children=None)
    assert result.roster_pages[0].empty_roster_evidence == proof()
    ledger = build_coverage_ledger({"branches": [BRANCH]}, result, [], [], {}, [], [])
    assert ledger["complete"]
    result.branches[0]["end"] = "single_page_no_pagination_control_seen"
    assert not build_coverage_ledger({"branches": [BRANCH]}, result, [], [], {}, [], [])["complete"]
    result.branches[0]["end"] = "confirmed_empty_roster"
    result.branches[0]["empty_roster"]["html_sha256"] = "0" * 64
    assert not build_coverage_ledger({"branches": [BRANCH]}, result, [], [], {}, [], [])["complete"]


@pytest.mark.asyncio
@pytest.mark.browser
async def test_real_browser_empty_verification_rejects_hidden_content_media_loading_and_pagination():
    from server.empty_roster import verify_empty_roster, empty_roster_proof_valid, install_empty_roster_tracking
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            install_empty_roster_tracking(page)
            await page.route("**/*", lambda route: route.fulfill(status=200, body=HTML, content_type="text/html"))
            await page.goto(BRANCH["url"])
            html = await page.content()
            good = await verify_empty_roster(page, BRANCH, html)
            assert empty_roster_proof_valid(good, BRANCH, html)
            assert "暂无" not in json.dumps(good, ensure_ascii=False)
            await page.set_content('<div id="roster"><div class="empty"><p>暂无教师</p>'
                                   '<span>本名录为空</span></div></div>')
            visible_marker = await verify_empty_roster(page, BRANCH, await page.content())
            assert visible_marker["confirmed"] is True
            invalid = [
                '<div id="roster"></div>',
                '<div id="roster"><p class="empty" hidden>暂无教师</p></div>',
                '<div id="roster"><p class="empty">暂无教师</p><div hidden>隐藏教师</div></div>',
                '<div id="roster"><p class="empty">暂无教师</p><div class="card" hidden>教师</div></div>',
                '<div id="roster"><p class="empty">暂无教师</p><img hidden src="/poster"></div>',
                '<div id="roster" style="background-image:url(/poster)"><p class="empty">暂无教师</p></div>',
                '<div id="roster" aria-busy="true"><p class="empty">暂无教师</p></div>',
                HTML + '<a rel="next" href="/next">继续</a>',
                '<div id="roster"><p class="empty">暂无教师</p><iframe hidden></iframe></div>',
                HTML + '<script>setTimeout(()=>document.querySelector("#roster").insertAdjacentHTML("beforeend", "<div class=card>教师</div>"),100)</script>',
                *('<div id="roster"><div class="empty"><p>暂无教师</p>'
                  f'<span {attribute}>教师列表正在加载</span></div></div>'
                  for attribute in ('hidden', 'style="display:none"', 'aria-hidden="true"',
                                    'style="opacity:0"', 'style="visibility:hidden"')),
            ]
            for source in invalid:
                await page.set_content(source)
                captured = await page.content()
                result = await verify_empty_roster(page, BRANCH, captured)
                assert result["confirmed"] is False, source
            await page.set_content(HTML)
            await page.evaluate("document.querySelector('#roster').style.display='none'")
            assert not (await verify_empty_roster(page, BRANCH, await page.content()))["confirmed"]
        finally:
            await browser.close()


def test_request_tracking_is_shared_idempotent_and_unavailable_without_a_page():
    from server.browser_readiness import install_request_tracking
    class Page:
        def __init__(self):
            self.listeners = {}
        def on(self, event, callback):
            self.listeners[event] = callback
    page = Page()
    tracker = install_request_tracking(page)
    assert tracker.available and tracker.pending == set() and tracker.activity == 0
    assert install_request_tracking(page) is tracker
    request = SimpleNamespace(is_navigation_request=lambda: False)
    # Requests are hashable local objects in Playwright; use a plain test object.
    class Request:
        def is_navigation_request(self):
            return False
    request = Request()
    page.listeners["request"](request)
    assert request in tracker.pending and tracker.activity == 1
    page.listeners["requestfailed"](request)
    assert not tracker.pending and tracker.failures == 1
    assert not install_request_tracking(None).available


@pytest.mark.asyncio
@pytest.mark.browser
async def test_empty_roster_accepts_only_native_disabled_pagination_and_keeps_other_gates():
    from server.empty_roster import verify_empty_roster, install_empty_roster_tracking
    cases = [
        ('<div class="pagination"><button disabled>上一页</button><button disabled>下一页</button></div>', True),
        ('<div class="pagination"><button disabled>上一页</button><button>下一页</button></div>', False),
        ('<div class="pagination"><button aria-disabled="true">下一页</button></div>', False),
        ('<div class="pagination"><a aria-disabled="true" href="/next">下一页</a></div>', False),
        ('<div class="pagination"><a href="">下一页</a></div>', False),
        ('<div class="pagination"><button disabled><span hidden>隐藏教师</span></button></div>', False),
        ('<div class="pagination" onclick="location.href=\'/next\'"><button disabled>下一页</button></div>', False),
    ]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        install_empty_roster_tracking(page)
        await page.route('**/*', lambda request: request.fulfill(body=HTML, content_type='text/html'))
        try:
            await page.goto(BRANCH['url'])
            for controls, allowed in cases:
                source = '<main><div id="roster"><p class="empty">暂无教师</p>' + controls + '</div></main>'
                await page.set_content(source)
                result = await verify_empty_roster(page, BRANCH, await page.content())
                assert result['confirmed'] is allowed, (source, result)
            await page.set_content(HTML + '<div class="pagination"><a aria-disabled="true" href="/next">继续</a></div>')
            assert not (await verify_empty_roster(page, BRANCH, await page.content()))['confirmed']
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_late_network_request_cannot_confirm_still_loading_roster():
    from server.empty_roster import verify_empty_roster, install_empty_roster_tracking
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        release = asyncio.Event()
        try:
            page = await browser.new_page()
            install_empty_roster_tracking(page)
            await page.route("**/list", lambda route: route.fulfill(status=200, body=HTML, content_type="text/html"))
            async def slow(route):
                await release.wait()
                await route.fulfill(status=200, body="[]", content_type="application/json")
            await page.route("**/slow", slow)
            await page.goto(BRANCH["url"])
            await page.wait_for_load_state("networkidle")
            # A request finishing inside the verification window also means that
            # the entire window was not idle, even though pending ends empty.
            await page.evaluate("fetch('/slow'); undefined")
            await page.wait_for_timeout(30)
            async def release_soon():
                await asyncio.sleep(0.1)
                release.set()
            task = asyncio.create_task(release_soon())
            transient = await verify_empty_roster(page, BRANCH, await page.content())
            await task
            assert not transient["confirmed"]
            release.clear()
            # This request starts before verification, after the first idle state.
            await page.evaluate("fetch('/slow'); undefined")
            await page.wait_for_timeout(100)
            result = await verify_empty_roster(page, BRANCH, await page.content())
            assert not result["confirmed"]
        finally:
            release.set()
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_browser_verify_replays_empty_selector_arguments_with_real_dom(tmp_path):
    from server.recon_browser_mcp import BrowserScout
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            from server.empty_roster import install_empty_roster_tracking
            install_empty_roster_tracking(page)
            await page.route("**/*", lambda route: route.fulfill(status=200, body=HTML, content_type="text/html"))
            await page.goto(BRANCH["url"])
            from pathlib import Path
            await page.evaluate(Path(agent_recon.__file__).with_name("recon_dom_probe.js").read_text(encoding="utf-8"))
            scout = BrowserScout(page, tmp_path, phase="branch")
            args = {key: BRANCH[key] for key in ("card_selector", "empty_state_selector", "roster_container_selector")}
            reply = await scout.call("browser_verify", args)
            value = json.loads(reply["content"][0]["text"])
            assert value["empty_roster"]["confirmed"] is True
            assert agent_recon.selector_evidence_issues({"branches": [BRANCH]}, events(value["empty_roster"])) == []
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_hidden_pseudo_content_in_marker_or_visible_inline_descendant_is_not_empty():
    from server.empty_roster import verify_empty_roster, install_empty_roster_tracking
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            install_empty_roster_tracking(page)
            await page.route("**/*", lambda route: route.fulfill(status=200, body=HTML, content_type="text/html"))
            await page.goto(BRANCH["url"])
            for target in (".empty", ".empty span"):
                for style in ("display:none", "visibility:hidden", "opacity:0"):
                    source = (f'<style>{target}::after{{content:"教师列表正在加载";{style}}}</style>'
                              '<div id="roster"><div class="empty">暂无教师<span>本名录为空</span></div></div>')
                    await page.set_content(source)
                    result = await verify_empty_roster(page, BRANCH, await page.content())
                    assert not result["confirmed"], (target, style)
                    assert result["other_content_count"] > 0
                # Visible generated text within the marker remains legal markup.
                source = (f'<style>{target}::after{{content:"本名录为空"}}</style>'
                          '<div id="roster"><div class="empty">暂无教师<span>说明</span></div></div>')
                await page.set_content(source)
                assert (await verify_empty_roster(page, BRANCH, await page.content()))["confirmed"]
        finally:
            await browser.close()

# This component fixture stipulates verified source purpose; purpose integration is unmocked elsewhere.
pytestmark = pytest.mark.verified_source_boundary
