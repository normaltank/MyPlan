"""Published receipts retain source-bound identity and declared table titles.

Browser sources below are local fixtures. Synthetic reviewer decisions are
confined to tmp_path; existing real employee decisions are tested separately.
"""
import copy
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from server import identity_review
from server.agent_queue import AgentTaskQueue
from server.browser_batch_tool import run_plan, _enrich_extracted_records
from server.browser_context import new_browser_context
from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline

CLAIM = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
         'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
HTML = ('<meta charset="utf-8"><title>师资队伍</title><header>样本大学 样本学院</header>'
        '<h1>师资队伍</h1><main><ul><li>王芳</li></ul></main>')


@pytest.mark.browser
@pytest.mark.asyncio
async def test_profileless_distinct_decision_reaches_queue_and_university_consumer(tmp_path):
    queue = AgentTaskQueue(tmp_path / 'queue.db', legacy_json_path=None)
    task = queue.submit_task('样本大学', '样本学院', 'https://fixture.example.edu/a')
    claim = queue.claim_task('fixture-worker', task_id=task.task_id)
    out = tmp_path / 'outputs' / 'tasks' / task.task_id / 'attempt-1'
    urls = ['https://fixture.example.edu/a', 'https://fixture.example.edu/b']
    plan = {'school': task.school, 'college': task.college, 'branches': [
        {**CLAIM, 'url': u, 'card_selector': 'main li', 'single_page': True} for u in urls]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await new_browser_context(browser, polite=False)
        await context.route('**/*', lambda route: route.fulfill(body=HTML, content_type='text/html; charset=utf-8'))
        try:
            first = await run_plan(plan, context, out)
            [item] = first['identity_review_items']
            decision_path = out / identity_review.DECISIONS_FILE
            identity_review.save_decision(decision_path, item, 'distinct', reviewed_by=7)
            saved_decision = decision_path.read_bytes()
            report = await run_plan(plan, context, out)
            rows = json.loads(Path(report['records_path']).read_text(encoding='utf-8'))
            assert report['delivery_ready'] and len(rows) == 2 and all(r['主页'] == '' for r in rows)
            audit_path = Path(report['audit_path'])
            original_audit = audit_path.read_bytes()
            for key, value in [('receipt_sha256', '0' * 64), ('record_index', 0),
                               ('record_index', 2), ('profile_url', urls[0] + '/other')]:
                bad = copy.deepcopy(report)
                bad['coverage']['field_sources'][0][key] = value
                try:
                    audit_path.write_text(json.dumps(bad), encoding='utf-8')
                    with pytest.raises(ValueError):
                        identity_review.audit_pairs(rows, report, task.task_id)
                finally:
                    audit_path.write_bytes(original_audit)
            queue.complete_task(task.task_id, rows, report, worker_id='fixture-worker', lease_token=claim.lease_token)
            snapshot = queue.get_university_snapshot(task.school)
            assert not snapshot['identity_conflicts'] and len(snapshot['records']) == 2
            assert all(r['主页'] == '' for r in snapshot['records'])
            assert decision_path.read_bytes() == saved_decision
        finally:
            await browser.close()


@pytest.mark.parametrize('columns', [True, False])
def test_synthetic_image_cells_carry_printed_teacher_but_not_degree_default(tmp_path, columns):
    from server.image_table_roster import parse_cells
    from test_image_table_roster import scanned_rows
    data = ([['姓名', '职称'], ['王芳', '教师'], ['李华', '教授']] if columns else
            [['职称/学位', '姓名'], ['博士', '王芳']])
    parsed = parse_cells(scanned_rows(data), page_url='https://fixture.example.edu/list',
                         media_url='https://fixture.example.edu/list.png')
    candidates = [FacultyCandidate.from_roster_record(r) for r in parsed['records']]
    source_records = {(r.page_url, r.name): r for r in parsed['records']}
    evidence = {}
    result = FacultyHarvestPipeline.harvest_candidates(candidates, None, base_url='https://fixture.example.edu/list',
        title_field_evidence=evidence, defer_homepage_projection=True)
    rows = result['sheet1_with_email'] + result['sheet2_no_email']
    assert not _enrich_extracted_records(rows, source_records, {'assigned_biographies': {}, 'evidence': []},
                                         tmp_path, {}, candidates, title_field_evidence=evidence)
    assert [r['职称'] for r in rows] == (['教师', '教授'] if columns else [''])


@pytest.mark.browser
@pytest.mark.asyncio
async def test_native_pdf_column_teacher_and_missing_column_value_keep_distinct_semantics(tmp_path):
    import hashlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    import pymupdf
    document = pymupdf.open()
    page = document.new_page()
    for y, cells in [(55, ('姓名', '职称')), (85, ('王芳', '教师')),
                     (115, ('李华', '教授')), (145, ('陈旭', ''))]:
        for x, text in zip((60, 220), cells):
            if text:
                page.insert_text((x, y), text, fontname='china-s', fontsize=12)
    original = document.tobytes()
    html = HTML.replace('<ul><li>王芳</li></ul>', '<a href="/faculty.pdf">师资名单下载</a>')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body, kind = ((original, 'application/pdf') if self.path == '/faculty.pdf'
                          else (html.encode(), 'text/html; charset=utf-8'))
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_HEAD(self):
            self.send_response(200)
            self.send_header('Content-Length', str(len(original)))
            self.end_headers()
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/faculty'
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await new_browser_context(browser, polite=False)
            try:
                result = await run_plan({'school': '样本大学', 'college': '样本学院', 'branches': [
                    {**CLAIM, 'url': url, 'single_page': True, 'card_selector': 'main'}]}, context, tmp_path)
                rows = json.loads(Path(result['records_path']).read_text(encoding='utf-8'))
                assert result['delivery_ready'], result['unresolved']
                assert {r['姓名']:r['职称'] for r in rows} == {'王芳':'教师', '李华':'教授', '陈旭':''}
                assert all(r['主页'] == '' for r in rows)
                sources = result['coverage']['field_sources']
                assert all(s['profile_url'] == url for s in sources)
                assert all(s['segment_record']['pdf_sha256'] == hashlib.sha256(original).hexdigest() for s in sources)
                assert result['source_qualification_evidence'][0]['browser_proof']['confirmed'] is True
            finally:
                await browser.close()
    finally:
        server.shutdown()
        server.server_close()
