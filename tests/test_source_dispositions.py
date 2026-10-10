"""Source retirement/pending evidence across submit, staged and actual capture."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from server import agent_recon
from server.browser_batch_tool import _validate_plan, run_plan
from server.recon_browser_mcp import BrowserScout
from server.recon_contract import validate_submission
from server.recon_plan_cache import _validate_structure
from server.source_qualification import SOURCE_FIELDS, witness

TARGET = {'school': '样本大学', 'college': '样本学院', 'url': 'https://example.edu/'}
URL = 'https://example.edu/people'
FIELDS = {'source_role': 'organizational_membership', 'source_container_selector': 'main',
          'role_evidence_selector': 'h1', 'source_context_selector': 'header'}


def plan(item):
    return {**{key: TARGET[key] for key in ('school', 'college')},
            'branches': [], 'source_dispositions': [item]}


@pytest.mark.parametrize('payload', [
    {key: value for key, value in {**TARGET, 'branches': []}.items() if key != 'url'},
    plan({'url': URL, 'reason': 'source_unreadable', 'teachers': [{'name': '王芳'}]}),
    plan({'url': URL, 'reason': 'not_faculty_roster'}),
    plan({'url': URL, 'reason': 'source_structure_unresolved', 'confirmed': True}),
    plan({'url': 'https://user:pass@example.edu/', 'reason': 'source_unreadable'}),
])
def test_empty_or_model_permission_cannot_settle_sources(payload):
    assert validate_submission(payload, 'discovery', TARGET, [])
    with pytest.raises(ValueError):
        _validate_plan(payload)


@pytest.mark.parametrize('title,context,role,expected', [
    ('学院领导', '样本大学样本学院', 'organizational_membership', True),
    ('教师名录', '样本大学样本学院', 'organizational_membership', False),
    ('人员展示', '样本大学样本学院', 'organizational_membership', False),
    ('学院领导', '样本大学其它学院', 'organizational_membership', False),
    ('学院领导', '样本大学样本学院', 'unverified_people_source', False),
])
def test_inactive_requires_affirmative_current_organizational_purpose(title, context, role, expected):
    html = f'<title>{title}</title><header>{context}</header><h1>{title}</h1><main>王芳教授</main>'
    proof = witness(html, URL, {**FIELDS, 'source_role': role}, TARGET)
    assert proof['non_faculty_purpose'] is expected


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('reason,title,status,current_title', [
    ('not_faculty_roster', '学院领导', 200, None),
    ('not_faculty_roster', '教师名录', 200, None),
    ('source_relationship_unverified', '人员展示', 200, None),
    ('source_structure_unresolved', '人员展示', 200, None),
    ('source_unreadable', '暂不可访问', 503, None),
    ('not_faculty_roster', '学院领导', 200, '教师名录'),
])
async def test_actual_original_submit_staged_and_capture_closure(tmp_path, monkeypatch, reason, title, status, current_title):
    html = (f'<title>{title}</title><header>样本大学样本学院</header><h1>{title}</h1>'
            '<main><p>正式官网当前页面的人员与栏目原文，姓名和职务仍为历史事实。</p>'
            '<p>' + '本页面列出的真实组织成员和说明原文。' * 12 + '</p></main>')
    item = {'url': URL, 'reason': reason}
    if reason in {'not_faculty_roster', 'source_relationship_unverified'}:
        item.update(FIELDS)
        if reason == 'source_relationship_unverified':
            item['source_role'] = 'unverified_people_source'
    value = plan(item)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf8'))
        await context.route('**/*', lambda route: route.fulfill(status=status, content_type='text/html; charset=utf-8', body=html))
        page = await context.new_page()
        scout = BrowserScout(page, tmp_path / 'scout', 'discovery', {**TARGET, 'required_urls': [URL]})
        navigation = json.loads((await scout.call('browser_navigate', {'url': URL}))['content'][0]['text'])
        old_original = Path(navigation['original_path']).read_bytes()
        if 'source_role' in item:
            await scout.call('browser_verify_source', {key: item[key] for key in SOURCE_FIELDS})
        reply = await scout.call('submit_plan', {'plan': value})
        result = json.loads(reply['content'][0]['text'])
        should_accept = not (reason == 'not_faculty_roster' and title == '教师名录')
        assert result['accepted'] is should_accept
        if not should_accept:
            assert {i['status'] for i in result['issues']} == {'source_disposition_unverified'}
            await browser.close()
            return
        calls = []
        def model(prompt, sid, cfg, patches):
            calls.append(cfg['phase'])
            return SimpleNamespace(final_response=json.dumps(value), finish_reason='completed', events=scout.events)
        monkeypatch.setattr(agent_recon, '_execute_harness', model)
        staged = agent_recon._run_staged_harness('pending', {'target': TARGET, 'timeout_seconds': 30,
                                                'feedback': {'known_urls': [URL]}}, ())
        assert json.loads(staged.final_response) == value
        assert calls == ['discovery']
        assert validate_submission(value, 'discovery', {**TARGET, 'required_urls': [URL]}, scout.events) == []
        omitted = validate_submission({**value, 'source_dispositions': []}, 'discovery', TARGET, scout.events)
        assert omitted
        omitted = validate_submission(
            {**value, 'source_dispositions': [{'url': TARGET['url'], 'reason': 'source_structure_unresolved'}]},
            'discovery', {**TARGET, 'required_urls': [URL]}, scout.events)
        assert 'previous_branch_omitted' in {i['status'] for i in omitted}
        if current_title:
            html = html.replace(title, current_title)
        report = await run_plan(value, context, tmp_path / 'capture')
        assert Path(navigation['original_path']).read_bytes() == old_original
        await browser.close()
    assert report['delivery_ready'] is False
    assert 'no_active_roster_sources' in {i['status'] for i in report['unresolved']}
    audit = json.loads(Path(report['audit_path']).read_text(encoding='utf8'))
    evidence = audit['source_disposition_evidence'][0]
    assert hashlib.sha256(Path(evidence['navigation_evidence_path']).read_bytes()).hexdigest() == evidence['navigation_evidence_sha256']
    raw = Path(evidence['original_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == evidence['source_html_sha256']
    assert '真实组织成员' in raw.decode('utf8')
    assert evidence['status'] == ('unverified' if current_title else
                                  'inactive' if reason == 'not_faculty_roster' else
                                  'unavailable' if reason == 'source_unreadable' else 'pending')
    if reason == 'not_faculty_roster':
        assert evidence['browser_proof']['non_faculty_purpose'] is (not bool(current_title))
    if current_title:
        assert 'source_disposition_unverified' in {i['status'] for i in report['unresolved']}
    with pytest.raises(ValueError, match='fresh discovery'):
        _validate_structure(value, TARGET['url'], [])


@pytest.mark.browser
@pytest.mark.asyncio
async def test_saved_wxc_committees_retire_only_with_actual_article_heading(tmp_path):
    from test_source_role_qualification import CASES, FIXTURES, PLAN, claim
    from server.source_qualification import capture_source_dispositions
    originals = {url: (FIXTURES / f'{sha}.html').read_bytes() for url, sha, _, _ in CASES[:2]}
    items = []
    for case in CASES[:2]:
        _, branch = claim(*case)
        items.append({key: branch[key] for key in ('url', *SOURCE_FIELDS)})
        # The old breadcrumb names only "机构设置". The current actual article
        # heading, not an unrelated menu or absent faculty keyword, proves role.
        items[-1].update(reason='not_faculty_roster', role_evidence_selector='.Article_Title')
    value = {**PLAN, 'branches': [], 'source_dispositions': items}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        async def serve(route):
            raw = originals.get(route.request.url)
            await route.fulfill(status=200 if raw else 404, content_type='text/html', body=raw or b'')
        await context.route('**/*', serve)
        issues, evidence = await capture_source_dispositions(context, value, tmp_path)
        await browser.close()
    assert issues == [{'status': 'no_active_roster_sources'}]
    assert len(evidence) == 2 and all(item['status'] == 'inactive' for item in evidence)
    for item in evidence:
        assert item['browser_proof']['title_bound'] is True
        assert item['browser_proof']['college_bound'] is True
        assert hashlib.sha256(Path(item['original_path']).read_bytes()).hexdigest() == item['source_html_sha256']
    for url, sha, _, _ in CASES[:2]:
        assert (FIXTURES / f'{sha}.html').read_bytes() == originals[url]
