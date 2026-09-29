import asyncio
from types import SimpleNamespace

import pytest

from server import harvest_workflow


PLAN = {"school": "Fixture", "college": "College", "branches": [{"url": "https://example.edu/list", "card_selector": "main li", "single_page": True}]}


def state_type():
    from server.harvest_state import HarvestState
    return HarvestState


def test_state_survives_restart_and_isolates_complete_plan_and_task(tmp_path):
    State = state_type()
    state = State(tmp_path / "task-a", PLAN)
    state.save_page("profile", "https://example.edu/person", "<main>Original</main>", meta={"final_url": "https://example.edu/person/"})
    reopened = State(tmp_path / "task-a", dict(reversed(list(PLAN.items()))))
    assert reopened.load_page("profile", "https://example.edu/person") == "<main>Original</main>"
    assert reopened.load_page_record("profile", "https://example.edu/person")["meta"]["final_url"].endswith("/")
    changed = {**PLAN, "profile_ready_selector": "article"}
    assert State(tmp_path / "task-a", changed).load_page("profile", "https://example.edu/person") is None
    assert State(tmp_path / "task-b", PLAN).load_page("profile", "https://example.edu/person") is None


def test_state_rejects_corrupt_missing_and_expired_html(tmp_path, monkeypatch):
    from server import harvest_state
    monkeypatch.setattr(harvest_state.time, "time", lambda: 1000)
    state = harvest_state.HarvestState(tmp_path, PLAN, max_age_seconds=60)
    record = state.save_page("profile", "https://example.edu/person", "<main>Saved</main>")
    from pathlib import Path
    path = Path(record["html_path"])
    path.write_text("<main>Changed</main>", encoding="utf-8")
    assert state.load_page("profile", record["url"]) is None
    state.save_page("profile", record["url"], "<main>Saved</main>")
    monkeypatch.setattr(harvest_state.time, "time", lambda: 1061)
    assert state.load_page("profile", record["url"]) is None
    monkeypatch.setattr(harvest_state.time, "time", lambda: 1001)
    path.unlink()
    assert state.load_page("profile", record["url"]) is None
    assert state.list_pages("profile") == []


def test_ajax_same_url_pages_have_separate_keys_and_metadata(tmp_path):
    state = state_type()(tmp_path, PLAN)
    url = PLAN["branches"][0]["url"]
    state.save_page("roster", url, "<li>Page 1</li>", key="branch-0/page-1", meta={"page": 1, "end": False})
    state.save_page("roster", url, "<li>Page 2</li>", key="branch-0/page-2", meta={"page": 2, "end": True})
    assert state.load_page("roster", url, key="branch-0/page-1") == "<li>Page 1</li>"
    assert state.load_page_record("roster", url, key="branch-0/page-2")["meta"]["end"] is True
    assert len(state.list_pages("roster")) == 2


def test_interrupted_atomic_write_preserves_previous_checkpoint(tmp_path, monkeypatch):
    from server import harvest_state
    state = harvest_state.HarvestState(tmp_path, PLAN)
    url = "https://example.edu/person"
    state.save_page("profile", url, "<main>Previous</main>")
    def interrupted(*args):
        raise OSError("interrupted replacement")
    monkeypatch.setattr(harvest_state.os, "replace", interrupted)
    with pytest.raises(OSError):
        state.save_page("profile", url, "<main>Incomplete replacement</main>")
    assert state.load_page("profile", url) == "<main>Previous</main>"
    assert not list(state.html_directory.glob("*.tmp"))


def test_state_connections_allow_independent_browser_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    state = state_type()(tmp_path, PLAN)
    def save(index):
        state.save_page("profile", f"https://example.edu/p{index}", f"<main>Page {index}</main>")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, range(12)))
    assert len(state.list_pages("profile")) == 12


class BrowserContext:
    def __init__(self, *, failures=0, block_url=None):
        self.visits = []
        self.failures = failures
        self.block_url = block_url
        self.blocked = asyncio.Event()
        self.closed = 0

    async def new_page(self):
        context = self
        class Page:
            async def goto(self, url, **kwargs):
                self.url = url
                context.visits.append(url)
                if url == context.block_url:
                    context.blocked.set()
                    await asyncio.Event().wait()
                if context.failures:
                    context.failures -= 1
                    raise TimeoutError("temporary browser timeout")
                return SimpleNamespace(status=200)

            async def content(self):
                return "<main>" + self.url + "</main>"

            async def close(self):
                context.closed += 1
        return Page()


@pytest.mark.asyncio
async def test_cancellation_keeps_success_and_restart_visits_only_missing_profile(tmp_path):
    state = state_type()(tmp_path, PLAN)
    first, second = "https://example.edu/p1", "https://example.edu/p2"
    context = BrowserContext(block_url=second)
    job = asyncio.create_task(harvest_workflow.capture_profiles(context, [first, second], 1, state=state))
    await asyncio.wait_for(context.blocked.wait(), 2)
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert state.load_page("profile", first) == "<main>" + first + "</main>"
    restored = state_type()(tmp_path, PLAN)
    context = BrowserContext()
    pages, unresolved = await harvest_workflow.capture_profiles(context, [first, second], 1, state=restored)
    assert context.visits == [second]
    assert pages[first] and pages[second] and not unresolved


@pytest.mark.asyncio
async def test_profile_transient_retry_is_bounded_and_failures_remain_unresolved():
    context = BrowserContext(failures=2)
    pages, unresolved = await harvest_workflow.capture_profiles(context, ["https://example.edu/p1"], 1)
    assert len(context.visits) == 3 and pages["https://example.edu/p1"] and not unresolved
    context = BrowserContext(failures=20)
    pages, unresolved = await harvest_workflow.capture_profiles(context, ["https://example.edu/p1"], 1)
    assert len(context.visits) == 3 and context.closed == 3
    assert pages["https://example.edu/p1"] is None
    assert [item["status"] for item in unresolved] == ["profile_navigation_failed"]
