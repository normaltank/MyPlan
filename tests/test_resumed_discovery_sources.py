"""Native discovery events must survive reuse into the final source gate."""
import copy
import hashlib
import json

import pytest
from playwright.async_api import async_playwright

from server import agent_recon
from server.browser_context import new_browser_context
from server.recon_browser_mcp import BrowserScout
from server.recon_contract import disposition_event_issues
from server.source_qualification import SOURCE_FIELDS

TARGET = {'school': '样本大学', 'college': '样本学院', 'url': 'https://fixture.example.edu/'}
CLAIM = {'source_role': 'organizational_membership', 'source_container_selector': 'main',
         'role_evidence_selector': 'h1', 'source_context_selector': 'footer'}


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('change', [None, 'source', 'target'])
async def test_resumed_discovery_retains_four_original_proofs_and_current_rejections(tmp_path, monkeypatch, change):
    urls = [TARGET['url'] + f'organization/{i}' for i in range(4)]
    html = ('<meta charset="utf-8"><title>学院领导</title><h1>学院领导</h1>'
            '<main><p>实际组织成员名单原文。来源类型不提供教师任职许可。</p></main>'
            '<footer>样本大学样本学院</footer>')
    pages = {url: html for url in urls}
    plan = {'school': TARGET['school'], 'college': TARGET['college'], 'branches': [],
            'source_dispositions': [{'url': url, 'reason': 'not_faculty_roster', **CLAIM} for url in urls]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await new_browser_context(browser, polite=False)
        await context.route('**/*', lambda route: route.fulfill(
            body=pages.get(route.request.url, ''), content_type='text/html; charset=utf-8'))
        scout = BrowserScout(await context.new_page(), tmp_path / 'discovery', 'discovery', TARGET)
        for item in plan['source_dispositions']:
            await scout.call('browser_navigate', {'url': item['url']})
            await scout.call('browser_verify_source', {key: item[key] for key in SOURCE_FIELDS})
        assert disposition_event_issues(plan, scout.events) == []
        original_files = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (tmp_path / 'discovery').glob('*.html')}
        if change == 'source':
            pages[urls[0]] = html.replace('样本大学样本学院', '样本大学另一学院')
            await scout.call('browser_navigate', {'url': urls[0]})
            await scout.call('browser_verify_source', CLAIM)
        await browser.close()
    target = {**TARGET, 'college': '另一学院'} if change == 'target' else TARGET
    checkpoint_events = copy.deepcopy(scout.events)
    checkpoint = {'plan': plan, 'events': checkpoint_events, 'session_id': 'original-discover'}
    checkpoint_before = copy.deepcopy(checkpoint)
    monkeypatch.setattr(agent_recon, '_execute_harness', lambda *a, **kw: pytest.fail('Reused discovery must make no model call'))
    cfg = {'target': target, 'timeout_seconds': 30, 'resume_discovery': checkpoint}
    if change:
        with pytest.raises(agent_recon.ReconPlanError) as raised:
            agent_recon._run_staged_harness('retry', cfg, ())
        statuses = {i['status'] for i in raised.value.safe_feedback['issues']}
        assert ('queued_target_changed' if change == 'target' else 'source_disposition_unverified') in statuses
    else:
        result = agent_recon._run_staged_harness('retry', cfg, ())
        final = json.loads(result.final_response)
        # This is the same source-disposition check used by run_recon after
        # staged composition, not only the earlier discovery validation.
        assert disposition_event_issues(final, result.events) == []
        assert result.events == checkpoint_events
        assert result.phase_metrics[0]['reused'] is True
    assert checkpoint == checkpoint_before
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == digest for p, digest in original_files.items())
