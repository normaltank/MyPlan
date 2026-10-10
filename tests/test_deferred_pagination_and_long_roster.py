"""Worker-style captures of rosters whose pager or department links render late,
and of rosters longer than 20 pages, with synthetic pages only.

Regressions for audit findings A2 and A1 (docs/audits/2026-10-01-production-chain-audit.md).
"""
from urllib.parse import unquote

import pytest
from playwright.async_api import async_playwright

from server.browser_batch_tool import run_plan
from server.browser_context import new_browser_context
from server.harvest_state import HarvestState

pytestmark = pytest.mark.browser

ROOT = 'https://fixture.edu/'


def profile(name):
    return (f'<html><body><header>学院导航</header><article><h1>{name}</h1><p>职称：教授</p>'
            f'<p>{name}，长期从事材料研究。</p></article><footer>版权</footer></body></html>')


async def harvest(tmp_path, plan, bodies):
    async def route(request):
        body = bodies(unquote(request.request.url.removeprefix(ROOT)))
        if body is None:
            await request.abort()
        else:
            await request.fulfill(body=body, content_type='text/html; charset=utf-8')

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await new_browser_context(browser)
        await ctx.raw_context.route('**/*', route)
        report = await run_plan(plan, ctx, tmp_path / 'task' / 'attempt-1', state=HarvestState(tmp_path / 'task', plan))
        await browser.close()
    return report


def later(script, delay_ms):
    return (f"<script>document.addEventListener('DOMContentLoaded',function(){{"
            f"setTimeout(function(){{{script}}},{delay_ms});}});</script>")


PAGER_PEOPLE = {1: ['王建国', '李晓明'], 2: ['张伟东', '刘海涛']}
PAGER_PLAN = {'school': '样本大学', 'college': '样本学院', 'branches': [
    {'url': ROOT + 'list/1', 'card_selector': 'ul.t > li', 'next_selector': '.pager a.next'}]}


def pager_site(delay_ms):
    def bodies(path):
        if path.startswith('list/'):
            number = int(path.split('/')[-1])
            cards = ''.join(f'<li><a href="/person/{n}">{n}</a> 教授</li>' for n in PAGER_PEOPLE[number])
            pager = "<div class='pager'></div>"
            if number == 1 and delay_ms is not None:
                pager += later("var d=document.createElement('a');d.className='next';d.href='/list/2';"
                               "d.textContent='下一页';document.querySelector('.pager').appendChild(d);", delay_ms)
            return f'<html><body><main><ul class="t">{cards}</ul>{pager}</main></body></html>'
        if path.startswith('person/'):
            return profile(path.split('/')[-1])
        return None
    return bodies


@pytest.mark.asyncio
async def test_pager_rendered_after_domcontentloaded_is_followed(tmp_path):
    report = await harvest(tmp_path, PAGER_PLAN, pager_site(400))
    assert report['directory_pages'] == 2 and report['total'] == 4
    assert report['branches'][0]['end'] == 'next_control_absent'
    assert report['delivery_ready'] is True


@pytest.mark.asyncio
async def test_entry_page_without_verified_pager_is_not_proof_of_completion(tmp_path):
    # The recon saw one next control on the entry page; if it never shows up the
    # roster must go to structure repair, not be delivered as complete.
    report = await harvest(tmp_path, PAGER_PLAN, pager_site(None))
    statuses = [item.get('status') for item in report['unresolved']]
    assert 'pagination_not_verified' in statuses
    assert report['branches'][0]['end'] != 'next_control_absent'
    assert report['coverage']['complete'] is False and report['delivery_ready'] is False


DEPTS = {'d1': ['王建国'], 'd2': ['李晓明'], 'd3': ['张伟东']}
NAV_PLAN = {'school': '样本大学', 'college': '样本学院', 'branches': [{
    'url': ROOT + 'nav', 'kind': 'navigation', 'child_link_selector': 'ul.depts a',
    'child_branch': {'card_selector': 'ul.t > li', 'single_page': True}}]}


def nav_site(path):
    if path == 'nav':
        static = ''.join(f'<li><a href="/dept/{d}">系{d}</a></li>' for d in ('d1', 'd2'))
        script = later("var li=document.createElement('li');li.innerHTML='<a href=\"/dept/d3\">系d3</a>';"
                       "document.querySelector('ul.depts').appendChild(li);", 300)
        return f'<html><body><main><ul class="depts">{static}</ul></main>{script}</body></html>'
    if path.startswith('dept/'):
        return '<html><body><main><ul class="t">' + ''.join(
            f'<li><a href="/person/{n}">{n}</a> 教授</li>' for n in DEPTS[path.split('/')[-1]]) + '</ul></main></body></html>'
    if path.startswith('person/'):
        return profile(path.split('/')[-1])
    return None


@pytest.mark.asyncio
async def test_department_link_rendered_after_domcontentloaded_is_discovered(tmp_path):
    report = await harvest(tmp_path, NAV_PLAN, nav_site)
    assert report['branches'][0]['discovered_children'] == 3
    assert report['total'] == 3 and report['delivery_ready'] is True


SURNAMES = '王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁'
LONG_PLAN = {'school': '样本大学', 'college': '样本学院',
             'branches': [{'url': ROOT + 'list/1', 'card_selector': 'main li', 'next_selector': '.next'}]}


def long_site(path):
    if path.startswith('list/'):
        number = int(path.split('/')[-1])
        nxt = f'<a class="next" href="/list/{number + 1}">下一页</a>' if number < 22 else ''
        name = SURNAMES[number - 1] + '建国'
        return f'<html><body><main><ul><li><a href="/person/{number}">{name}</a> 教授</li></ul>{nxt}</main></body></html>'
    if path.startswith('person/'):
        return profile(SURNAMES[int(path.split('/')[-1]) - 1] + '建国')
    return None


@pytest.mark.asyncio
async def test_roster_longer_than_twenty_pages_completes_under_worker_checkpoint(tmp_path):
    report = await harvest(tmp_path, LONG_PLAN, long_site)
    assert report['directory_pages'] == 22 and report['total'] == 22
    assert report['branches'][0]['end'] == 'next_control_absent'
    assert not report['unresolved'] and report['delivery_ready'] is True

# This component fixture stipulates verified source purpose; purpose integration is unmocked elsewhere.
pytestmark = pytest.mark.verified_source_boundary
