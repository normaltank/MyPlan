"""Four independently selected DOM stages through routed scout and export."""
import copy
import json
import os
from dataclasses import replace
from pathlib import Path

import openpyxl
import pytest
from playwright.async_api import async_playwright

from server import page_types
from server.browser_batch_tool import run_plan
from server.page_types import register_type, resolve_type
from server.recon_browser_mcp import BrowserScout
from server.recon_plan_cache import validate_cached_plan


@pytest.mark.browser
@pytest.mark.asyncio
async def test_four_selected_stages_and_one_registration_only_replacement(tmp_path, monkeypatch):
    monkeypatch.setattr(page_types, '_REGISTRY', dict(page_types._REGISTRY))
    host = 'https://fixture.example.edu'
    home, first, second = host + '/faculty', host + '/ranks/1', host + '/ranks/2'
    people = [('a', '王芳', '教授', '代数研究与完整任职履历。'),
              ('b', '李华', '教授', '分析研究与完整任职履历。'),
              ('c', '赵明', '副教授', '几何研究与完整任职履历。'),
              ('d', '刘敏', '副教授', '拓扑研究与完整任职履历。')]
    prefix = '<meta charset="utf-8"><title>教师名录</title><h1>教师名录</h1><header>样本大学 样本学院</header>'
    def roster(entries, next_url=None):
        groups = ''.join(f'<section class="rankgroup"><h2>{rank}</h2>'
                         f'<div class="names"><a href="/p/{key}">{name}</a></div></section>'
                         for key, name, rank, _ in entries)
        return prefix + '<main>' + groups + '</main>' + (
            f'<a class="next" href="{next_url}">下一页</a>' if next_url else '')
    pages = {home: prefix + '<main><div class="groups"><a href="/ranks/1">教师名录</a></div></main>',
             first: roster(people[:2], second), second: roster(people[2:]),
             **{host + '/p/' + key: f'<article><h1>{name}</h1><p>职称：{rank}</p>'
                f'<p>工作单位：样本大学样本学院</p><p>{bio}</p></article>'
                for key, name, rank, bio in people}}
    source = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
              'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
    leaf = {'url': first, **source, 'next_selector': 'a.next', 'max_pages': 3,
            'profile_content_selector': 'article', 'page_types': {
                'roster': {'type_id': 'roster.rank_grid', 'implementation_version': 1,
                           'params': {'container_selector': '.rankgroup',
                                      'record_start_selector': 'h2', 'card_selector': '.names'}},
                'pagination': {'type_id': 'pagination.verified_next', 'implementation_version': 1},
                'detail': {'type_id': 'detail.article_region', 'implementation_version': 1}}}
    parent = {'url': home, 'kind': 'navigation', **source, 'single_page': True,
              'child_link_selector': '.groups', 'child_branches': [leaf], 'page_types': {
                  'entrance': {'type_id': 'entrance.scoped_links', 'implementation_version': 1}}}
    plan = {'school': '样本大学', 'college': '样本学院', 'branches': [parent]}
    retained = Path(os.environ['PAGE_STAGE_EVIDENCE_DIR']) if os.getenv('PAGE_STAGE_EVIDENCE_DIR') else tmp_path
    retained.mkdir(parents=True, exist_ok=True)
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
            page = await context.new_page()
            scout = BrowserScout(page, retained / 'scout', 'branch',
                                 {'school': plan['school'], 'college': plan['college'], 'url': home})
            for type_id in ('entrance.scoped_links', 'roster.rank_grid',
                            'pagination.verified_next', 'detail.article_region'):
                reply = await scout.call('page_type_catalog', {'type_id': type_id})
                assert any(item['type_id'] == type_id for item in json.loads(reply['content'][0]['text'])['items'])
            assert page.url == 'about:blank' and requests == []
            await scout.call('browser_navigate', {'url': home})
            await scout.call('browser_verify_source', {**source, 'unit_selector': '.groups'})
            await scout.call('browser_verify_stage', {'role': 'entrance', 'branch': parent})
            await scout.call('browser_navigate', {'url': first})
            await scout.call('browser_verify_source', {**source, 'unit_selector': '.names'})
            await scout.call('browser_verify', {'card_selector': '.names', 'container_selector': '.rankgroup',
                'record_start_selector': 'h2', 'next_selector': 'a.next', 'page_types': leaf['page_types']})
            await scout.call('browser_verify_stage', {'role': 'pagination', 'branch': leaf})
            await scout.call('browser_verify_stage', {'role': 'detail', 'branch': leaf,
                                                     'urls': [host + '/p/a', host + '/p/b']})
            accepted = json.loads((await scout.call('submit_plan', {'plan': parent}))['content'][0]['text'])
            (retained / 'submission-result.json').write_text(json.dumps(accepted, ensure_ascii=False, indent=2))
            (retained / 'scout-events.json').write_text(json.dumps(scout.events, ensure_ascii=False, indent=2))
            assert accepted['accepted'], accepted.get('issues')
            report = await run_plan(plan, context, retained / 'capture', task_id='four-stages')
            assert report['delivery_ready'] and report['coverage']['complete'], report['unresolved']
            entrance_proof = report['coverage']['branches'][0]['stage_evidence']
            pagination_proof = report['coverage']['branches'][0]['child_visits'][0]['stage_evidence']
            assert entrance_proof['type_id'] == 'entrance.scoped_links'
            assert pagination_proof['type_id'] == 'pagination.verified_next'
            assert all(item['roster_page_type']['type_id'] == 'roster.rank_grid'
                       for item in report['roster_observations'])
            assert all(item['stage_evidence']['type_id'] == 'detail.article_region'
                       for item in report['profile_page_type_evidence'].values())
            revalidated = await validate_cached_plan({'plan': {**plan, 'branches': [leaf]}}, context,
                retained / 'cache-revalidation', verify_discovery=False)
            assert revalidated['valid'] and revalidated['model_calls'] == 0, revalidated
            (retained / 'cache-validation.json').write_text(json.dumps(revalidated, ensure_ascii=False, indent=2))
            rows = json.loads(Path(report['records_path']).read_text())
            assert len(rows) == 4
            fields = ('姓名', '职称', '邮箱', '学校', '学院', '主页', '简介')
            for key, name, rank, bio in people:
                row = next(row for row in rows if row['主页'] == host + '/p/' + key)
                assert (row['姓名'], row['职称'], row['邮箱'], row['学校'], row['学院']) == (
                    name, rank, '', '样本大学', '样本学院')
                assert bio in row['简介']
            workbook = openpyxl.load_workbook(report['excel_path'], read_only=True)
            try:
                exported = []
                for sheet in workbook:
                    cells = list(sheet.values)
                    exported.extend(dict(zip(cells[0], (value or '' for value in row))) for row in cells[1:])
                assert sorted(tuple(row[field] for field in fields) for row in exported) == sorted(
                    tuple(row[field] for field in fields) for row in rows)
            finally:
                workbook.close()
            original = resolve_type(leaf['page_types']['detail'], 'detail')
            calls = []
            async def alternate(operation, *args, **kwargs):
                result = await original.implementation(operation, *args, **kwargs)
                calls.append(result.meta['final_url'])
                return result
            register_type(replace(original, type_id='detail.integration_replacement', implementation=alternate))
            changed = copy.deepcopy(plan)
            changed['branches'][0]['child_branches'][0]['page_types']['detail']['type_id'] = 'detail.integration_replacement'
            borrowed = json.loads((await scout.call('submit_plan', {'plan': changed['branches'][0]}))['content'][0]['text'])
            assert not borrowed['accepted']
            await scout.call('page_type_catalog', {'type_id': 'detail.integration_replacement'})
            await scout.call('browser_verify_stage', {'role': 'detail',
                'branch': changed['branches'][0]['child_branches'][0], 'urls': [host + '/p/a', host + '/p/b']})
            refreshed = json.loads((await scout.call('submit_plan', {'plan': changed['branches'][0]}))['content'][0]['text'])
            assert refreshed['accepted'], refreshed.get('issues')
            (retained / 'replacement-scout-events.json').write_text(json.dumps(scout.events, ensure_ascii=False, indent=2))
            probe_calls = len(calls)
            assert probe_calls == 2
            await page.close()
            replaced = await run_plan(changed, context, retained / 'replacement', task_id='four-stages')
            assert replaced['delivery_ready'] and len(calls) - probe_calls == 4
            changed_rows = json.loads(Path(replaced['records_path']).read_text())
            assert sorted(tuple(row[field] for field in fields) for row in changed_rows) == sorted(
                tuple(row[field] for field in fields) for row in rows)
            assert report['coverage']['roster_observations'] == replaced['coverage']['roster_observations']
            assert replaced['coverage']['branches'][0]['stage_evidence'] == entrance_proof
            assert replaced['coverage']['branches'][0]['child_visits'][0]['stage_evidence'] == pagination_proof
            assert all(item['stage_evidence']['type_id'] == 'detail.integration_replacement'
                       for item in replaced['profile_page_type_evidence'].values())
            assert set(requests) <= set(pages)
            (retained / 'accepted-plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2))
        finally:
            await browser.close()
