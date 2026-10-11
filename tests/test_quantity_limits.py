import pytest

from server import agent_worker
from server.agent_recon import validate_model_plan
from server.browser_batch_tool import _capture_branch, _discover_children, _validate_plan


def test_default_worker_does_not_stop_a_college_at_a_model_request_count(tmp_path):
    args = agent_worker._build_parser().parse_args([])
    assert args.max_recon_calls == args.max_recon_tokens == 0
    assert args.max_discovery_calls == args.max_branch_calls == 0
    assert args.recon_alert_tokens == args.recon_alert_calls == 0
    worker = agent_worker.AgentWorker(db_path=tmp_path/'tasks.db', output_dir=tmp_path/'out')
    assert worker.phase_call_limits == {'discovery': 0, 'branch': 0}
    plan = {'school': '样本大学', 'college': '样本学院', 'branches': [{
        'url': 'https://example.edu/list', 'card_selector': 'main li',
        'next_selector': '.next', 'max_pages': 120}]}
    _validate_plan(plan)
    validate_model_plan(plan, school=plan['school'], college=plan['college'])


@pytest.mark.browser
@pytest.mark.asyncio
async def test_long_roster_continues_past_twenty_and_navigation_past_thirty(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    root = 'https://fixture.edu/'
    async def route(request):
        path = request.request.url.removeprefix(root)
        if path == 'navigation':
            html = '<main>' + ''.join(f'<a href="/list/{i}">名单</a>' for i in range(35)) + '</main>'
        else:
            number = int(path.split('/')[-1])
            next_control = f'<a class="next" href="/list/{number+1}">下一页</a>' if number < 22 else ''
            html = f'<main><ul><li><a href="/person/{number}">张富有</a></li></ul>{next_control}</main>'
        await request.fulfill(body=html, content_type='text/html; charset=utf-8')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await new_browser_context(browser)
        await context.raw_context.route('**/*', route)
        try:
            pages, errors, evidence = await _capture_branch(context, {'url': root+'list/1',
                'card_selector': 'main li', 'next_selector': '.next'})
            assert not errors and len(pages) == 22
            assert evidence['end'] == 'next_control_absent'
            _, urls, errors, _ = await _discover_children(context, {'url': root+'navigation',
                'kind': 'navigation', 'child_link_selector': 'main a'})
            assert not errors and len(urls) == 35
        finally:
            await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_actual_pagination_cycle_stops_without_a_count_cap(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    root = 'https://fixture.edu/'
    async def route(request):
        index = 1 if request.request.url.endswith('/1') else 2
        html = f'<main><li><a href="/person/{index}">张富有</a></li><a class="next" href="/{3-index}">下一页</a></main>'
        await request.fulfill(body=html, content_type='text/html; charset=utf-8')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await new_browser_context(browser)
        await context.raw_context.route('**/*', route)
        try:
            pages, errors, evidence = await _capture_branch(context, {'url': root+'1',
                'card_selector': 'main li', 'next_selector': '.next'})
            assert len(pages) == 2 and errors[0]['status'] == 'pagination_cycle'
            assert evidence['end'] == 'unresolved'
        finally:
            await browser.close()

# This component fixture stipulates verified source purpose; purpose integration is unmocked elsewhere.
pytestmark = pytest.mark.verified_source_boundary
