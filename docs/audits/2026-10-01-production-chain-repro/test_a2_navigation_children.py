"""A2 (navigation variant): child links a ready-handler appends ~300 ms after
DOMContentLoaded are not discovered; the missing department is silently absent
and the college is still delivery_ready."""
from urllib.parse import unquote

import pytest
from playwright.async_api import async_playwright

from server.browser_batch_tool import run_plan
from server.browser_context import new_browser_context
from server.harvest_state import HarvestState

ROOT = 'https://fixture.edu/'
DEPTS = {'d1': ['王建国'], 'd2': ['李晓明'], 'd3': ['张伟东']}


def nav(delay):
    static = ''.join(f'<li><a href="/dept/{d}">系{d}</a></li>' for d in ('d1', 'd2'))
    if delay is None:
        return f'<html><body><main><ul class="depts">{static}<li><a href="/dept/d3">系d3</a></li></ul></main></body></html>'
    late = ("var li=document.createElement('li');li.innerHTML='<a href=\"/dept/d3\">系d3</a>';"
            "document.querySelector('ul.depts').appendChild(li);")
    script = (f"<script>document.addEventListener('DOMContentLoaded',function(){{"
              f"setTimeout(function(){{{late}}},{delay});}});</script>")
    return f'<html><body><main><ul class="depts">{static}</ul></main>{script}</body></html>'


def make_route(delay):
    async def route(request):
        path = unquote(request.request.url.removeprefix(ROOT))
        if path == 'nav':
            body = nav(delay)
        elif path.startswith('dept/'):
            body = '<html><body><main><ul class="t">' + ''.join(
                f'<li><a href="/person/{n}">{n}</a> 教授</li>' for n in DEPTS[path.split('/')[-1]]) + '</ul></main></body></html>'
        elif path.startswith('person/'):
            n = path.split('/')[-1]
            body = f'<html><body><article><h1>{n}</h1><p>职称：教授</p><p>{n}，从事研究。</p></article></body></html>'
        else:
            await request.abort()
            return
        await request.fulfill(body=body, content_type='text/html; charset=utf-8')
    return route


PLAN = {'school': '样本大学', 'college': '样本学院', 'branches': [{
    'url': ROOT + 'nav', 'kind': 'navigation', 'child_link_selector': 'ul.depts a',
    'child_branch': {'card_selector': 'ul.t > li', 'single_page': True}}]}


async def harvest(tmp_path, delay):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await new_browser_context(browser)
        await ctx.raw_context.route('**/*', make_route(delay))
        report = await run_plan(PLAN, ctx, tmp_path / 't' / 'attempt-1', state=HarvestState(tmp_path / 't', PLAN))
        await browser.close()
    return report


@pytest.mark.asyncio
async def test_control_server_rendered_children(tmp_path):
    report = await harvest(tmp_path, None)
    assert report['branches'][0]['discovered_children'] == 3 and report['total'] == 3


@pytest.mark.asyncio
async def test_late_child_is_silently_missing(tmp_path):
    report = await harvest(tmp_path, 300)
    assert report['branches'][0]['discovered_children'] == 2
    assert report['delivery_ready'] is True and report['total'] == 2 and not report['unresolved']
