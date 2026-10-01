"""A1: a paginated branch without max_pages stops at page 21 under the worker's checkpoint state.

Same synthetic 22-page fixture as tests/test_quantity_limits.py, but captured the
way the production worker does it (run_plan with HarvestState).
"""
import pytest
from playwright.async_api import async_playwright

from server.browser_batch_tool import _capture_branch, run_plan
from server.browser_context import new_browser_context
from server.harvest_state import HarvestState

ROOT = 'https://fixture.edu/'
SURNAMES = '王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁'
GIVEN = '建国'


def page(number):
    nxt = f'<a class="next" href="/list/{number+1}">下一页</a>' if number < 22 else ''
    name = SURNAMES[number - 1] + GIVEN
    return (f'<html><body><main><ul><li><a href="/person/{number}">{name}</a> 教授</li></ul>'
            f'{nxt}</main></body></html>')


async def route(request):
    url = request.request.url
    path = url.removeprefix(ROOT)
    if path.startswith('list/'):
        body = page(int(path.split('/')[-1]))
    elif path.startswith('person/'):
        number = int(path.split('/')[-1])
        name = SURNAMES[number - 1] + GIVEN
        body = (f'<html><body><header>导航</header><article><h1>{name}</h1>'
                f'<p>职称：教授</p><p>{name}，研究方向为材料科学。</p></article></body></html>')
    else:
        await request.abort()
        return
    await request.fulfill(body=body, content_type='text/html; charset=utf-8')


PLAN = {'school': '样本大学', 'college': '样本学院',
        'branches': [{'url': ROOT + 'list/1', 'card_selector': 'main li', 'next_selector': '.next'}]}


@pytest.mark.asyncio
async def test_without_state_all_22_pages_are_captured(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await new_browser_context(browser)
        await ctx.raw_context.route('**/*', route)
        pages, errors, evidence = await _capture_branch(ctx, PLAN['branches'][0])
        await browser.close()
    assert len(pages) == 22 and not errors and evidence['end'] == 'next_control_absent'


@pytest.mark.xfail(strict=True, reason="A1: checkpoint caps unbounded rosters at 20 pages")
@pytest.mark.asyncio
async def test_worker_style_run_plan_with_state_captures_all_22_pages(tmp_path):
    state = HarvestState(tmp_path / 'task', PLAN)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await new_browser_context(browser)
        await ctx.raw_context.route('**/*', route)
        report = await run_plan(PLAN, ctx, tmp_path / 'task' / 'attempt-1', state=state)
        await browser.close()
    statuses = [item.get('status') for item in report['unresolved']]
    print('directory_pages', report['directory_pages'], 'statuses', statuses)
    print([item.get('error') for item in report['unresolved'] if item.get('status') == 'browser_navigation_failed'])
    # Observed on main: directory_pages == 21, browser_navigation_failed
    # (ValueError: invalid roster sequence), delivery_ready False.
    assert report['directory_pages'] == 22 and not statuses
    assert report['delivery_ready'] is True
