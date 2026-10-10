"""A moved windowed pager is not a terminal page, including old checkpoints."""

import json

import pytest

from server.pagination_identity import next_identity, displaced_controls

URL = "https://faculty.example.edu/list/1"
CARDS = "main li"
FRAGILE = ".pages > span.next:nth-of-type(3) > a"
STABLE = ".pages > span.next > a"
FIRST = '<main><ul><li>张伟</li></ul><div class="pages"><span>1</span><span>2</span><span class="next"><a href="/list/2">Next</a></span></div></main>'
SECOND = '<main><ul><li>李明</li></ul><div class="pages"><span>1</span><span>2</span><span>3</span><span class="next"><a href="/list/3">Next</a></span></div></main>'
LAST = '<main><ul><li>陈明</li></ul><div class="pages"><span>1</span><span>2</span><span>3</span><span class="next" disabled>Next</span></div></main>'


def test_control_identity_follows_roster_scope_and_actual_role_not_ordinal():
    identity = next_identity(FIRST, CARDS, FRAGILE)
    assert displaced_controls(SECOND, CARDS, identity, URL) == [
        "https://faculty.example.edu/list/3"
    ]
    assert "张伟" not in json.dumps(identity, ensure_ascii=False)
    unrelated = LAST + '<aside><a href="/news">Next</a></aside>'
    assert displaced_controls(unrelated, CARDS, identity, URL) == []
    same_outer_scope = LAST.replace(
        "</main>",
        '<aside><div class="pages"><span class="next">'
        '<a href="/news">Next</a></span></div></aside></main>',
    )
    assert displaced_controls(same_outer_scope, CARDS, identity, URL) == []


async def context_for(browser, visits, bodies=None):
    from server.browser_context import new_browser_context

    context = await new_browser_context(browser, polite=False)
    bodies = bodies or {
        URL: FIRST,
        "https://faculty.example.edu/list/2": SECOND,
        "https://faculty.example.edu/list/3": LAST,
    }

    async def route(request):
        address = request.request.url
        visits.append(address)
        await request.fulfill(
            body=bodies.get(address, ""), content_type="text/html; charset=utf-8"
        )

    await context.route("**/*", route)
    return context


