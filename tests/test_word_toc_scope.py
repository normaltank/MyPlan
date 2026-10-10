"""Word TOC metadata has a bounded structural role, not a global hiding rule."""

import pytest
from server.profile_content import scope_profile_html

URL = "https://faculty.example.edu/p/1"


def toc(hidden, href="file:///D:/cv.docx#_Toc1234", label="科研成果"):
    return f'<p><a href="{href}"><span>{label}</span>{hidden}</a></p>'


LEADER = '<span style="display:none">' + "-" * 99 + "</span>"
PAGE = '<span style="display:none">12</span>'


def test_word_toc_leader_and_page_are_not_hidden_biography_data():
    html = "<article><p>完整本人履历。</p>" + toc(LEADER + PAGE) + "</article>"
    scoped, evidence, issue = scope_profile_html(html, "article", URL)
    assert issue is None
    assert "完整本人履历。" in scoped and "科研成果" in scoped
    assert "-" * 99 not in scoped and ">12<" not in scoped
    assert evidence["word_toc_decorations_removed"] == 2


@pytest.mark.parametrize(
    "hidden",
    [
        '<span style="display:none">王芳</span>',
        '<span style="display:none">wf@example.edu</span>',
        '<span style="display:none">John Smith</span>',
        '<span style="display:none"><img src="/cv.png"></span>',
        '<img style="display:none" src="/cv.png">',
    ],
)
def test_toc_link_does_not_make_person_data_or_media_trivial(hidden):
    _, _, issue = scope_profile_html(
        "<article>" + toc(LEADER + hidden + PAGE) + "</article>", "article", URL
    )
    assert issue["status"] == "profile_content_selector_hidden"


@pytest.mark.parametrize(
    ("hidden", "href", "label"),
    [
        (LEADER + PAGE, "/profile/123#_Toc1234", "科研成果"),
        (LEADER + PAGE, "/cv.docx#other", "科研成果"),
        (LEADER + PAGE, "/cv.docx#_Toc1234", ""),
        (PAGE, "/cv.docx#_Toc1234", "科研成果"),
        (PAGE + LEADER, "/cv.docx#_Toc1234", "科研成果"),
    ],
)
def test_unknown_toc_relationship_remains_blocking(hidden, href, label):
    _, _, issue = scope_profile_html(
        "<article>" + toc(hidden, href, label) + "</article>", "article", URL
    )
    assert issue["status"] == "profile_content_selector_hidden"


def test_unbound_hidden_numbers_and_long_leaders_are_still_blocked():
    for hidden in (PAGE, LEADER):
        _, _, issue = scope_profile_html(
            "<article><p>完整本人履历。</p>" + hidden + "</article>", "article", URL
        )
        assert issue["status"] == "profile_content_selector_hidden"


@pytest.mark.asyncio
@pytest.mark.browser
async def test_live_word_toc_proof_uses_the_same_bound_structure(tmp_path):
    from test_profile_verification_leniency import verify

    html = "<article><p>完整本人履历。</p>" + toc(LEADER + PAGE) + "</article>"
    result = await verify(tmp_path, html)
    assert result["confirmed"] and result["hidden_content_count"] == 0
    broken = await verify(
        tmp_path, html.replace(PAGE, '<span style="display:none">王芳</span>')
    )
    assert not broken["confirmed"]


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize("replacement", ["王芳", "John Smith", '<img src="/cv.png">'])
async def test_old_word_binding_cannot_ignore_changed_meaningful_content(replacement):
    from playwright.async_api import async_playwright
    from server.profile_structure import _FACTS, _HIDDEN_PATHS
    from server.word_toc import live_decorations

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            await page.set_content(
                "<article><p>完整履历。</p>" + toc(LEADER + PAGE) + "</article>"
            )
            args = {
                "selector": "article",
                "start": None,
                "trivialChars": 16,
                "wordToc": live_decorations(await page.content(), "article"),
            }
            assert (await page.evaluate(_FACTS, args))["hidden_content_count"] == 0
            await page.evaluate(
                'value=>document.querySelector("a span[style]").innerHTML=value',
                replacement,
            )
            assert (await page.evaluate(_FACTS, args))["hidden_content_count"] > 0
            assert await page.evaluate(_HIDDEN_PATHS, args)
        finally:
            await browser.close()
