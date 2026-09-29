"""A roster's sampled URLs must carry actual, exact profile scope evidence."""
import json

import pytest
from playwright.async_api import async_playwright

from server.agent_recon import selector_evidence_issues, validate_model_plan
from server.recon_browser_mcp import BrowserScout


URL = "https://scope.example.edu/list"
PROFILES = ["https://scope.example.edu/p/1", "https://scope.example.edu/p/2"]
PLAN = {"school": "样本大学", "college": "样本学院", "branches": [
    {"url": URL, "single_page": True, "card_selector": ".cards>li",
     "profile_content_selector": "article"}]}


def event(name, args, result, cid):
    return [{"type": "tool/call", "data": {"callId": cid, "name": "mcp__playwright__" + name,
                                             "arguments": json.dumps(args)}},
            {"type": "tool/result", "data": {"message": {"content": [
                {"toolCallId": cid, "content": [{"type": "text", "text": json.dumps(result)}]}]}}}]


def roster_events():
    return event("browser_verify", {"card_selector": ".cards>li"},
                 {"url": URL, "cardCount": 2, "pagination": {"total": 0}, "profileSamples": PROFILES}, "0")


def scope_events(url, selector="article", cid="1"):
    return event("browser_verify_profile", {"profile_content_selector": selector},
                 {"url": url, "profile_content_selector": selector,
                  "profile_content_start_selector": None, "confirmed": True,
                  "container_count": 1, "container_visible": True, "dom_stable": True,
                  "network_idle": True, "html_sha256": "a" * 64, "selected_html_sha256": "b" * 64}, cid)


def test_model_scope_is_structure_only_and_requires_each_sampled_url():
    validate_model_plan(PLAN, school=PLAN["school"], college=PLAN["college"])
    assert selector_evidence_issues(PLAN, roster_events())[0]["status"] == "profile_content_not_verified"
    partial = roster_events() + scope_events(PROFILES[0])
    assert selector_evidence_issues(PLAN, partial)[0]["status"] == "profile_content_not_verified"
    assert selector_evidence_issues(PLAN, partial + scope_events(PROFILES[1], cid="2")) == []
    assert selector_evidence_issues(PLAN, partial + scope_events("https://scope.example.edu/unrelated", cid="2"))
    assert selector_evidence_issues(PLAN, partial + scope_events(PROFILES[1], "main", cid="2"))


def test_actual_personal_samples_require_scope_but_profileless_roster_does_not():
    unscoped = {**PLAN, "branches": [{key: value for key, value in PLAN["branches"][0].items()
                                      if key != "profile_content_selector"}]}
    assert selector_evidence_issues(unscoped, roster_events())[0]["status"] == "profile_content_selector_missing"
    profileless = event("browser_verify", {"card_selector": ".cards>li"},
                        {"url": URL, "cardCount": 2, "pagination": {"total": 0}, "profileSamples": [None, None]}, "0")
    assert selector_evidence_issues(unscoped, profileless) == []


@pytest.mark.asyncio
@pytest.mark.browser
async def test_browser_profile_tool_reports_structural_proof_without_teacher_fields(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            scout = BrowserScout(page, tmp_path)
            await page.route("**/*", lambda route: route.fulfill(body='<main><div id="text"><hr><p>完整履历正文与邮箱字段留在本地。</p></div></main>', content_type="text/html"))
            await page.goto(PROFILES[0])
            result = await scout.call("browser_verify_profile", {"profile_content_selector": "#text",
                                                                 "profile_content_start_selector": "#text>hr"})
            text = result["content"][0]["text"]
            value = json.loads(text)
            assert value["confirmed"] is True
            assert value["container_count"] == 1
            assert "履历" not in text and "邮箱字段" not in text
            import hashlib
            snapshot = sorted(tmp_path.glob('*.html'))[-1].read_text(encoding='utf-8')
            assert value['html_sha256'] == hashlib.sha256(snapshot.encode('utf-8')).hexdigest()
            hidden = await scout.call("browser_verify_profile", {"profile_content_selector": "body"})
            assert not json.loads(hidden["content"][0]["text"])["confirmed"]
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_profile_probe_rejects_stylesheet_hidden_retained_content_but_ignores_before_boundary(tmp_path):
    from server.browser_context import new_browser_context
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser)
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path)
            bodies = [
                '<style>.gone{display:none}</style><article><p>可见完整正文</p><p class="gone">另一份隐藏内容</p></article>',
                '<style>.gone{display:none}</style><article><div class="gone">发布信息</div><hr><p>完整正文</p></article>']
            source = bodies[0]
            await page.route('**/*', lambda route: route.fulfill(body=source, content_type='text/html'))
            await page.goto(PROFILES[0])
            reply = await scout.call('browser_verify_profile', {'profile_content_selector': 'article'})
            assert json.loads(reply['content'][0]['text'])['confirmed'] is False
            source = bodies[1]
            await page.goto(PROFILES[1])
            reply = await scout.call('browser_verify_profile', {'profile_content_selector': 'article',
                                                               'profile_content_start_selector': 'article>hr'})
            assert json.loads(reply['content'][0]['text'])['confirmed'] is True
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_profile_samples_include_anchor_cards_and_deduplicate_before_budget(tmp_path):
    from server.browser_context import new_browser_context
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser)
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path)
            await page.route('**/*', lambda route: route.fulfill(body=
                '<div class="cards"><a href="/p/1">甲</a><a href="/p/1">重复</a><a href="/p/2">乙</a></div>',
                content_type='text/html'))
            await page.goto(URL)
            reply = await scout.call('browser_verify', {'card_selector': '.cards>a'})
            value = json.loads(reply['content'][0]['text'])
            assert value['linkedSamples'] == PROFILES
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_missing_stylesheet_cannot_confirm_a_visible_profile(tmp_path):
    from server.browser_context import new_browser_context
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser)
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path)
            async def route(request):
                if request.request.url.endswith('.css'):
                    await request.abort()
                else:
                    await request.fulfill(body='<link rel="stylesheet" href="/profile.css"><article><p>完整正文。</p></article>',
                                          content_type='text/html')
            await page.route('**/*', route)
            await page.goto(PROFILES[0])
            reply = await scout.call('browser_verify_profile', {'profile_content_selector':'article'})
            assert json.loads(reply['content'][0]['text'])['confirmed'] is False
        finally:
            await browser.close()


def test_any_two_profiles_linked_from_verified_pages_prove_the_scope():
    page2 = "https://scope.example.edu/list/2"
    later = ["https://scope.example.edu/p/3", "https://scope.example.edu/p/4"]
    plan = {"school": "样本大学", "college": "样本学院", "branches": [
        {"url": URL, "next_selector": ".next", "card_selector": ".cards>li", "profile_content_selector": "article"}]}
    events = (event("browser_verify", {"card_selector": ".cards>li", "next_selector": ".next"},
                    {"url": URL, "cardCount": 2, "nextCount": 1, "nextUrl": page2, "nextDisabled": False,
                     "pagination": {"total": 1}, "linkedSamples": PROFILES}, "0")
              + event("browser_verify", {"card_selector": ".cards>li"},
                      {"url": page2, "cardCount": 2, "pagination": {"total": 1}, "linkedSamples": later}, "1"))
    one = selector_evidence_issues(plan, events + scope_events(later[0], cid="2"))
    assert [issue["status"] for issue in one] == ["profile_content_not_verified"]
    assert one[0]["sample_urls"] == PROFILES + later
    assert selector_evidence_issues(plan, events + scope_events(later[0], cid="2")
                                    + scope_events(later[1], cid="3")) == []
