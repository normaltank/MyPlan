"""Current work must not be replaced by a terminal/global historical request."""
import asyncio
import contextlib
from urllib.parse import urlparse

import pytest
from fastapi import HTTPException
from playwright.async_api import async_playwright

from server import app as web
from server.agent_queue import AgentTaskQueue
from server.agent_worker import AgentWorker


def test_history_metadata_groups_requests_and_preserves_all_task_rows(tmp_path):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    old = queue.submit_batch([('绍兴文理学院', f'学院{i}', 'https://example.edu') for i in range(11)], 'old')
    recent = queue.submit_task('新学校', '新学院', 'https://example.edu/new')
    with queue._connect() as db:
        db.execute("UPDATE tasks SET status='completed',created_at=10 WHERE batch_id='old'")
        db.execute('UPDATE tasks SET created_at=20 WHERE task_id=?', (recent.task_id,))
    rows = queue.get_request_history(limit=1)
    assert len(rows) == 1 and rows[0]['id'] == recent.task_id and rows[0]['kind'] == 'task'
    rows = queue.get_request_history(limit=20)
    assert rows[1]['id'] == 'old' and rows[1]['total_colleges'] == 11
    assert queue.get_request_history(limit=1, offset=1)[0]['id'] == 'old'
    assert rows[1]['active'] is False and rows[1]['schools'] == ['绍兴文理学院']
    assert all('records' not in row and 'lease_token' not in row for row in rows)
    assert queue.get_queue_stats()['total_tasks'] == len(old) + 1