@pytest.mark.asyncio
@pytest.mark.browser
async def test_moved_control_with_changed_label_remains_unverified():
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import _capture_branch

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await context_for(
                browser,
                [],
                {
                    URL: FIRST,
                    "https://faculty.example.edu/list/2": SECOND.replace(
                        "Next", "Continue"
                    ),
                },
            )
            pages, errors, evidence = await _capture_branch(
                context, {"url": URL, "card_selector": CARDS, "next_selector": FRAGILE}
            )
            assert len(pages) == 2
            assert errors[0]["reason"] == "next_control_moved_outside_selector"
            assert evidence["end"] == "next_selector_not_verified"
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize(
    "second",
    [
        SECOND.replace('<a href="/list/3">', '<a class="active" href="/list/3">'),
        SECOND.replace('class="pages"', 'class="changed-pages"').replace(
            '<a href="/list/3">', '<a rel="next" href="/list/3">'
        ),
        SECOND.replace('<a href="/list/3">Next</a>', "<button>Next</button>"),
        SECOND.replace('class="pages"', 'class="changed-pages"').replace(
            "Next", "Continue"
        ),
    ],
)
async def test_changed_forward_slot_is_not_proof_of_a_terminal_page(second):
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import _capture_branch

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await context_for(
                browser, [], {URL: FIRST, "https://faculty.example.edu/list/2": second}
            )
            pages, errors, evidence = await _capture_branch(
                context, {"url": URL, "card_selector": CARDS, "next_selector": FRAGILE}
            )
            assert len(pages) == 2 and errors[0]["status"] == "pagination_not_verified"
            assert evidence["end"] == "next_selector_not_verified"
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_new_previous_relation_cannot_retain_a_forward_identity():
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import _capture_branch

    second = SECOND.replace('<a href="/list/3">', '<a rel="prev" href="/news/1">')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            visits = []
            context = await context_for(
                browser,
                visits,
                {URL: FIRST, "https://faculty.example.edu/list/2": second},
            )
            _, errors, evidence = await _capture_branch(
                context, {"url": URL, "card_selector": CARDS, "next_selector": STABLE}
            )
            assert errors[0]["reason"] == "next_control_identity_changed"
            assert evidence["next_clicks"] == 1
            assert "https://faculty.example.edu/news/1" not in visits
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_identical_sibling_pagers_cannot_authorize_a_binding():
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import _capture_branch

    html = FIRST.replace(
        "</main>",
        '<div class="pages"><span>1</span><span>2</span><span class="next">'
        '<a href="/news/1">Next</a></span></div></main>',
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            visits = []
            context = await context_for(browser, visits, {URL: html})
            _, errors, evidence = await _capture_branch(
                context,
                {
                    "url": URL,
                    "card_selector": CARDS,
                    "next_selector": "main > div.pages:nth-of-type(1) > span.next > a",
                },
            )
            assert errors[0]["reason"] == "next_control_binding_ambiguous"
            assert evidence["next_clicks"] == 0 and visits == [URL]
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_rejected_resume_does_not_retain_its_old_control_identity():
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import _capture_branch

    bodies = {
        URL: FIRST.replace("Next", "Continue"),
        "https://faculty.example.edu/list/2": SECOND.replace("Next", "Continue"),
        "https://faculty.example.edu/list/3": LAST,
    }
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await context_for(browser, [], bodies)
            pages, errors, evidence = await _capture_branch(
                context,
                {"url": URL, "card_selector": CARDS, "next_selector": STABLE},
                resume_pages=[
                    {"url": URL, "html": FIRST},
                    {
                        "url": "https://faculty.example.edu/list/2",
                        "html": SECOND.replace("李明", "旧内容"),
                    },
                ],
            )
            assert len(pages) == 3 and not errors
            assert evidence["resume_rejected"] == "frontier_changed_or_unavailable"
            assert evidence["restored_pages"] == 0
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize("widget_id", ["", 'id="pager"'])
async def test_real_terminal_widget_excludes_a_separate_news_next(widget_id):
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import _capture_branch

    news = '<div id="news" class="news"><span class="next"><a href="/news/2">Next</a></span></div>'
    bodies = {
        URL: FIRST.replace('class="pages"', f'class="pages" {widget_id}').replace(
            "</main>", news + "</main>"
        ),
        "https://faculty.example.edu/list/2": LAST.replace(
            'class="pages"', f'class="pages" {widget_id}'
        ).replace("</main>", news + "</main>"),
    }
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            visits = []
            context = await context_for(browser, visits, bodies)
            pages, errors, evidence = await _capture_branch(
                context, {"url": URL, "card_selector": CARDS, "next_selector": STABLE}
            )
            assert len(pages) == 2 and not errors
            assert evidence["end"] == "next_control_absent"
            assert "https://faculty.example.edu/news/2" not in visits
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize("selector,expected_count", [(FRAGILE, 2), (STABLE, 3)])
async def test_actual_browser_cannot_treat_shifted_next_as_end(
    selector, expected_count
):
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import _capture_branch

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            visits = []
            context = await context_for(browser, visits)
            pages, errors, evidence = await _capture_branch(
                context, {"url": URL, "card_selector": CARDS, "next_selector": selector}
            )
            assert len(pages) == expected_count
            if selector == FRAGILE:
                assert errors[0]["status"] == "pagination_not_verified"
                assert errors[0]["reason"] == "next_control_moved_outside_selector"
                assert evidence["end"] == "next_selector_not_verified"
            else:
                assert not errors and evidence["end"] == "next_control_absent"
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_legacy_false_complete_checkpoint_revalidates_its_terminal(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import _capture_sources_with_state
    from server.harvest_state import HarvestState

    branch = {"url": URL, "card_selector": CARDS, "next_selector": FRAGILE}
    plan = {"school": "测试大学", "college": "测试学院", "branches": [branch]}
    state = HarvestState(tmp_path, plan)
    state.save_roster_progress(branch, URL, FIRST, 0)
    state.save_roster_progress(branch, "https://faculty.example.edu/list/2", SECOND, 1)
    key = json.dumps(branch, sort_keys=True, separators=(",", ":"))
    refs = [
        {"url": r["url"], "key": r["key"]} for r in state.load_roster_progress(branch)
    ]
    state.save_page(
        "branch",
        URL,
        FIRST,
        key=key,
        meta={
            "pages": refs,
            "evidence": {
                "url": URL,
                "end": "next_control_absent",
                "visited_pages": 2,
                "next_clicks": 1,
            },
        },
    )
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            visits = []
            context = await context_for(browser, visits)
            capture, stats = await _capture_sources_with_state(plan, context, state)
            assert "https://faculty.example.edu/list/2" in visits
            assert stats["restored_branches"] == 0
            assert any(
                x["status"] == "pagination_not_verified" for x in capture.unresolved
            )
        finally:
            await browser.close()
