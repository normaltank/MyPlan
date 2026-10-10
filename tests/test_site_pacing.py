"""Per-site request pacing and rate-limit pauses shared by every fetch path.

A university site answered a real run with "每秒不能多于1个页面请求 ... IP临时冻结为10分钟".
The pace is kept in one SQLite clock so separate processes (recon MCP sessions,
the worker, the IDE batch tool) share it; a rate-limit answer pauses the site
for all of them instead of being read as page content.
"""
import asyncio
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from server import site_limits
from server.browser_context import IsolatedBrowserContext, IsolatedPage, PoliteRequest
from server.harvest_workflow import capture_profiles
from server.site_limits import (SiteRateLimited, check_document, check_response, freeze_site,
                                rate_limit_notice, reserve_site_turn)

FREEZE_NOTICE = ("<html><body>111.198.54.4，检测到您的IP在短时间内发起多次请求，疑似正在对网站进行CC攻击，"
                 "因此您的IP将被冻结一段时间(IP临时冻结为10分钟)。如果您是正常的网站监测扫描，"
                 "请您调整扫描软件的并发请求数设置（每秒不能多于1个页面请求）！</body></html>")


@pytest.fixture
def clock(tmp_path, monkeypatch):
    monkeypatch.setenv("HARVEST_SITE_CLOCK_DB", str(tmp_path / "clock.sqlite3"))
    monkeypatch.delenv("HARVEST_PER_SITE_INTERVAL_SECONDS", raising=False)  # the shipped default
    return tmp_path / "clock.sqlite3"


def test_configured_pace_spaces_page_starts_per_host(clock, monkeypatch):
    monkeypatch.setenv("HARVEST_PER_SITE_INTERVAL_SECONDS", "1")
    now = 1000.0
    assert reserve_site_turn("https://cs.x.edu.cn/a", now=now) == 0
    assert reserve_site_turn("https://cs.x.edu.cn/b", now=now) == 1
    assert reserve_site_turn("https://cs.x.edu.cn/c", now=now) == 2
    # Another college's subdomain keeps its own pace, so a batch is not serialised.
    assert reserve_site_turn("https://law.x.edu.cn/b", now=now) == 0
    assert reserve_site_turn("https://cs.x.edu.cn/d", now=now + 10) == 0  # idle host: no wait


def test_by_default_only_sites_that_froze_us_are_paced(clock):
    assert [reserve_site_turn("https://cs.x.edu.cn/a", now=5.0) for _ in range(2)] == [0, 0]
    later = freeze_site("https://cs.x.edu.cn/a", 60, "test") + 1
    assert [reserve_site_turn("https://cs.x.edu.cn/a", now=later) for _ in range(2)] == [0, 1]


def test_media_originals_are_not_paced(clock):
    assert [reserve_site_turn("https://cs.x.edu.cn/p.jpg", paced=False, now=5.0) for _ in range(3)] == [0, 0, 0]


def test_after_a_freeze_the_university_shares_one_pace(clock):
    until = freeze_site("https://cs.x.edu.cn/a", 60, "test")
    later = until + 1
    assert reserve_site_turn("https://cs.x.edu.cn/a", now=later) == 0
    assert reserve_site_turn("https://law.x.edu.cn/b", now=later) == 1  # learned: one server-wide firewall


def test_explicit_zero_means_no_configured_pace(clock, monkeypatch):
    monkeypatch.setenv("HARVEST_PER_SITE_INTERVAL_SECONDS", "0")
    assert [reserve_site_turn("https://cs.x.edu.cn/a", now=5.0) for _ in range(3)] == [0, 0, 0]


def test_separate_processes_share_one_pace(clock):
    freeze_site("https://a.z.edu.cn/", 60, "test")  # a site that froze us: paced from then on
    script = ("import sys,time; from server.site_limits import reserve_site_turn\n"
              "now=float(sys.argv[1]); print(' '.join(str(now+reserve_site_turn('https://a.z.edu.cn/', now=now))"
              " for _ in range(3)))")
    now = str(time.time())
    procs = [subprocess.Popen([sys.executable, "-c", script, now], stdout=subprocess.PIPE, text=True,
                              cwd=Path(__file__).resolve().parents[1]) for _ in range(2)]
    turns = sorted(float(value) for proc in procs for value in proc.communicate(timeout=60)[0].split())
    assert len(turns) == 6
    assert all(later - earlier >= 0.999 for earlier, later in zip(turns, turns[1:]))


