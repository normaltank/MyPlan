import json
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from server.browser_batch_tool import run_plan
from server.harvest_state import HarvestState

EMPTY_URL = 'https://example.edu/empty'
BRANCH = {'url': EMPTY_URL, 'single_page': True, 'card_selector': '#roster .card',
          'empty_state_selector': '#roster .empty', 'roster_container_selector': '#roster'}
EMPTY_HTML = '<main><div id="roster"><p class="empty">暂无教师</p></div></main>'
PLAN = {'school': '样本大学', 'college': '样本学院', 'source_kind': 'university_faculty_roster',
        'branches': [BRANCH, {'url': 'https://example.edu/list', 'card_selector': 'main li', 'single_page': True}]}


async def fixture_context(browser, sources, visits):
    context = await browser.new_context()
    async def route(request):
        url = request.request.url
        visits.append(url)
        if url in sources:
            await request.fulfill(body=sources[url], content_type='text/html; charset=utf-8')
        else:
            await request.abort()
    await context.route('**/*', route)
    return context


def mixed_sources():
    sources = {EMPTY_URL: EMPTY_HTML}
    names = ['张富有', '刘圣洁', '孙洁']
    sources['https://example.edu/list'] = '<main><ul>' + ''.join(
        f'<li><a href="/p/{number}">{name}</a>教授</li>' for number, name in enumerate(names, 1)) + '</ul></main>'
    for number, name in enumerate(names, 1):
        sources[f'https://example.edu/p/{number}'] = f'<article><h1>{name}</h1><p>完整履历：{name}的学习经历、科研项目与论文。</p><p>邮箱：p{number}@example.edu</p></article>'
    return sources


@pytest.mark.asyncio
@pytest.mark.browser
async def test_confirmed_empty_branch_is_counted_as_covered_beside_normal_teachers(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await fixture_context(browser, mixed_sources(), [])
        report = await run_plan(PLAN, context, tmp_path)
        assert report['delivery_ready'] and report['coverage']['complete']
        assert report['total'] == 3 and report['with_email'] + report['no_email'] == 3
        assert report['branches'][0]['end'] == 'confirmed_empty_roster'
        assert report['roster_observations'][0]['status'] == 'confirmed_empty'
        assert report['unresolved'] == []
        rows = json.loads(open(report['records_path'], encoding='utf-8').read())
        assert all('科研项目与论文' in row['简介'] for row in rows)
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_empty_branch_is_revisited_and_changed_content_blocks_delivery(tmp_path):
    state = HarvestState(tmp_path / 'state', PLAN)
    sources = mixed_sources()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await fixture_context(browser, sources, [])
        first = await run_plan(PLAN, context, tmp_path / 'first', state=state)
        assert first['delivery_ready']
        await context.close()
        sources[EMPTY_URL] = EMPTY_HTML.replace('</div>', '<img hidden src="/poster"></div>')
        visits = []
        context = await fixture_context(browser, sources, visits)
        report = await run_plan(PLAN, context, tmp_path / 'second', state=state)
        assert EMPTY_URL in visits
        assert not report['delivery_ready'] and not report['excel_path']
        assert any(item['status'] == 'empty_roster_not_verified' for item in report['unresolved'])
        assert report['total'] == 3  # Complete normal teacher observations remain saved.
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_zero_cards_without_visible_empty_marker_are_not_confirmed(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await fixture_context(browser, {EMPTY_URL: '<main><div id="roster"></div></main>'}, [])
        report = await run_plan({**PLAN, 'branches': [BRANCH]}, context, tmp_path)
        assert not report['delivery_ready'] and not report['excel_path']
        assert any(item['status'] == 'empty_roster_not_verified' for item in report['unresolved'])
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_entirely_empty_college_reports_explicit_review_requirement(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await fixture_context(browser, {EMPTY_URL: EMPTY_HTML}, [])
        report = await run_plan({**PLAN, 'branches': [BRANCH]}, context, tmp_path)
        assert report['total'] == 0 and not report['delivery_ready'] and not report['excel_path']
        assert any(item['status'] == 'zero_person_delivery_requires_review' for item in report['unresolved'])
        await browser.close()


@pytest.mark.asyncio
async def test_zero_person_review_does_not_repeat_structure_model_calls(tmp_path, monkeypatch):
    from server import agent_worker, agent_recon
    worker = agent_worker.AgentWorker(db_path=tmp_path / 'tasks.db', output_dir=tmp_path / 'out')
    task = SimpleNamespace(school=PLAN['school'], college=PLAN['college'], url=EMPTY_URL)
    calls = []
    async def recon(*_args, **_kwargs):
        calls.append(1)
        return agent_recon.ReconResult({**PLAN, 'branches': [BRANCH]}, 0, 'completed', 'fixture')
    async def collect(*_args):
        return {'delivery_ready': False, 'unresolved': [{'status': 'zero_person_delivery_requires_review', 'url': EMPTY_URL}]}
    monkeypatch.setattr(agent_worker, 'reconnoiter', recon)
    monkeypatch.setattr(worker, '_run_browser_plan', collect)
    report = await worker._harvest_with_repair(task, tmp_path / 'out')
    assert not report['delivery_ready'] and len(calls) == 1

# This component fixture stipulates verified source purpose; purpose integration is unmocked elsewhere.
pytestmark = pytest.mark.verified_source_boundary