@pytest.mark.asyncio
async def test_missing_official_entry_is_rejected_before_creating_any_task(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    monkeypatch.setattr(web, 'agent_queue', queue)
    with pytest.raises(HTTPException, match='官网入口'):
        await web.start_single_collection(web.CollectRequest(school='新学校', college='新学院', url=None))
    with pytest.raises(HTTPException, match='官网入口'):
        await web.start_batch_collection(web.BatchCollectRequest(items_text='新学校 新学院'))
    with pytest.raises(HTTPException, match='第 2 行'):
        await web.start_batch_collection(web.BatchCollectRequest(
            items_text='一校 一院 https://example.edu/\n二校 二院'))
    assert queue.get_queue_stats()['total_tasks'] == 0


def test_worker_presence_expires_and_instances_do_not_overwrite(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    monkeypatch.setattr('server.agent_queue.time.time', lambda: 100)
    assert queue.get_worker_status()['online'] is False
    queue.touch_worker('one')
    queue.touch_worker('two')
    assert queue.get_worker_status()['online_count'] == 2
    queue.remove_worker('one')
    assert queue.get_worker_status()['online_count'] == 1
    monkeypatch.setattr('server.agent_queue.time.time', lambda: 131)
    assert queue.get_worker_status()['online'] is False


@pytest.mark.asyncio
async def test_idle_worker_heartbeat_and_clean_shutdown(tmp_path):
    worker = AgentWorker(db_path=tmp_path / 'tasks.db', output_dir=tmp_path / 'out')
    assert not worker.queue.get_worker_status()['online']
    runner = asyncio.create_task(worker.run_forever())
    try:
        for _ in range(10):
            await asyncio.sleep(.05)
            if worker.queue.get_worker_status()['online']:
                break
        assert worker.queue.get_worker_status()['online']
        assert worker.queue.get_queue_stats()['total_tasks'] == 0
    finally:
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
    assert not worker.queue.get_worker_status()['online']


OLD = {'batch_id': 'old', 'status': 'completed', 'delivery_state': 'blocked', 'progress': 100,
       'total_colleges': 11, 'completed_colleges': 11, 'failed_colleges': 0,
       'stats': {'total': 100, 'with_email': 0}, 'current_step': '历史完成',
       'delivery_scopes': [{'school': '绍兴文理学院', 'target_colleges': ['学院'], 'state': 'blocked'}],
       'subtasks': [{'school': '绍兴文理学院', 'college': f'学院{i}', 'status': 'completed',
                    'counts_basis': 'profile_observations_not_verified_people', 'stats': {'total': 10},
                    'download_url': None, 'agent_thoughts': []} for i in range(11)]}


async def local_page(browser, *, storage=None, active=False, hold_old=None, active_batch=False,
                     foreign_active=False):
    page = await browser.new_page()
    calls = []
    if storage:
        await page.add_init_script('localStorage.setItem("currentBatchId", "old");')
    if active:
        await page.add_init_script('localStorage.setItem("facultyActiveRequest:1", JSON.stringify({kind:"task",id:"new"}));')
    if active_batch:
        await page.add_init_script('localStorage.setItem("facultyActiveRequest:1", JSON.stringify({kind:"batch",id:"old"}));')
    if foreign_active:
        await page.add_init_script('localStorage.setItem("facultyActiveRequest:2", JSON.stringify({kind:"task",id:"other"}));')
    html = await web.serve_ui()
    async def route(request):
        path = urlparse(request.request.url).path
        calls.append(path)
        if path == '/api/batch/old' and hold_old is not None:
            await hold_old.wait()
        if path == '/':
            await request.fulfill(body=html, content_type='text/html; charset=utf-8')
            return
        values = {'/api/queue/stats': {'queued': 0, 'running': 0, 'completed': 22, 'failed': 0},
                  '/api/auth/me': {'id': 1, 'username': 'tester', 'display_name': '测试员工', 'role': 'employee', 'csrf_token': 'test'},
                  '/api/reports/daily': {'submitted_colleges': 0, 'completed_colleges': 0,
                                         'delivered_record_rows': 0, 'failed_colleges': 0},
                  '/api/worker/status': {'online': True, 'online_count': 1},
                  '/api/history': {'requests': [{'kind': 'batch', 'id': 'old', 'schools': ['绍兴文理学院'],
                      'total_colleges': 11, 'active': False, 'created_at': 100}]},
                  '/api/batch/old': {**OLD, 'status': 'running', 'progress': 0, 'delivery_state': 'collecting',
                      'current_step': '后台已接单，正在核实官网结构'} if active_batch else OLD,
                  '/api/batch/latest': OLD,
                  '/api/task/new': {'task_id': 'new', 'school': '新学校', 'college': '新学院',
                      'status': 'running', 'progress': 20, 'current_step': '正在核实结构', 'agent_thoughts': []}}
        if path in values:
            await request.fulfill(json=values[path])
        else:
            await request.abort()
    await page.route('**/*', route)
    await page.goto('http://frontend.test/')
    return page, calls


@pytest.mark.asyncio
@pytest.mark.parametrize('legacy_storage', [False, True])
@pytest.mark.browser
async def test_terminal_history_is_not_automatically_the_current_request(legacy_storage):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page, calls = await local_page(browser, storage=legacy_storage)
            await page.wait_for_timeout(1300)
            assert '绍兴文理学院' not in await page.locator('#subtaskList').inner_text()
            assert '/api/batch/latest' not in calls
            assert await page.locator('#startSingleBtn').is_enabled()
            assert '待提交' in await page.locator('#taskBadge').inner_text()
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_late_history_response_cannot_overwrite_new_request_view():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        release = asyncio.Event()
        try:
            page, calls = await local_page(browser, hold_old=release)
            await page.locator('#historyToggle').click()
            await page.locator('#historyList button').first.click()
            await page.wait_for_timeout(100)
            assert '/api/batch/old' in calls
            await page.locator('#newRequestBtn').click()
            release.set()
            await page.wait_for_timeout(250)
            assert '待提交' in await page.locator('#taskBadge').inner_text()
            assert await page.locator('#subtaskList > div').count() == 0
            assert await page.locator('#resultArea').is_hidden()
        finally:
            release.set()
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_other_employee_browser_pointer_is_not_restored():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page, calls = await local_page(browser, foreign_active=True)
            await page.wait_for_timeout(250)
            assert '/api/task/other' not in calls
            assert '待提交' in await page.locator('#taskBadge').inner_text()
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_running_batch_pointer_restores_only_its_own_batch():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page, calls = await local_page(browser, active_batch=True)
            await page.locator('#taskBadge').get_by_text('自动采集中').wait_for(timeout=3000)
            assert '/api/batch/old' in calls and '/api/batch/latest' not in calls
            assert '自动采集中' in await page.locator('#taskBadge').inner_text()
            assert '核实官网结构' in await page.locator('#stepText').inner_text()
            assert '侦察中' in await page.locator('#percentText').inner_text()
            assert '本次采集' in await page.locator('#requestContext').inner_text()
            assert await page.locator('#resultArea').is_hidden()
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_history_is_explicit_and_return_to_new_request_clears_old_results():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page, _ = await local_page(browser)
            await page.locator('#historyToggle').click(timeout=1500)
            await page.locator('#historyList button').first.click(timeout=1500)
            await page.wait_for_timeout(1200)
            assert await page.locator('#subtaskList > div').count() == 11
            assert '待复核' in await page.locator('#taskBadge').inner_text()
            assert await page.locator('#downloadBtn').is_hidden()
            assert await page.locator('#resultStatusCard').get_attribute('data-tone') == 'warn'
            assert await page.locator('#resultStatusIcon use').get_attribute('href') == '#i-alert'
            assert '历史' in await page.locator('#requestContext').inner_text()
            await page.locator('#newRequestBtn').click()
            assert await page.locator('#subtaskCardBox').is_hidden()
            assert await page.locator('#resultArea').is_hidden()
            assert '待提交' in await page.locator('#taskBadge').inner_text()
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_current_running_single_request_is_restored():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page, calls = await local_page(browser, active=True)
            await page.wait_for_timeout(1200)
            assert '/api/task/new' in calls
            assert '核实结构' in await page.locator('#stepText').inner_text()
            assert '/api/batch/latest' not in calls
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_single_form_requires_entry_before_posting():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page, calls = await local_page(browser)
            await page.locator('#schoolInput').fill('新学校')
            await page.locator('#collegeInput').fill('新学院')
            page.once('dialog', lambda dialog: asyncio.create_task(dialog.accept()))
            await page.locator('#startSingleBtn').click()
            await page.wait_for_timeout(100)
            assert '/api/collect' not in calls
            assert '待提交' in await page.locator('#taskBadge').inner_text()
        finally:
            await browser.close()