def test_runtime_admissions_share_one_pace_across_processes(clock, monkeypatch):
    """Exercise real sync and async admissions, not only future reservations."""
    monkeypatch.setenv("HARVEST_PER_SITE_INTERVAL_SECONDS", "0.15")
    script = (
        "import asyncio,sys,time; from server.site_limits import site_turn,wait_site_turn\n"
        "async def main():\n"
        " starts=[]\n"
        " for _ in range(3):\n"
        "  if sys.argv[1]=='async': await site_turn('https://a.z.edu.cn/')\n"
        "  else: wait_site_turn('https://a.z.edu.cn/')\n"
        "  starts.append(time.monotonic())\n"
        " print(' '.join(str(value) for value in starts))\n"
        "asyncio.run(main())\n")
    procs = [subprocess.Popen([sys.executable, "-c", script, mode], stdout=subprocess.PIPE, text=True,
                              cwd=Path(__file__).resolve().parents[1]) for mode in ("async", "sync")]
    starts = []
    for proc in procs:
        starts.extend(float(value) for value in proc.communicate(timeout=15)[0].split())
        assert proc.returncode == 0
    starts.sort()
    assert len(starts) == 6
    assert all(later - earlier >= 0.145 for earlier, later in zip(starts, starts[1:]))


class _RuntimeClock:
    """Control scheduling without changing asyncio's own monotonic clock."""

    def __init__(self):
        self.wall = 1000.0
        self.elapsed = 0.0

    def time(self):
        return self.wall

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.wall += seconds
        self.elapsed += seconds

    async def asleep(self, seconds):
        self.sleep(seconds)
        await asyncio.sleep(0)


