"""Real browser replay of interrupted roster pagination, without live requests."""
import asyncio
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from server.browser_batch_tool import _capture_branch, run_plan
from server.harvest_state import HarvestState

ROOT = 'https://example.edu/list/1'
BRANCH = {'url': ROOT, 'card_selector': 'main li', 'next_selector': '.next', 'max_pages': 3}
PLAN = {'school': 'Fixture', 'college': 'College', 'branches': [BRANCH]}
NAMES = ['张富有', '刘圣洁', '孙洁']


def roster(number, *, next_href=None, ajax=False, name=None):
    html = f'<main><ul><li><a href="/p/{number}">{name or NAMES[number-1]}</a>教授</li></ul>'
    if next_href:
        html += f'<a class="next" href="{next_href}">Next</a>'
    if ajax:
        html += '<button class="next" onclick="document.querySelector(\'li\').textContent=\'刘圣洁教授\'">Next</button>'
    return html + '</main>'


def checkpoint(state):
    return lambda url, html, sequence: state.save_roster_progress(BRANCH, url, html, sequence)


async def replay_context(browser, bodies, visits, *, blocked=None):
    context = await browser.new_context()
    async def route(request):
        url = request.request.url
        visits.append(url)
        if url == blocked:
            await request.abort()
        elif url == 'https://example.edu/updates':
            await asyncio.sleep(0.8)
            await request.fulfill(body='updated', content_type='text/plain')
        elif url in bodies:
            await request.fulfill(body=bodies[url], content_type='text/html; charset=utf-8')
        else:
            await request.abort()
    await context.route('**/*', route)
    return context


@pytest.mark.asyncio
@pytest.mark.browser
async def test_interrupted_url_pagination_resumes_at_last_success_without_revisiting_first(tmp_path):
    bodies = {ROOT: roster(1, next_href='/list/2'),
              'https://example.edu/list/2': roster(2, next_href='/list/3'),
              'https://example.edu/list/3': roster(3)}
    state = HarvestState(tmp_path, PLAN)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        first_visits = []
        context = await replay_context(browser, bodies, first_visits, blocked='https://example.edu/list/3')
        pages, errors, _ = await _capture_branch(context, BRANCH, on_page=checkpoint(state))
        assert len(pages) == 2 and errors
        await context.close()
        saved = HarvestState(tmp_path, PLAN).load_roster_progress(BRANCH)
        assert len(saved) == 2
        original_times = [row['captured_at'] for row in saved]
        visits = []
        context = await replay_context(browser, bodies, visits)
        pages, errors, evidence = await _capture_branch(context, BRANCH,
            on_page=checkpoint(state), resume_pages=saved)
        assert visits == ['https://example.edu/list/2', 'https://example.edu/list/3']
        assert len(pages) == 3 and not errors and evidence['end'] == 'next_control_absent'
        assert evidence['restored_pages'] == 2 and evidence['resumed_from_page'] == 2
        assert [row['captured_at'] for row in state.load_roster_progress(BRANCH)][:2] == original_times
        assert all(name in pages[index][1] for index, name in enumerate(NAMES))
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_changed_frontier_discards_saved_prefix_and_restarts_from_entry(tmp_path):
    state = HarvestState(tmp_path, PLAN)
    state.save_roster_progress(BRANCH, ROOT, roster(1, next_href='/list/2'), 0)
    state.save_roster_progress(BRANCH, 'https://example.edu/list/2', roster(2, next_href='/list/3'), 1)
    saved = state.load_roster_progress(BRANCH)
    bodies = {ROOT: roster(1, next_href='/list/2', name='王小明'),
              'https://example.edu/list/2': roster(2, next_href='/list/3', name='李小红'),
              'https://example.edu/list/3': roster(3)}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        visits = []
        context = await replay_context(browser, bodies, visits)
        pages, errors, evidence = await _capture_branch(context, BRANCH,
            on_page=checkpoint(state), resume_pages=saved)
        assert visits == ['https://example.edu/list/2', ROOT, 'https://example.edu/list/2', 'https://example.edu/list/3']
        assert not errors and evidence['restored_pages'] == 0
        assert '王小明' in pages[0][1] and '张富有' not in pages[0][1]
        assert '李小红' in pages[1][1]
        await browser.close()


def test_corrupt_or_noncontiguous_progress_is_not_reused(tmp_path):
    state = HarvestState(tmp_path, PLAN)
    first = state.save_roster_progress(BRANCH, ROOT, roster(1, next_href='/list/2'), 0)
    state.save_roster_progress(BRANCH, 'https://example.edu/list/2', roster(2, next_href='/list/3'), 1)
    Path(first['html_path']).write_text('changed', encoding='utf-8')
    assert state.load_roster_progress(BRANCH) == []
    with pytest.raises(ValueError):
        state.save_roster_progress(BRANCH, 'https://example.edu/list/3', roster(3), 2)


