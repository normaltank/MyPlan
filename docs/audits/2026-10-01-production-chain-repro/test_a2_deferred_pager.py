"""A2: a pager rendered shortly after DOMContentLoaded is read as 'next_control_absent'.

The recon verified next_selector (nextCount == 1) after the page settled. The
harvest reads the DOM right after goto(wait_until='domcontentloaded'); a pager
that a ready-handler inserts a few hundred ms later is not there yet, the branch
terminates on page 1 with a TERMINAL end state, coverage is 'complete' and the
college is delivered with page 2 missing.
"""
import pytest
from playwright.async_api import async_playwright

from server.browser_batch_tool import run_plan
from server.browser_context import new_browser_context
from server.harvest_state import HarvestState

ROOT = 'https://fixture.edu/'
PEOPLE = {1: ['王建国', '李晓明'], 2: ['张伟东', '刘海涛']}


def roster(number, delay_ms):
    cards = ''.join(f'<li><a href="/person/{name}">{name}</a> 教授</li>' for name in PEOPLE[number])
    pager = ''
    if number == 1:
        insert = ("var d=document.createElement('a');d.className='next';d.href='/list/2';"
                  "d.textContent='下一页';document.querySelector('.pager').appendChild(d);")
        pager = (f"<div class='pager'></div><script>document.addEventListener('DOMContentLoaded',"
                 f"function(){{setTimeout(function(){{{insert}}},{delay_ms});}});</script>"
                 if delay_ms is not None else f"<div class='pager'><a class='next' href='/list/2'>下一页</a></div>")
    else:
        pager = "<div class='pager'></div>"
    return f'<html><body><main><ul class="t">{cards}</ul>{pager}</main></body></html>'


def make_route(delay_ms):
    async def route(request):
        path = request.request.url.removeprefix(ROOT)
        from urllib.parse import unquote
        path = unquote(path)
        if path.startswith('list/'):
            body = roster(int(path.split('/')[-1]), delay_ms)
        elif path.startswith('person/'):
            name = path.split('/')[-1]
            body = (f'<html><body><header>学院导航</header><article><h1>{name}</h1><p>职称：教授</p>'
                    f'<p>{name}，长期从事材料研究。</p></article><footer>版权</footer></body></html>')
        else:
            await request.abort()
            return
        await request.fulfill(body=body, content_type='text/html; charset=utf-8')
    return route


PLAN = {'school': '样本大学', 'college': '样本学院',
        'branches': [{'url': ROOT + 'list/1', 'card_selector': 'ul.t > li', 'next_selector': '.pager a.next'}]}


async def harvest(tmp_path, delay_ms):
    state = HarvestState(tmp_path / 'task', PLAN)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await new_browser_context(browser)
        await ctx.raw_context.route('**/*', make_route(delay_ms))
        report = await run_plan(PLAN, ctx, tmp_path / 'task' / 'attempt-1', state=state)
        await browser.close()
    return report


@pytest.mark.asyncio
async def test_control_server_rendered_pager_collects_both_pages(tmp_path):
    report = await harvest(tmp_path, None)
    print('control', report['directory_pages'], report['total'], report['delivery_ready'],
          [i.get('status') for i in report['unresolved']])
    assert report['directory_pages'] == 2 and report['total'] == 4


@pytest.mark.asyncio
async def test_deferred_pager_is_delivered_as_complete_with_page_two_missing(tmp_path):
    report = await harvest(tmp_path, 400)
    print('deferred', report['directory_pages'], report['total'], report['delivery_ready'],
          report['branches'][0].get('end'), [i.get('status') for i in report['unresolved']])
    assert report['directory_pages'] == 1
    assert report['branches'][0]['end'] == 'next_control_absent'
    assert report['coverage']['complete'] is True
    assert report['delivery_ready'] is True and report['total'] == 2  # 张伟东、刘海涛 silently missing
