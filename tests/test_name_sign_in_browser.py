"""Real browser acceptance for two colleagues who sign in by name, on an isolated DB."""
from __future__ import annotations

from datetime import datetime
import socket
import threading
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright
import pytest
import uvicorn

from server import app as web
from server.agent_queue import AgentTaskQueue
from server.access_control import AccountStore
from server.university_delivery import publish_delivery
from test_authenticated_image_review import _sample_audit

pytestmark = pytest.mark.browser


def _sign_in(page, base, name):
    page.goto(base)
    page.locator('#displayName').fill(name)
    page.locator('#signin button').click()
    page.locator('#schoolInput').wait_for()


def _submit(page, school, college, url):
    page.locator('#schoolInput').fill(school)
    page.locator('#collegeInput').fill(college)
    page.locator('#urlInput').fill(url)
    page.locator('#startSingleBtn').click()
    page.locator('#requestContext').get_by_text('本次采集', exact=False).wait_for()


def _finish(queue, task_id, output_root, name, school, college, base_url):
    claimed = queue.claim_task('staging-worker', task_id=task_id)
    assert claimed is not None
    record = {'序号': '1', '姓名': name, '职称': '教授', '邮箱': f'{name}@example.edu',
              '学校': school, '学院': college, '采集人': '验收夹具',
              '采集日期': '2026-09-28', '简介': '验收用虚拟记录',
              '主页': base_url + '/teacher'}
    queue.complete_task(task_id, [record], {'total': 1, 'with_email': 1, 'no_email': 0},
                        worker_id='staging-worker', lease_token=claimed.lease_token)
    published = publish_delivery(queue, claimed.batch_id, school, output_root)
    assert published['university_excel']
    with queue._connect() as con:
        con.execute('UPDATE tasks SET download_url=? WHERE task_id=?',
                    (published['download_url'], task_id))
    return published['download_url']


def test_two_colleagues_share_work_review_download_and_named_stats(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    output_root = tmp_path / 'outputs'
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(output_root))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(web.app, host='127.0.0.1', port=port,
                                          log_level='error', access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{port}'
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                alice = browser.new_context().new_page()
                bob = browser.new_context().new_page()
                _sign_in(alice, base, '员工甲')
                _sign_in(bob, base, '员工乙')
                assert alice.locator('#accountLabel').inner_text() == '员工甲'
                _submit(alice, '甲大学', '甲学院', 'https://a.example.edu')
                _submit(bob, '乙大学', '乙学院', 'https://b.example.edu')
                a_id = queue.get_request_history(owner_user_id=store.sign_in('员工甲').id)[0]['id']
                b_id = queue.get_request_history(owner_user_id=store.sign_in('员工乙').id)[0]['id']
                assert a_id != b_id
                assert bob.request.get(base + f'/api/task/{a_id}').status == 200

                _sample_audit(output_root, queue.get_task(a_id))
                with queue._connect() as con:
                    con.execute("UPDATE tasks SET status='failed',error='审计未通过交付门禁' WHERE task_id=?", (a_id,))
                alice.locator('#reviewBtn').wait_for(state='visible', timeout=10000)
                assert bob.request.get(base + f'/review/task/{a_id}').status == 200
                alice.locator('#reviewBtn').click()
                alice.locator('textarea').fill('人工核对后的文字')
                alice.locator('.card button').click()
                alice.wait_for_function("document.querySelector('.card .status').textContent.length > 0")
                assert '已保存复核决定' in alice.locator('.card .status').inner_text()
                alice.locator('#retry-review').click()
                alice.get_by_text('已重新入队').wait_for()
                assert queue.get_task(a_id).status == 'queued'

                a_download = _finish(queue, a_id, output_root, 'TeacherA', '甲大学', '甲学院', 'https://a.example.edu')
                b_download = _finish(queue, b_id, output_root, 'TeacherB', '乙大学', '乙学院', 'https://b.example.edu')
                assert alice.request.get(base + b_download).status == 200
                assert bob.request.get(base + a_download).status == 200

                day = datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
                for page in (alice, bob):
                    page.goto(base)
                    page.locator('#reportDay').fill(day)
                    page.locator('button[onclick="loadDailyReport()"]').click()
                    page.locator('#dailySummary').get_by_text('提交 1 个学院', exact=False).wait_for()
                    assert '交付 1 条教师记录' in page.locator('#dailySummary').inner_text()
                    rows = page.locator('#employeeDailyRows').inner_text()
                    assert '员工甲' in rows and '员工乙' in rows
                    page.locator('#historyToggle').click()
                    page.locator('#historyList').get_by_text('乙大学', exact=False).wait_for()
                    history = page.locator('#historyList').inner_text()
                    assert '甲大学' in history and '员工甲' in history and '员工乙' in history
                alice.locator('.account summary').click()
                alice.get_by_text('换个名字').click()
                alice.locator('#displayName').wait_for()
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