@pytest.mark.asyncio
@pytest.mark.browser
async def test_same_url_ajax_states_restart_from_entry(tmp_path):
    state = HarvestState(tmp_path, PLAN)
    first = roster(1, ajax=True)
    state.save_roster_progress(BRANCH, ROOT, first, 0)
    state.save_roster_progress(BRANCH, ROOT, roster(2), 1)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        visits = []
        context = await replay_context(browser, {ROOT: first}, visits)
        pages, _, evidence = await _capture_branch(context, BRANCH, resume_pages=state.load_roster_progress(BRANCH))
        assert visits == [ROOT] and evidence['restored_pages'] == 0
        assert '张富有' in pages[0][1]
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_resume_does_not_reset_page_limit_budget(tmp_path):
    branch = {**BRANCH, 'max_pages': 2}
    plan = {**PLAN, 'branches': [branch]}
    state = HarvestState(tmp_path, plan)
    state.save_roster_progress(branch, ROOT, roster(1, next_href='/list/2'), 0)
    state.save_roster_progress(branch, 'https://example.edu/list/2', roster(2, next_href='/list/3'), 1)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        visits = []
        context = await replay_context(browser, {'https://example.edu/list/2': roster(2, next_href='/list/3')}, visits)
        pages, errors, evidence = await _capture_branch(context, branch, resume_pages=state.load_roster_progress(branch))
        assert len(pages) == 2 and evidence['end'] == 'unresolved'
        assert any(item['status'] == 'page_limit_reached' for item in errors)
        assert visits == ['https://example.edu/list/2']
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_delayed_frontier_change_is_checked_before_restoring_prefix(tmp_path):
    state = HarvestState(tmp_path, PLAN)
    state.save_roster_progress(BRANCH, ROOT, roster(1, next_href='/list/2'), 0)
    state.save_roster_progress(BRANCH, 'https://example.edu/list/2', roster(2, next_href='/list/3'), 1)
    delayed = roster(2, next_href='/list/3') + '<script>setTimeout(()=>document.querySelector("li a").textContent="李小红",150)</script>'
    bodies = {ROOT: roster(1, next_href='/list/2'), 'https://example.edu/list/2': delayed,
              'https://example.edu/list/3': roster(3)}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        visits = []
        context = await replay_context(browser, bodies, visits)
        _, _, evidence = await _capture_branch(context, BRANCH, resume_pages=state.load_roster_progress(BRANCH))
        assert evidence['restored_pages'] == 0 and ROOT in visits
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_pending_ajax_after_networkidle_does_not_validate_old_frontier(tmp_path):
    state = HarvestState(tmp_path, PLAN)
    state.save_roster_progress(BRANCH, ROOT, roster(1, next_href='/list/2'), 0)
    state.save_roster_progress(BRANCH, 'https://example.edu/list/2', roster(2, next_href='/list/3'), 1)
    delayed = roster(2, next_href='/list/3') + '<script>setTimeout(()=>fetch("/updates").then(()=>document.querySelector("li a").textContent="李小红"),600)</script>'
    bodies = {ROOT: roster(1, next_href='/list/2'), 'https://example.edu/list/2': delayed,
              'https://example.edu/list/3': roster(3)}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        visits = []
        context = await replay_context(browser, bodies, visits)
        _, _, evidence = await _capture_branch(context, BRANCH, resume_pages=state.load_roster_progress(BRANCH))
        assert evidence['restored_pages'] == 0 and ROOT in visits
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_batch_resume_preserves_all_people_and_fetches_only_missing_profiles(tmp_path):
    bodies = {ROOT: roster(1, next_href='/list/2'),
              'https://example.edu/list/2': roster(2, next_href='/list/3'),
              'https://example.edu/list/3': roster(3)}
    for number, name in enumerate(NAMES, 1):
        bodies[f'https://example.edu/p/{number}'] = f'<article><h1>{name}</h1><p>完整履历：{name}的学习经历、科研项目与论文。</p><p>邮箱：p{number}@example.edu</p></article>'
    state = HarvestState(tmp_path / 'state', PLAN)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await replay_context(browser, bodies, [], blocked='https://example.edu/list/3')
        first = await run_plan(PLAN, context, tmp_path / 'first', state=state)
        assert not first['delivery_ready'] and first['total'] == 2
        await context.close()
        visits = []
        context = await replay_context(browser, bodies, visits)
        resumed = await run_plan(PLAN, context, tmp_path / 'resumed', state=state)
        assert resumed['delivery_ready'] and resumed['total'] == 3
        assert resumed['with_email'] + resumed['no_email'] == 3
        assert resumed['coverage']['complete']
        assert resumed['checkpoints']['restored_roster_pages'] == 0  # fresh source relationship proof
        assert visits == [ROOT, 'https://example.edu/list/2', 'https://example.edu/list/3', 'https://example.edu/p/3']
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_cancellation_preserves_last_successful_page_and_cursor(tmp_path):
    bodies = {ROOT: roster(1, next_href='/list/2'),
              'https://example.edu/list/2': roster(2, next_href='/list/3'),
              'https://example.edu/list/3': roster(3)}
    state = HarvestState(tmp_path, PLAN)
    def cancel_after_second(url, html, sequence):
        state.save_roster_progress(BRANCH, url, html, sequence)
        if sequence == 1:
            asyncio.current_task().cancel()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await replay_context(browser, bodies, [])
        job = asyncio.create_task(_capture_branch(context, BRANCH, on_page=cancel_after_second))
        with pytest.raises(asyncio.CancelledError):
            await job
        await context.close()
        reopened = HarvestState(tmp_path, PLAN)
        saved = reopened.load_roster_progress(BRANCH)
        assert len(saved) == 2
        visits = []
        context = await replay_context(browser, bodies, visits)
        pages, errors, _ = await _capture_branch(context, BRANCH,
            on_page=checkpoint(reopened), resume_pages=saved)
        assert len(pages) == 3 and not errors
        assert visits == ['https://example.edu/list/2', 'https://example.edu/list/3']
        await browser.close()

# This component fixture stipulates verified source purpose; purpose integration is unmocked elsewhere.
pytestmark = pytest.mark.verified_source_boundary
