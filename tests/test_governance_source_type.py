"""A governance directory needs its own bound title and actual office labels."""
import pytest
import hashlib
import json
from pathlib import Path
from playwright.async_api import async_playwright

from server.source_qualification import witness
from server.browser_batch_tool import run_plan
from server.recon_browser_mcp import BrowserScout


TARGET = {'school': '样本大学', 'college': '样本学院'}
URL = 'https://example.edu/organization'
CLAIM = {'source_role': 'organizational_membership', 'source_container_selector': 'main',
         'role_evidence_selector': '#source-title', 'source_context_selector': 'footer'}


@pytest.mark.parametrize('title,body,expected', [
    ('组织建设', '<h2>学院党委</h2><p>党委书记：王芳</p>', True),
    ('组织建设', '<h2>支部设置</h2><p>教工党支部：李 华</p>', True),
    ('组织建设', '<p>组织建设活动说明，尚无组织任职名录。</p>', False),
    ('组织建设工作总结', '<p>党委书记：组织任职原文</p>', False),
    ('组织建设', '<h1>教师名录</h1><p>党委书记：组织任职原文</p>', False),
    ('组织建设', '<p>本页仅介绍岗位职责，不公布任职名单。</p><p>党委书记：负责主持党委会议</p>', False),
    ('组织建设', '<p>本页不设党委书记：不列人员，仅介绍政策。</p>', False),
])
def test_governance_directory_purpose_never_follows_an_unbound_keyword(title, body, expected):
    html = f'<title>{title}</title><h1 id="source-title">{title}</h1><main>{body}</main><footer>样本大学样本学院</footer>'
    proof = witness(html, URL, CLAIM, TARGET)
    assert proof['non_faculty_purpose'] is expected
    # The same page does not become an affirmative teacher roster.
    if title != '教师名录':
        faculty = witness(html, URL, {**CLAIM, 'source_role': 'faculty_roster'}, TARGET)
        assert faculty['role_supported'] is False


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('body,accepted', [
    ('<h2>学院党委</h2><p>党委书记：王芳</p>', True),
    ('<p>组织建设活动说明，尚无组织任职名录。</p>', False),
    ('<p>党委书记：负责主持党委会议</p>', False),
    ('<p>本页不设党委书记：不列人员，仅介绍政策。</p>', False),
])
async def test_governance_type_uses_actual_source_submission_and_independent_capture(tmp_path, body, accepted):
    html = ('<meta charset="utf-8"><title>组织建设</title><h1 id="source-title">组织建设</h1>'
            f'<main>{body}</main><footer>Copyright 样本大学样本学院</footer>')
    item = {'url': URL, 'reason': 'not_faculty_roster', **CLAIM}
    plan = {**TARGET, 'branches': [], 'source_dispositions': [item]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf-8'))
        await context.route('**/*', lambda route: route.fulfill(body=html, content_type='text/html; charset=utf-8'))
        scout = BrowserScout(await context.new_page(), tmp_path / 'scout', 'discovery',
                             {**TARGET, 'url': URL, 'required_urls': [URL]})
        await scout.call('browser_navigate', {'url': URL})
        proof = json.loads((await scout.call('browser_verify_source', CLAIM))['content'][0]['text'])
        submission = json.loads((await scout.call('submit_plan', {'plan': plan}))['content'][0]['text'])
        captured = await run_plan(plan, context, tmp_path / 'capture')
        await browser.close()
    assert proof['non_faculty_purpose'] is accepted
    assert submission['accepted'] is accepted
    source = captured['source_disposition_evidence'][0]
    assert source['status'] == ('inactive' if accepted else 'unverified')
    assert hashlib.sha256(Path(source['original_path']).read_bytes()).hexdigest() == source['source_html_sha256']
    assert captured['delivery_ready'] is False  # No source is not a no-faculty verdict.
    assert 'no_active_roster_sources' in {x['status'] for x in captured['unresolved']}
