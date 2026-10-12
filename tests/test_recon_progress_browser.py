"""Actual source receipts distinguish changed material from repeated copies."""
import hashlib
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from server.recon_browser_mcp import BrowserScout
from server.source_qualification import SOURCE_FIELDS


def body(reply):
    return json.loads(reply['content'][0]['text'])


@pytest.mark.browser
@pytest.mark.asyncio
async def test_real_chizhou_unknown_exclusion_feedback_and_repeated_source_receipts(tmp_path):
    raw = Path('tests/fixtures/source_disposition/chizhou-current-leaders.html').read_bytes()
    assert hashlib.sha256(raw).hexdigest() == 'ffd913c28f2f7529a89436b22fdc5ef2edad656651833df81b3325a5cd61f653'
    url = 'http://my.czu.edu.cn/xygk/xrld.htm'
    target = {'school': '池州学院', 'college': '马克思主义学院', 'url': url}
    item = {'url': url, 'reason': 'not_faculty_roster', 'source_role': 'organizational_membership',
            'source_container_selector': '.list-main-right', 'role_evidence_selector': '.lm_name',
            'source_context_selector': '.logo, .footer'}
    plan = {key: target[key] for key in ('school', 'college')}
    plan.update(branches=[], source_dispositions=[item])
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context()
            await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf8'))
            await context.route('**/*', lambda route: route.fulfill(
                content_type='text/html; charset=utf-8' if route.request.url == url else 'text/plain',
                body=raw if route.request.url == url else b''))
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path, 'discovery', target)
            await scout.call('browser_navigate', {'url': url})
            for _ in range(3):
                proof = body(await scout.call('browser_verify_source', {key: item[key] for key in SOURCE_FIELDS}))
                assert proof['confirmed'] is True and proof['non_faculty_purpose'] is False
                reply = body(await scout.call('submit_plan', {'plan': plan}))
                assert reply['accepted'] is False
                issue, = reply['issues']
                assert issue['failed_condition'] == 'non_faculty_purpose_unverified'
            assert reply['stop'] == 'recon_gate_stalled'
        finally:
            await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_actual_detail_stage_uses_verified_article_instead_of_full_document(tmp_path):
    url, profile_url = 'https://progress.example.edu/list', 'https://progress.example.edu/person'
    target = {'school': '样本大学', 'college': '样本学院', 'url': url}
    branch = {'url': url, 'card_selector': 'main li', 'single_page': True,
        'profile_content_selector': 'article', 'source_role': 'faculty_roster',
        'source_container_selector': 'main', 'role_evidence_selector': 'h1', 'source_context_selector': 'header',
        'page_types': {'detail': {'type_id': 'detail.article_region', 'implementation_version': 1,
                                'params': {'profile_content_selector': 'article'}}}}
    page_html = {'value': '<article><p>完整的可见个人正文。</p></article><div id="clock">0</div>'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context()
            await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf8'))
            await context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=page_html['value']))
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path, 'branch', target)
            for i in range(3):
                page_html['value'] = f'<article><p>完整的可见个人正文。</p></article><div id="clock">{i}</div>'
                packet = body(await scout.call('browser_verify_stage', {
                    'role': 'detail', 'branch': branch, 'urls': [profile_url]}))
                assert packet['results'][0]['stage_evidence']['payload']['confirmed'] is True
                reply = body(await scout.call('submit_plan', {'plan': branch}))
            assert reply['stop'] == 'recon_gate_stalled'
            page_html['value'] = '<article><p>完整的可见个人正文，新增研究材料。</p></article><div id="clock">3</div>'
            await scout.call('browser_verify_stage', {'role': 'detail', 'branch': branch, 'urls': [profile_url]})
            reply = body(await scout.call('submit_plan', {'plan': branch}))
            assert 'unchanged_resubmission' not in reply and 'stop' not in reply
            # A corrupted original cannot be normalized as a verified article.
            original = Path(packet['results'][0]['originals'][0]['evidence_path'])
            from server.recon_progress import observation_fingerprint
            before = observation_fingerprint(scout.events, reply['issues'])
            original.write_bytes(original.read_bytes() + b'changed')
            assert observation_fingerprint(scout.events, reply['issues']) != before
        finally:
            await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
async def test_actual_source_clock_is_not_progress_but_changed_scope_is(tmp_path):
    url = 'https://progress.example.edu/source'
    target = {'school': '样本大学', 'college': '样本学院', 'url': url}
    item = {'url': url, 'reason': 'not_faculty_roster', 'source_role': 'organizational_membership',
            'source_container_selector': 'main', 'role_evidence_selector': 'h1',
            'source_context_selector': 'header'}
    plan = {'school': target['school'], 'college': target['college'],
            'branches': [], 'source_dispositions': [item]}
    html = ('<title>人员展示</title><header>样本大学样本学院</header><h1>人员展示</h1>'
            '<main><p>已观察到但师资关系尚未确认的官方人员材料。</p></main><div id="clock">0</div>')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await browser.new_context()
            await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf8'))
            await context.route('**/*', lambda route: route.fulfill(content_type='text/html', body=html))
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path, 'discovery', target)
            await scout.call('browser_navigate', {'url': url})
            scopes, full = set(), set()
            for i in range(3):
                await page.evaluate('i => document.querySelector("#clock").textContent = String(i)', i)
                proof = body(await scout.call('browser_verify_source', {key: item[key] for key in SOURCE_FIELDS}))
                assert proof['confirmed'] is True
                scopes.add(proof['selected_scope_sha256'])
                full.add(proof['proof_sha256'])
                reply = body(await scout.call('submit_plan', {'plan': plan}))
            assert len(scopes) == 1 and len(full) == 3
            assert reply['stop'] == 'recon_gate_stalled'
            await page.evaluate('() => document.querySelector("main p").textContent += " 新的相关人员材料。"')
            await scout.call('browser_verify_source', {key: item[key] for key in SOURCE_FIELDS})
            reply = body(await scout.call('submit_plan', {'plan': plan}))
            assert reply['accepted'] is False and 'stop' not in reply and 'unchanged_resubmission' not in reply
        finally:
            await browser.close()
