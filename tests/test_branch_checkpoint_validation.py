"""Damaged complete-branch checkpoints recover through the browser batch interface."""
import json

import pytest

from server.browser_batch_tool import run_plan
from server.harvest_state import HarvestState
from test_browser_batch_tool import FakeContext, profile, roster


URL = "https://faculty.example.edu/roster"
BRANCH = {"url": URL, "card_selector": "main li", "single_page": True}
PLAN = {"school": "Fixture", "college": "College", "branches": [BRANCH]}
NAMES = ["张富有", "刘圣洁", "孙洁"]


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing_evidence", "nonlist_pages", "bad_reference", "missing_reference_key",
                                   "wrong_reference_key", "unterminated", "wrong_terminal", "malformed_end", "wrong_count", "wrong_sequence"])
async def test_incomplete_bundle_metadata_is_a_cache_miss_and_revisited(tmp_path, damage):
    html = roster(list(enumerate(NAMES)))
    state = HarvestState(tmp_path, PLAN)
    key = json.dumps(BRANCH, sort_keys=True, separators=(",", ":"))
    record = state.save_page("roster", URL, html, key=key + "|0",
                             meta={"branch_url": URL, "sequence": 0})
    metadata = {"pages": [{"url": URL, "key": record["key"]}],
                "evidence": {"url": URL, "end": "single_page_no_pagination_control_seen", "visited_pages": 1}}
    if damage == "missing_evidence":
        metadata.pop("evidence")
    elif damage == "nonlist_pages":
        metadata["pages"] = "corrupt"
    elif damage == "bad_reference":
        metadata["pages"] = ["corrupt"]
    elif damage == "missing_reference_key":
        metadata["pages"][0].pop("key")
    elif damage == "wrong_reference_key":
        metadata["pages"][0]["key"] = "other_branch|0"
    elif damage == "unterminated":
        metadata["evidence"]["end"] = "unresolved"
    elif damage == "wrong_terminal":
        metadata["evidence"]["end"] = "next_control_disabled"
    elif damage == "malformed_end":
        metadata["evidence"]["end"] = []
    elif damage == "wrong_count":
        metadata["evidence"]["visited_pages"] = "1"
    elif damage == "wrong_sequence":
        state.save_page("roster", URL, html, key=record["key"], meta={"branch_url": URL, "sequence": 9})
    state.save_page("branch", URL, html, key=key, meta=metadata)
    browser = FakeContext({URL: html, **{
        f"https://faculty.example.edu/p/{i}.htm": profile(name, f"person{i}@example.edu")
        for i, name in enumerate(NAMES)}})
    result = await run_plan(PLAN, browser, tmp_path / "output", state=state)
    assert URL in browser.visits
    assert result["checkpoints"]["restored_branches"] == 0
    assert result["delivery_ready"] and result["total"] == 3


@pytest.mark.asyncio
async def test_healthy_complete_bundle_restores_the_original_capture(tmp_path):
    state = HarvestState(tmp_path, PLAN)
    bodies = {URL: roster(list(enumerate(NAMES))), **{
        f"https://faculty.example.edu/p/{i}.htm": profile(name, f"person{i}@example.edu")
        for i, name in enumerate(NAMES)}}
    first = await run_plan(PLAN, FakeContext(bodies), tmp_path / "first", state=state)
    captured_at = state.list_pages("branch")[0]["captured_at"]
    browser = FakeContext({})
    second = await run_plan(PLAN, browser, tmp_path / "second", state=state)
    assert not browser.visits
    assert first["delivery_ready"] and second["delivery_ready"]
    assert second["total"] == 3 and second["checkpoints"]["restored_branches"] == 1
    assert state.list_pages("branch")[0]["captured_at"] == captured_at
