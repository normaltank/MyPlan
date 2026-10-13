"""An archived graduate-advisor roster must pass the real source tool and gate."""
import hashlib
import json
from pathlib import Path

import pytest

from server.source_qualification import SOURCE_FIELDS, captured_source_evidence, witness

SHA = '1dd4ecde1258612fb00bbd4f123901addf3b7d0d97cc4be9595f6bd6ce89ad39'
RAW = Path(__file__).parent / 'fixtures' / 'source-qualification' / (SHA + '.html')
URL = 'https://mks.imun.edu.cn/szdw/yjsds.htm'
TARGET = {'school': '内蒙古民族大学', 'college': '马克思主义学院'}
BRANCH = {'url': URL, 'source_role': 'faculty_roster', 'source_container_selector': '.w14',
          'role_evidence_selector': '.position', 'source_context_selector': '.logo, .nav, .footer'}


def test_saved_graduate_advisor_label_is_a_complete_bound_faculty_purpose():
    raw = RAW.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SHA
    result = witness(raw.decode('utf8'), URL, BRANCH, TARGET)
    assert result['title_bound'] and result['college_bound'] and result['role_supported']
    # A parser observation cannot manufacture the browser proof needed downstream.
    assert captured_source_evidence(raw.decode('utf8'), URL, BRANCH, TARGET).status == 'unknown'


@pytest.mark.browser
@pytest.mark.asyncio
async def test_real_graduate_advisor_original_passes_browser_and_submit(tmp_path):
    from playwright.async_api import async_playwright
    from server.recon_browser_mcp import BrowserScout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(service_workers='block')
        await context.route('**/*', lambda route: route.fulfill(
            body=RAW.read_bytes() if route.request.url == URL else b'',
            content_type='text/html; charset=utf-8' if route.request.url == URL else 'text/plain'))
        page = await context.new_page()
        scout = BrowserScout(page, tmp_path / 'proof', 'discovery', {**TARGET, 'url': URL})
        await page.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf8'))
        await scout.call('browser_navigate', {'url': URL})
        response = await scout.call('browser_verify_source', {key: BRANCH[key] for key in SOURCE_FIELDS})
        proof = json.loads(response['content'][0]['text'])
        decision = json.loads((await scout.call('submit_plan', {
            'plan': {**TARGET, 'branches': [BRANCH]}}))['content'][0]['text'])
        assert proof['college_bound'] and proof['dom_stable'] and proof['network_idle']
        assert proof['witness_visible'] and proof['role_supported'] and proof['confirmed']
        assert decision['accepted'], decision
        original = Path(proof['original_path']).read_text(encoding='utf8')
        assert captured_source_evidence(original, URL, BRANCH, TARGET, proof).status == 'verified'
        altered = {**BRANCH, 'role_evidence_selector': '.nav'}
        assert captured_source_evidence(original, URL, altered, TARGET, proof).status == 'unknown'
        assert captured_source_evidence(original, URL, BRANCH,
            {**TARGET, 'college': '外国语学院'}, proof).status == 'unknown'
        await browser.close()


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('label', [
    '研究生导师招生通知', '研究生导师讲座新闻', '学院简介', '现任领导', '委员会成员', '教授',
])
async def test_similar_labels_and_faculty_sidebar_cannot_qualify_a_source(tmp_path, label):
    from playwright.async_api import async_playwright
    from server.recon_browser_mcp import BrowserScout

    html = (f'<title>{label}-内蒙古民族大学马克思主义学院</title>'
            '<header>内蒙古民族大学马克思主义学院</header>'
            f'<h1>{label}</h1><main><ul><li>官方人员名单</li></ul></main>'
            '<aside><a href="/faculty">师资队伍</a><p>研究生导师</p></aside>')
    branch = {**BRANCH, 'source_container_selector': 'main', 'role_evidence_selector': 'h1',
              'source_context_selector': 'header'}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        scout = BrowserScout(page, tmp_path / 'proof', 'discovery', {**TARGET, 'url': URL})
        await page.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf8'))
        await scout.call('browser_navigate', {'url': URL})
        proof = json.loads((await scout.call('browser_verify_source', {
            key: branch[key] for key in SOURCE_FIELDS}))['content'][0]['text'])
        decision = json.loads((await scout.call('submit_plan', {
            'plan': {**TARGET, 'branches': [branch]}}))['content'][0]['text'])
        assert proof['college_bound'] and proof['title_bound'] and proof['dom_stable']
        assert not proof['role_supported'] and not proof['confirmed'] and not decision['accepted']
        await browser.close()