@pytest.fixture
def runtime_clock(monkeypatch):
    controlled = _RuntimeClock()

    async def in_thread(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(site_limits, "time", controlled)
    monkeypatch.setattr(site_limits, "asyncio", SimpleNamespace(to_thread=in_thread, sleep=controlled.asleep))
    return controlled


@pytest.mark.asyncio
@pytest.mark.parametrize("rollback_wall", [False, True])
async def test_late_thread_result_cannot_bunch_runtime_admissions(clock, runtime_clock, monkeypatch,
                                                               rollback_wall):
    """A delayed earlier caller must not use its stale turn after another caller."""
    monkeypatch.setenv("HARVEST_PER_SITE_INTERVAL_SECONDS", "0.2")
    issued, resume = asyncio.Event(), asyncio.Event()
    first = True

    async def delayed_thread(function, *args, **kwargs):
        nonlocal first
        result = function(*args, **kwargs)
        if first:
            first = False
            issued.set()
            await resume.wait()
        return result

    monkeypatch.setattr(site_limits.asyncio, "to_thread", delayed_thread)
    late = asyncio.create_task(site_limits.site_turn("https://cs.x.edu.cn/first"))
    await issued.wait()
    runtime_clock.sleep(0.4)
    await site_limits.site_turn("https://cs.x.edu.cn/second")
    earlier_release = runtime_clock.monotonic()
    if rollback_wall:
        runtime_clock.wall -= 10  # must not revive an old admission window
    resume.set()
    await late
    assert runtime_clock.monotonic() - earlier_release >= 0.199


@pytest.mark.asyncio
@pytest.mark.parametrize("paced", [False, True])
async def test_runtime_rechecks_freeze_extended_while_sleeping(clock, runtime_clock, monkeypatch, paced):
    monkeypatch.setattr(site_limits, "FREEZE_BOUNDS", (0.1, 10))
    url = "https://cs.x.edu.cn/profile.htm"
    until = freeze_site(url, 0.2, "initial")
    extended = False

    async def extend_during_sleep(delay):
        nonlocal until, extended
        runtime_clock.sleep(delay)
        if not extended:
            extended = True
            until = freeze_site("https://law.x.edu.cn/", 0.4, "extended while waiting")
        await asyncio.sleep(0)

    monkeypatch.setattr(site_limits.asyncio, "sleep", extend_during_sleep)
    await site_limits.site_turn(url, paced=paced)
    assert runtime_clock.time() >= until


def test_sync_runtime_rechecks_freeze_extended_while_sleeping(clock, runtime_clock, monkeypatch):
    monkeypatch.setattr(site_limits, "FREEZE_BOUNDS", (0.1, 10))
    url = "https://cs.x.edu.cn/profile.htm"
    until = freeze_site(url, 0.2, "initial")
    original_sleep = runtime_clock.sleep
    extended = False

    def extend_during_sleep(delay):
        nonlocal until, extended
        original_sleep(delay)
        if not extended:
            extended = True
            until = freeze_site("https://law.x.edu.cn/", 0.4, "extended while waiting")

    monkeypatch.setattr(runtime_clock, "sleep", extend_during_sleep)
    site_limits.wait_site_turn(url)
    assert runtime_clock.time() >= until


@pytest.mark.asyncio
@pytest.mark.parametrize("paced", [False, True])
async def test_delayed_unpaced_decision_rechecks_new_freeze(clock, runtime_clock, monkeypatch, paced):
    """Even with no configured pace, a stalled decision must not bypass a new freeze."""
    monkeypatch.setattr(site_limits, "FREEZE_BOUNDS", (0.1, 10))
    url = "https://cs.x.edu.cn/profile.htm"
    until = 0.0
    first = True

    async def delayed_thread(function, *args, **kwargs):
        nonlocal first, until
        result = function(*args, **kwargs)
        if first:
            first = False
            runtime_clock.sleep(0.2)
            until = freeze_site("https://law.x.edu.cn/", 0.4, "new freeze before handoff")
        return result

    monkeypatch.setattr(site_limits.asyncio, "to_thread", delayed_thread)
    await site_limits.site_turn(url, paced=paced)
    assert runtime_clock.time() >= until


@pytest.mark.asyncio
@pytest.mark.parametrize("paced", [False, True])
async def test_runtime_keeps_unpaced_requests_immediate(clock, runtime_clock, monkeypatch, paced):
    # Media ignore a configured page pace; ordinary pages retain the default of no pace.
    monkeypatch.setenv("HARVEST_PER_SITE_INTERVAL_SECONDS", "0" if paced else "0.4")
    for _ in range(3):
        await site_limits.site_turn("https://cs.x.edu.cn/resource", paced=paced)
    assert runtime_clock.monotonic() == 0


@pytest.mark.asyncio
async def test_cancelled_handoff_does_not_block_later_admissions(clock, runtime_clock, monkeypatch):
    monkeypatch.setenv("HARVEST_PER_SITE_INTERVAL_SECONDS", "0.2")
    issued = asyncio.Event()
    first = True

    async def cancelled_thread(function, *args, **kwargs):
        nonlocal first
        result = function(*args, **kwargs)
        if first:
            first = False
            issued.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(site_limits.asyncio, "to_thread", cancelled_thread)
    pending = asyncio.create_task(site_limits.site_turn("https://cs.x.edu.cn/cancelled"))
    await issued.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await asyncio.wait_for(site_limits.site_turn("https://cs.x.edu.cn/next"), timeout=1)
    assert runtime_clock.monotonic() < 1  # at most a bounded unused slot, never a held lease


def test_freeze_holds_every_start_until_it_ends(clock):
    until = freeze_site("https://cs.x.edu.cn/a", 600, "test")
    wait = reserve_site_turn("https://law.x.edu.cn/b")
    assert until - time.time() - 1 < wait <= 600
    # A shorter freeze reported later never shortens the pause.
    assert freeze_site("https://cs.x.edu.cn/a", 60, "test") == until


def test_freeze_notice_is_recognised_with_its_duration(clock):
    assert rate_limit_notice(FREEZE_NOTICE)
    with pytest.raises(SiteRateLimited, match="site_rate_limited x.edu.cn") as caught:
        check_document("https://cs.x.edu.cn/t/1.htm", FREEZE_NOTICE)
    assert 590 < caught.value.until - time.time() <= 600


@pytest.mark.parametrize("html", [
    # A real page that merely mentions the words is long, not a notice.
    "<html><body><p>张三，教授。</p>" + "<p>研究方向：网络安全、CC攻击检测与访问频率分析。</p>" * 60 + "</body></html>",
    "<html><body><h1>李四</h1><p>副教授，邮箱 lisi@x.edu.cn</p></body></html>",
    "",
])
def test_ordinary_pages_are_not_notices(html):
    assert rate_limit_notice(html) is None


def test_http_answers_that_pause_the_site(clock):
    with pytest.raises(SiteRateLimited):
        check_response("https://a.x.edu.cn/", 429, {})
    check_response("https://b.y.edu.cn/", 503, {})  # overload without Retry-After: ordinary retry
    check_response("https://b.y.edu.cn/", 403, {})
    with pytest.raises(SiteRateLimited) as caught:
        check_response("https://b.y.edu.cn/", 503, {"Retry-After": "120"})
    assert 110 < caught.value.until - time.time() <= 120


# ── Wrappers: every production request waits for its turn ──────────────────
class _RawResponse:
    def __init__(self, url, status=200, headers=None):
        self.url, self.status, self.headers = url, status, headers or {}


class _RawPage:
    def __init__(self, bodies, log):
        self.url, self.bodies, self.log = "about:blank", bodies, log

    async def goto(self, url, **_):
        self.log.append(("goto", url))
        self.url = url
        return _RawResponse(url)

    async def content(self):
        return self.bodies.get(self.url, f"<html><body><p>{self.url}</p></body></html>")

    async def close(self):
        pass


class _RawContext:
    def __init__(self, bodies, log):
        self.bodies, self.log = bodies, log

    async def new_page(self):
        return _RawPage(self.bodies, self.log)


@pytest.fixture
def turns(monkeypatch):
    seen = []

    async def fake_turn(url, paced=True):
        seen.append(url if paced else ("unpaced", url))
    monkeypatch.setattr("server.browser_context.site_turn", fake_turn)
    return seen


@pytest.mark.asyncio
async def test_page_navigation_and_clicks_wait_for_the_site_turn(turns, clock):
    log = []
    page = await IsolatedBrowserContext(_RawContext({}, log)).new_page()
    await page.goto("https://cs.x.edu.cn/list.htm")

    class _Locator:
        async def click(self, **_):
            log.append(("click",))
    from server.browser_context import IsolatedLocator
    await IsolatedLocator(page, _Locator(), "a.next").click()
    assert turns == ["https://cs.x.edu.cn/list.htm", "https://cs.x.edu.cn/list.htm"]
    assert log == [("goto", "https://cs.x.edu.cn/list.htm"), ("click",)]


@pytest.mark.asyncio
async def test_offline_replay_context_is_not_paced(turns, clock):
    page = await IsolatedBrowserContext(_RawContext({}, []), polite=False).new_page()
    await page.goto("https://cs.x.edu.cn/list.htm")
    assert turns == []


@pytest.mark.asyncio
async def test_notice_page_is_never_returned_as_content(turns, clock):
    url = "https://cs.x.edu.cn/t/1.htm"
    page = IsolatedPage(_RawPage({url: FREEZE_NOTICE}, []), None)
    await page.goto(url)
    with pytest.raises(SiteRateLimited):
        await page.content()


@pytest.mark.asyncio
async def test_asset_requests_wait_out_freezes_and_429_pauses_the_site(turns, clock):
    class _Api:
        async def get(self, url, **_):
            return _RawResponse(url, 429, {"retry-after": "90"})

        async def head(self, url, **_):
            return _RawResponse(url, 200, {"content-length": "5"})

    request = PoliteRequest(_Api())
    assert (await request.head("https://cs.x.edu.cn/p.jpg")).status == 200
    with pytest.raises(SiteRateLimited):
        await request.get("https://cs.x.edu.cn/p.jpg")
    assert turns == [("unpaced", "https://cs.x.edu.cn/p.jpg")] * 2
    assert reserve_site_turn("https://www.x.edu.cn/") > 80  # the freeze holds the whole site


@pytest.mark.asyncio
async def test_profile_capture_pauses_on_a_notice_and_then_reads_the_real_page(clock, monkeypatch):
    """The page that met the notice is opened again after the pause, not read as a profile."""
    monkeypatch.setenv("HARVEST_PER_SITE_INTERVAL_SECONDS", "0")
    monkeypatch.setattr(site_limits, "FREEZE_BOUNDS", (0.5, 0.5))
    url = "https://cs.x.edu.cn/t/1.htm"
    bodies = {url: FREEZE_NOTICE}
    log = []

    class _Recovering(_RawPage):
        async def content(self):
            html = await super().content()
            bodies.pop(url, None)  # the site answers normally once the freeze is over
            return html

    class _Context(_RawContext):
        async def new_page(self):
            return _Recovering(self.bodies, self.log)

    started = time.monotonic()
    pages, unresolved = await capture_profiles(IsolatedBrowserContext(_Context(bodies, log)), [url], 1)
    assert unresolved == []
    assert "CC攻击" not in pages[url] and url in pages[url]
    assert time.monotonic() - started >= 0.4  # waited out the freeze before the second open
    assert [entry for entry in log if entry[0] == "goto"] == [("goto", url)] * 2


@pytest.mark.browser
@pytest.mark.asyncio
async def test_real_browser_pages_on_one_host_keep_the_pace(clock, monkeypatch):
    from playwright.async_api import async_playwright

    from server.browser_context import new_browser_context
    monkeypatch.setenv("HARVEST_PER_SITE_INTERVAL_SECONDS", "0.4")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await new_browser_context(browser)
            starts = []

            async def serve(route):
                if route.request.resource_type == "document":
                    starts.append(time.monotonic())
                await route.fulfill(body="<html><body><p>ok</p></body></html>", content_type="text/html")
            await context.route("**/*", serve)

            async def open_one(index):
                page = await context.new_page()
                await page.goto(f"https://cs.pace.edu.cn/{index}.htm")
                await page.close()
            await asyncio.gather(*(open_one(index) for index in range(4)))
        finally:
            await browser.close()
    # Four document requests on one host cannot start within less than three intervals.
    assert len(starts) == 4
    assert max(starts) - min(starts) >= 1.1
