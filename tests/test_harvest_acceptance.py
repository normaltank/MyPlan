"""Offline contract corpus: fixed fixture oracles, never collector-derived ones.

The browser double replays saved HTML and modeled controls. These tests prove
deterministic behavior on those bytes, not live site coverage or OCR accuracy.
"""

import copy
import asyncio
import hashlib
import json
from pathlib import Path

import openpyxl
import pytest

import server.browser_batch_tool as batch_tool
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters


FIXTURES = Path(__file__).parent / "fixtures" / "harvest_acceptance"
CASES = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
FIELDS = {"序号", "姓名", "职称", "邮箱", "学校", "学院", "采集人", "采集日期", "简介", "主页"}


@pytest.fixture(autouse=True)
def isolated_acceptance_runtime(monkeypatch):
    monkeypatch.setenv("HARVEST_TASK_DB", str(Path(__file__).parents[1] / "scratch" / "verification" / "tasks.db"))
    monkeypatch.setenv("HARVEST_DISABLE_NATIVE_CHROME_FALLBACK", "1")
    monkeypatch.setattr(batch_tool, "_batch_fetch_profiles_via_native_chrome", lambda _urls: {})
    monkeypatch.setattr(batch_tool, "_fetch_page_via_native_chrome", lambda _url: None)


class ReplayLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    async def count(self):
        return int(self.selector == ".next" and self.page.index + 1 < len(self.page.states))

    async def is_enabled(self):
        return True

    async def click(self):
        self.page.index += 1
        self.page.url = self.page.states[self.page.index]["url"]


class ReplayPage:
    def __init__(self, context):
        self.context, self.url, self.index, self.states = context, "", 0, []

    async def goto(self, url, **_kwargs):
        self.context.visits.append(url)
        self.url, self.index = url, 0
        self.states = self.context.pages.get(url, [])
        if not self.states:
            raise RuntimeError("fixture profile is deliberately unavailable")
        return type("Response", (), {"status": 200})()

    async def content(self):
        return self.states[self.index]["html"]

    def locator(self, selector):
        return ReplayLocator(self, selector)

    async def close(self):
        pass


class ReplayContext:
    def __init__(self, pages):
        self.visits = []
        self.pages = {
            url: [{"url": state["url"], "html": (FIXTURES / state["file"]).read_text(encoding="utf-8")}
                  for state in states]
            for url, states in pages.items()
        }

    async def new_page(self):
        return ReplayPage(self)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
async def test_fixed_harvest_acceptance_contract(case, tmp_path, monkeypatch):
    # No fallback may make an offline failure succeed by contacting the site.
    monkeypatch.setenv("HARVEST_DISABLE_NATIVE_CHROME_FALLBACK", "1")
    monkeypatch.setattr(batch_tool, "_batch_fetch_profiles_via_native_chrome", lambda _urls: {})
    monkeypatch.setattr(batch_tool, "_fetch_page_via_native_chrome", lambda _url: None)
    plan = copy.deepcopy(case["plan"])
    browser = ReplayContext(case["pages"])
    expected = case["expected"]

    report = await batch_tool.run_plan(plan, browser, tmp_path)

    # Assert blocker first, so failure output contains no teacher biographies.
    assert report["delivery_ready"] is expected["delivery_ready"], case["id"]
    assert report["total"] == expected["total"], case["id"]
    assert report["with_email"] == expected["with_email"], case["id"]
    assert report["no_email"] == expected["no_email"], case["id"]
    assert report["with_email"] + report["no_email"] == report["total"]
    assert report["directory_pages"] == expected["directory_pages"]
    assert report["candidate_count"] == expected["candidate_count"]
    coverage = report["coverage"]
    assert coverage["scope"] == "verified_roster_branches"
    assert coverage["complete"] is expected["delivery_ready"]
    assert coverage["candidate_count"] == expected["candidate_count"]
    assert coverage["discovery_completeness"] == "requires_independent_navigation_evidence"
    assert json.loads(Path(report["coverage_path"]).read_text(encoding="utf-8")) == coverage
    assert len(coverage["pages"]) == expected["directory_pages"]
    if expected["delivery_ready"]:
        assert coverage["issues"] == []
    else:
        assert coverage["issues"]
    if case["id"] == "ajax_same_url":
        assert len({page["state_id"] for page in coverage["pages"]}) == 2
        assert len({page["url"] for page in coverage["pages"]}) == 1
    if "excluded_count" in expected:
        assert report["excluded_count"] == expected["excluded_count"]
    if "unresolved_status" in expected:
        assert expected["unresolved_status"] in {item["status"] for item in report["unresolved"]}

    records = json.loads(Path(report["records_path"]).read_text(encoding="utf-8"))
    assert {(row["姓名"], row["主页"]) for row in records} == {tuple(pair) for pair in expected["identities"]}
    assert {(row["姓名"], row["主页"], row["邮箱"]) for row in records} == {
        tuple(triple) for triple in expected["email_assignments"]}
    assert all(set(row) == FIELDS for row in records)
    for name, fragments in expected.get("bio_fragments", {}).items():
        biography = next(row["简介"] for row in records if row["姓名"] == name)
        assert all(fragment in biography for fragment in fragments), "full biography lost an authored source paragraph"
        assert "网站导航" not in biography and "网站页脚" not in biography

    if expected["delivery_ready"]:
        assert report["unresolved"] == []
        workbook = openpyxl.load_workbook(report["excel_path"], read_only=True)
        try:
            assert workbook.sheetnames == ["教师列表", "无邮箱教师列表"]
            email_rows = list(workbook["教师列表"].values)
            no_email_rows = list(workbook["无邮箱教师列表"].values)
            assert len(email_rows) - 1 == expected["with_email"]
            assert len(no_email_rows) - 1 == expected["no_email"]
            assert len(email_rows[0]) == len(no_email_rows[0]) == 10
            assert {(row[1], row[9]) for row in email_rows[1:]}.isdisjoint(
                {(row[1], row[9]) for row in no_email_rows[1:]})
        finally:
            workbook.close()
    else:
        assert report["excel_path"] is None
        assert not list(tmp_path.glob("*.xlsx"))


