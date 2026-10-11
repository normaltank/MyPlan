"""Synthetic new shape through real routed browser, review, queue and workbook.

No model, live host, production database or independently known college census.
"""
import json
from pathlib import Path

import openpyxl
import pytest
from playwright.async_api import async_playwright

from server import identity_review
from server.agent_queue import AgentTaskQueue
from server.browser_batch_tool import run_plan
from server.recon_browser_mcp import BrowserScout


@pytest.mark.browser
@pytest.mark.asyncio
async def test_new_rank_grid_retains_identity_decision_sources_and_all_export_fields(tmp_path):
    root = 'https://fixture.example.edu/ranks'
    people = [('a', '王芳', '教授', '代数研究与完整任职履历。'),
              ('b', '李华', '教授', '分析研究与完整任职履历。'),
              ('c', '王芳', '副教授', '几何研究与完整任职履历。'),
              ('d', '刘敏', '副教授', '拓扑研究与完整任职履历。')]
    roster = ('<meta charset="utf-8"><title>教师名录</title><header>样本大学 样本学院</header>'
              '<h1>教师名录</h1><main><section class="rankgroup"><h2>教授</h2>'
              '<div class="names"><a href="/p/a">王芳</a>、<a href="/p/b">李华</a></div></section>'
              '<section class="rankgroup"><h2>副教授</h2><div class="names">'
              '<a href="/p/c">王芳</a>、<a href="/p/d">刘敏</a></div></section></main>')
    pages = {root: roster, **{f'https://fixture.example.edu/p/{key}':
        f'<article><h1>{name}</h1><p>职称：{rank}</p><p>工作单位：样本大学样本学院</p>'
        f'<p>{bio}</p></article>' for key, name, rank, bio in people}}
    branch = {'url': root, 'single_page': True,
        'source_role': 'faculty_roster', 'source_container_selector': 'main',
        'role_evidence_selector': 'h1', 'source_context_selector': 'header',
        'profile_content_selector': 'article',
        'page_types': {'roster': {'type_id': 'roster.rank_grid', 'implementation_version': 1,
            'params': {'container_selector': '.rankgroup', 'record_start_selector': 'h2',
                       'card_selector': '.names'}}}}
    plan = {'school': '样本大学', 'college': '样本学院', 'branches': [branch]}
    queue = AgentTaskQueue(tmp_path / 'queue.db', legacy_json_path=None)
    task = queue.submit_task(plan['school'], plan['college'], root)
    claim = queue.claim_task('offline-type-worker', task_id=task.task_id)
    out = tmp_path / 'outputs' / 'tasks' / task.task_id / 'attempt-1'
    requests = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(service_workers='block')
        async def serve(route):
            requests.append(route.request.url)
            body = pages.get(route.request.url)
            await route.fulfill(status=200 if body is not None else 404,
                                content_type='text/html; charset=utf-8', body=body or '')
        await context.route('**/*', serve)
        try:
            await context.add_init_script(Path('server/recon_dom_probe.js').read_text())
            scout_page = await context.new_page()
            scout = BrowserScout(scout_page, tmp_path / 'scout', 'branch',
                                 {'school': plan['school'], 'college': plan['college'], 'url': root})
            catalog_reply = await scout.call('page_type_catalog', {'type_id': 'roster.rank_grid'})
            available = json.loads(catalog_reply['content'][0]['text'])
            assert scout_page.url == 'about:blank' and not requests
            assert any(item['type_id'] == 'roster.rank_grid' and item['implementation_version'] == 1
                       for item in available['items'])
            await scout.call('browser_navigate', {'url': root})
            await scout.call('browser_verify_source', {
                'source_role': branch['source_role'],
                'source_container_selector': branch['source_container_selector'],
                'role_evidence_selector': branch['role_evidence_selector'],
                'source_context_selector': branch['source_context_selector'], 'unit_selector': '.names'})
            verified = await scout.call('browser_verify', {'card_selector': '.names',
                'container_selector': '.rankgroup', 'record_start_selector': 'h2',
                'page_types': branch['page_types']})
            probe = json.loads(verified['content'][0]['text'])
            samples = probe.get('linkedSamples', probe.get('profileSamples', []))[:2]
            assert len(samples) == 2
            await scout.call('browser_verify_profile', {'profile_content_selector': 'article', 'urls': samples})
            submitted = await scout.call('submit_plan', {'plan': branch})
            acceptance = json.loads(submitted['content'][0]['text'])
            assert acceptance['accepted'], acceptance.get('issues')
            await scout_page.close()
            first = await run_plan(plan, context, out, task_id=task.task_id)
            assert first['candidate_count'] == 4
            assert not first['delivery_ready']
            [item] = first['identity_review_items']
            decisions = out / identity_review.DECISIONS_FILE
            identity_review.save_decision(decisions, item, 'distinct', reviewed_by=7)
            before = decisions.read_bytes()
            report = await run_plan(plan, context, out, task_id=task.task_id)
            assert decisions.read_bytes() == before
            assert report['delivery_ready'] and report['coverage']['complete']
            rows = json.loads(Path(report['records_path']).read_text())
            assert len(rows) == 4
            expected = {f'https://fixture.example.edu/p/{key}': (name, rank, bio)
                        for key, name, rank, bio in people}
            for row in rows:
                name, rank, bio = expected[row['主页']]
                assert (row['姓名'], row['职称'], row['学校'], row['学院'], row['邮箱']) == (
                    name, rank, plan['school'], plan['college'], '')
                assert bio in row['简介'] and '工作单位：样本大学样本学院' in row['简介']
            assert all(item['source_evidence'][0]['status'] == 'verified' for item in report['eligibility'])
            assert all(item['receipt_sha256'] for item in report['coverage']['field_sources'])
            queue.complete_task(task.task_id, rows, report, worker_id='offline-type-worker', lease_token=claim.lease_token)
            snapshot = queue.get_university_snapshot(plan['school'])
            assert len(snapshot['records']) == 4 and not snapshot['identity_conflicts']
            assert decisions.read_bytes() == before
            workbook = openpyxl.load_workbook(report['excel_path'], read_only=True)
            try:
                exported = []
                for sheet in workbook:
                    cells = list(sheet.values)
                    labels = cells[0]
                    exported.extend({label: value or '' for label, value in zip(labels, values)}
                                    for values in cells[1:])
                fields = ('姓名', '职称', '邮箱', '学校', '学院', '主页', '简介')
                assert sorted(tuple(row[field] for field in fields) for row in exported) == sorted(
                    tuple(row[field] for field in fields) for row in rows)
            finally:
                workbook.close()
            assert set(requests) <= set(pages)
        finally:
            await browser.close()
