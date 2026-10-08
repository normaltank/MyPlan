"""A page turn is judged by the verified cards, not by which names the miner can read.

The change check after a "next" click used full-page name mining. When the next
page held only names that mining does not admit (rare surnames such as 禤 and
亓 go to human review), the signature was empty, the branch stopped with
``pagination_unchanged`` and the worker spent paid reconnaissance on a selector
that was right. With a verified card selector the cards themselves decide.
"""
import asyncio

from test_browser_batch_tool import FakeContext

from server.browser_batch_tool import _capture_branch, _wait_for_change

URL = "https://faculty.example.edu/roster"


def roster(people, chrome=""):
    cards = "".join(f'<li class="card"><a href="/p/{i}.htm">{name}</a><span>教授</span></li>'
                    for i, name in people)
    return f"<html><body><main><ul>{cards}</ul></main><footer>{chrome}</footer></body></html>"


FIRST = [(1, "张富有"), (2, "刘圣洁"), (3, "孙洁")]


def capture(pages):
    browser = FakeContext({URL: pages})
    return asyncio.run(_capture_branch(
        browser, {"url": URL, "next_selector": ".next", "card_selector": "main li.card"}))


def test_next_page_of_rare_surname_names_is_captured():
    pages, errors, evidence = capture([roster(FIRST), roster([(4, "禤海燕"), (5, "亓明珠")])])
    assert [status for status in (item["status"] for item in errors)] == []
    assert len(pages) == 2 and evidence["end"] == "next_control_absent"


def test_chrome_only_change_is_still_unchanged_pagination():
    pages, errors, _ = capture([roster(FIRST, "clock 1"), roster(FIRST, "clock 2")])
    assert len(pages) == 1
    assert [item["status"] for item in errors] == ["pagination_unchanged"]


def test_lazy_image_attributes_alone_are_not_a_page_turn():
    lazy = roster(FIRST).replace("<span>", '<img data-src="/a.jpg"><span>')
    loaded = roster(FIRST).replace("<span>", '<img src="/a.jpg"><span>')
    pages, errors, _ = capture([lazy, loaded])
    assert len(pages) == 1
    assert [item["status"] for item in errors] == ["pagination_unchanged"]


def test_page_turn_waits_for_busy_and_empty_cards_to_settle():
    before = roster(FIRST)
    after = roster([(4, '禤海燕')])

    class SettlingPage:
        url = URL
        calls = 0

        async def content(self):
            self.calls += 1
            if self.calls == 1:
                return '<main aria-busy="true"><li class="card">暂时的内容</li></main>'
            if self.calls == 2:
                return '<main><li class="card"></li></main>'
            if self.calls == 3:
                return '<main><li class="card">短暂过渡</li></main>'
            return after

    page = SettlingPage()
    result = asyncio.run(_wait_for_change(page, before, URL, seconds=1, unit_selector='main li.card'))
    assert result == after
    assert page.calls >= 5


def test_a_link_to_the_next_page_is_judged_after_that_page_has_loaded():
    # A slow CMS: the click commits the new URL at once, but its document takes
    # longer than the change window to load (传媒学院 副教授, 2026-10-08).
    after = roster([(4, '禤海燕')])

    class SlowNavigation:
        url = URL + "/1.htm"
        loaded = False

        async def wait_for_load_state(self, state, timeout):
            assert state == "domcontentloaded"
            await asyncio.sleep(0.5)
            self.loaded = True

        async def content(self):
            return after if self.loaded else roster(FIRST)

    result = asyncio.run(_wait_for_change(SlowNavigation(), roster(FIRST), URL, seconds=0.3,
                                          unit_selector='main li.card'))
    assert result == after