def test_real_saved_directory_has_fixed_card_identity_oracle():
    source = json.loads((FIXTURES / "real_source.json").read_text(encoding="utf-8"))
    snapshot = FIXTURES / "real_hnucm_directory.html"
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == source["sha256"]
    result = reconcile_rosters(
        [CapturedRosterPage(source["url"], snapshot.read_text(encoding="utf-8"),
                            {"card_selector": source["selector"]})],
        {"source_kind": "university_faculty_roster"},
    )
    assert len(result.candidates) == source["expected_candidates"] == 60
    assert result.observations[0]["visible_cards"] == source["expected_visible_cards"] == 60
    identities = [(person.name, person.url) for person in result.candidates]
    assert set(identities) == {tuple(pair) for pair in source["expected_identities"]}
    assert tuple(source["expected_first_identity"]) in identities
    assert tuple(source["expected_last_identity"]) in identities
    assert result.observations[0]["unaccounted_cards"] == []
    assert result.unresolved == []


class BlockingReplayPage(ReplayPage):
    async def goto(self, url, **kwargs):
        if url == self.context.block_profile:
            self.context.visits.append(url)
            self.context.blocked.set()
            await asyncio.Event().wait()
        return await super().goto(url, **kwargs)

    def locator(self, selector):
        if selector == ".next" and self.context.block_pagination:
            page = self

            class BlockingLocator(ReplayLocator):
                async def click(self):
                    page.context.blocked.set()
                    await asyncio.Event().wait()

            return BlockingLocator(self, selector)
        return super().locator(selector)


class BlockingReplayContext(ReplayContext):
    def __init__(self, pages, *, block_profile=None, block_pagination=False):
        super().__init__(pages)
        self.block_profile = block_profile
        self.block_pagination = block_pagination
        self.blocked = asyncio.Event()

    async def new_page(self):
        return BlockingReplayPage(self)


@pytest.mark.asyncio
async def test_run_plan_cancelled_after_profile_success_restores_only_missing_pages(tmp_path):
    from server.harvest_state import HarvestState

    case = next(case for case in CASES if case["id"] == "ordinary_full_biography")
    plan = copy.deepcopy(case["plan"])
    plan["profile_concurrency"] = 1
    first = "https://acceptance.example.edu/p/1.htm"
    second = "https://acceptance.example.edu/p/2.htm"
    third = "https://acceptance.example.edu/p/3.htm"
    state = HarvestState(tmp_path / "task", plan)
    browser = BlockingReplayContext(case["pages"], block_profile=second)
    job = asyncio.create_task(batch_tool.run_plan(plan, browser, tmp_path / "interrupted", state=state))
    try:
        await asyncio.wait_for(browser.blocked.wait(), 2)
    finally:
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    assert state.load_page("profile", first) is not None
    assert state.load_page("profile", second) is None
    restarted = HarvestState(tmp_path / "task", plan)
    resumed_browser = ReplayContext(case["pages"])
    report = await batch_tool.run_plan(plan, resumed_browser, tmp_path / "resumed", state=restarted)
    assert resumed_browser.visits == [second, third]
    assert report["total"] == 3 and report["with_email"] == 2 and report["no_email"] == 1
    assert report["delivery_ready"] is True
    assert report["coverage"]["complete"] is True
    assert report["checkpoints"]["plan_digest"] == restarted.plan_digest
    assert report["checkpoints"]["restored_profiles"] == 1
    assert report["checkpoints"]["restored_branches"] == 1


@pytest.mark.asyncio
async def test_run_plan_complete_ajax_branch_restores_all_states_and_profiles(tmp_path):
    from server.harvest_state import HarvestState

    case = next(case for case in CASES if case["id"] == "ajax_same_url")
    plan = copy.deepcopy(case["plan"])
    initial_state = HarvestState(tmp_path / "task", plan)
    initial = await batch_tool.run_plan(plan, ReplayContext(case["pages"]), tmp_path / "initial",
                                        state=initial_state)
    original_capture_times = {row["url"]: row["captured_at"] for row in initial_state.list_pages("profile")}
    assert initial["directory_pages"] == 2 and initial["total"] == 6
    restarted_browser = ReplayContext({})
    restored = await batch_tool.run_plan(plan, restarted_browser, tmp_path / "restored",
                                         state=HarvestState(tmp_path / "task", plan))
    assert restarted_browser.visits == []
    assert restored["directory_pages"] == 2
    assert restored["total"] == restored["with_email"] == 6
    assert restored["delivery_ready"] is True
    assert restored["coverage"]["complete"] is True
    assert restored["coverage"]["scope"] == "verified_roster_branches"
    assert restored["checkpoints"]["restored_profiles"] == 6
    assert restored["checkpoints"]["restored_branches"] == 1
    restored_state = HarvestState(tmp_path / "task", plan)
    assert {row["url"]: row["captured_at"] for row in restored_state.list_pages("profile")} == original_capture_times
    assert len({page["state_id"] for page in restored["coverage"]["pages"]}) == 2
    assert {(page["url"], page["state_id"]) for page in restored["coverage"]["pages"]} == {
        (page["url"], page["state_id"]) for page in initial["coverage"]["pages"]}
    identities = json.loads(Path(restored["records_path"]).read_text(encoding="utf-8"))
    assert {(row["姓名"], row["主页"]) for row in identities} == {
        tuple(pair) for pair in case["expected"]["identities"]}


@pytest.mark.asyncio
async def test_run_plan_partial_ajax_branch_revisits_directory_after_cancel(tmp_path):
    from server.harvest_state import HarvestState

    case = next(case for case in CASES if case["id"] == "ajax_same_url")
    plan = copy.deepcopy(case["plan"])
    browser = BlockingReplayContext(case["pages"], block_pagination=True)
    job = asyncio.create_task(batch_tool.run_plan(plan, browser, tmp_path / "interrupted",
                                                 state=HarvestState(tmp_path / "task", plan)))
    try:
        await asyncio.wait_for(browser.blocked.wait(), 2)
    finally:
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    partial_state = HarvestState(tmp_path / "task", plan)
    assert len(partial_state.list_pages("roster")) == 1
    assert partial_state.list_pages("branch") == []  # A captured page does not prove terminal pagination.
    restarted_browser = ReplayContext(case["pages"])
    report = await batch_tool.run_plan(plan, restarted_browser, tmp_path / "resumed",
                                       state=HarvestState(tmp_path / "task", plan))
    assert restarted_browser.visits[0] == "https://acceptance.example.edu/roster"
    assert report["directory_pages"] == 2
    assert report["total"] == report["with_email"] == 6
    assert report["delivery_ready"] is True
    assert report["coverage"]["complete"] is True
    assert report["checkpoints"]["restored_branches"] == 0


@pytest.mark.asyncio
async def test_run_plan_changed_plan_cannot_restore_previous_branch_bundle(tmp_path):
    from server.harvest_state import HarvestState

    case = next(case for case in CASES if case["id"] == "ajax_same_url")
    plan = copy.deepcopy(case["plan"])
    await batch_tool.run_plan(plan, ReplayContext(case["pages"]), tmp_path / "initial",
                              state=HarvestState(tmp_path / "task", plan))
    changed = copy.deepcopy(plan)
    changed["branches"][0]["max_pages"] = 4
    browser = ReplayContext(case["pages"])
    report = await batch_tool.run_plan(changed, browser, tmp_path / "changed",
                                       state=HarvestState(tmp_path / "task", changed))
    assert browser.visits[0] == "https://acceptance.example.edu/roster"
    assert len(browser.visits) == 7
    assert report["directory_pages"] == 2 and report["total"] == 6
    assert report["checkpoints"]["restored_branches"] == report["checkpoints"]["restored_profiles"] == 0
