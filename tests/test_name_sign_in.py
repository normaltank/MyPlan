"""Staff sign in by typing a name; the name attributes work, and everyone sees everything."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from server import app as web
from server.agent_queue import AgentTaskQueue
from server.access_control import AccountStore


def _setup(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    return queue, store


def _signed_in(name):
    browser = TestClient(web.app)
    assert browser.post('/api/auth/name', json={'display_name': name}).status_code == 200
    return browser, browser.get('/api/auth/me').json()['csrf_token']


def test_same_name_returns_same_account_and_sessions_are_revocable(tmp_path):
    store = AccountStore(tmp_path / 'tasks.db')
    alice = store.sign_in('  张  三 ')
    assert alice.display_name == '张 三'
    assert store.sign_in('张 三').id == alice.id
    assert store.sign_in('李四').id != alice.id
    with pytest.raises(ValueError):
        store.sign_in('   ')
    with pytest.raises(ValueError):
        store.sign_in('名' * 41)
    token, csrf = store.create_session(alice.id)
    assert store.resolve_session(token).id == alice.id
    assert store.verify_csrf(token, csrf)
    assert not store.verify_csrf(token, 'wrong')
    store.revoke_session(token)
    assert store.resolve_session(token) is None
    token, _ = store.create_session(alice.id)
    with store._connect() as con:
        con.execute('UPDATE browser_sessions SET expires_at=0 WHERE token_hash=?',
                    (store._hash_token(token),))
    assert store.resolve_session(token) is None


def test_existing_account_is_reused_by_its_display_name(tmp_path):
    store = AccountStore(tmp_path / 'tasks.db')
    with store._connect() as con:
        con.execute("INSERT INTO users(username,display_name,role,password_hash,created_at) "
                    "VALUES ('badr','Badr','admin','pbkdf2_sha256$1$00$00',0)")
    assert store.sign_in('Badr').username == 'badr'


def test_batch_ownership_history_and_daily_counts(tmp_path):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    alice = store.sign_in('alice')
    bob = store.sign_in('bob')
    batch = queue.submit_batch([('甲大学', '甲学院', 'https://a.edu'),
                                ('甲大学', '乙学院', 'https://b.edu')], 'batch-a', owner_user_id=alice.id)
    other = queue.submit_task('乙大学', '丙学院', 'https://c.edu', owner_user_id=bob.id)
    legacy = queue.submit_task('旧大学', '旧学院', 'https://old.edu')
    assert {task.owner_user_id for task in batch} == {alice.id}
    assert queue.get_task(other.task_id).owner_user_id == bob.id
    assert queue.get_task(legacy.task_id).owner_user_id is None
    assert [row['id'] for row in queue.get_request_history(owner_user_id=alice.id)] == ['batch-a']
    assert len(queue.get_request_history()) == 3
    today = datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat()
    counts = queue.get_daily_counts(today, owner_user_id=alice.id)
    assert counts['submitted_colleges'] == 2
    assert counts['completed_colleges'] == 0
    claimed = queue.claim_task('worker', task_id=batch[0].task_id)
    queue.complete_task(claimed.task_id,
                        [{'姓名': '测试教师', '职称': '教授', '邮箱': 'teacher@example.edu',
                          '简介': '简介', '主页': 'https://a.edu/teacher'}],
                        {'total': 1, 'with_email': 1, 'no_email': 0},
                        worker_id='worker', lease_token=claimed.lease_token)
    counts = queue.get_daily_counts(today, owner_user_id=alice.id)
    assert counts['completed_colleges'] == 1
    assert counts['delivered_record_rows'] == 1
    assert queue.get_daily_counts(today, owner_user_id=bob.id)['delivered_record_rows'] == 0


def test_name_page_then_shared_work_without_lease_leak(tmp_path, monkeypatch):
    queue, _store = _setup(tmp_path, monkeypatch)
    anonymous = TestClient(web.app)
    assert '你是谁' in anonymous.get('/').text
    assert anonymous.get('/api/history').status_code == 401
    assert anonymous.post('/api/auth/name', json={'display_name': ''}).status_code == 422
    alice, alice_csrf = _signed_in('alice')
    bob, _ = _signed_in('bob')
    assert '提交采集请求' in alice.get('/').text
    response = alice.post('/api/collect', json={'school': '甲大学', 'college': '甲学院', 'url': 'https://a.edu'},
                          headers={'X-CSRF-Token': alice_csrf})
    assert response.status_code == 200
    task_id = response.json()['task_id']
    shared = bob.get(f'/api/task/{task_id}')
    assert shared.status_code == 200
    assert 'lease_token' not in shared.json()
    assert 'worker_id' not in shared.json()
    history = bob.get('/api/history').json()['requests']
    assert [(row['id'], row['submitted_by']) for row in history] == [(task_id, 'alice')]
    queue.claim_task('server-worker', task_id=task_id)
    assert 'lease_token' not in alice.get(f'/api/task/{task_id}').json()
    assert alice.post('/api/collect', json={'school': '甲大学', 'college': '乙学院', 'url': 'https://b.edu'}).status_code == 403
    assert alice.post('/api/collect', json={'school': '甲大学', 'college': '乙学院', 'url': 'https://b.edu'},
                      headers={'X-CSRF-Token': alice_csrf, 'Origin': 'http://evil.test'}).status_code == 403
    assert TestClient(web.app).post('/api/auth/name', json={'display_name': 'mallory'},
                                    headers={'Origin': 'http://evil.test'}).status_code == 403
    assert alice.post('/api/auth/logout', headers={'X-CSRF-Token': alice_csrf}).status_code == 200
    assert alice.get(f'/api/task/{task_id}').status_code == 401


def test_unowned_legacy_task_is_visible_to_everyone(tmp_path, monkeypatch):
    queue, _store = _setup(tmp_path, monkeypatch)
    legacy = queue.submit_task('旧大学', '旧学院', 'https://old.edu')
    alice, _ = _signed_in('alice')
    assert alice.get(f'/api/task/{legacy.task_id}').status_code == 200


def test_batch_and_download_are_shared_but_latest_is_your_own(tmp_path, monkeypatch):
    queue, store = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(tmp_path / 'outputs'))
    alice, csrf = _signed_in('alice')
    bob, _ = _signed_in('bob')
    response = alice.post('/api/collect_batch', json={'items_text': '甲大学 甲学院 https://a.edu'},
                          headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    batch_id = response.json()['batch_id']
    task_id = queue.get_batch_status(batch_id)['subtasks'][0]['task_id']
    assert queue.get_task(task_id).owner_user_id == store.sign_in('alice').id
    assert bob.get(f'/api/batch/{batch_id}').status_code == 200
    assert alice.get('/api/batch/latest').json()['batch_id'] == batch_id
    assert bob.get('/api/batch/latest').status_code == 404
    path = tmp_path / 'outputs' / 'versions' / 'school' / 'version'
    path.mkdir(parents=True)
    (path / '甲大学.xlsx').write_bytes(b'xlsx')
    (path / 'manifest.json').write_text(json.dumps({
        'school': '甲大学', 'scope': {'batch_id': batch_id},
        'college_snapshots': [{'task_id': task_id}]
    }, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr('server.university_delivery.manifest_snapshot_is_safe', lambda *_: True)
    monkeypatch.setattr(queue, 'get_delivery_scope', lambda *_: {'state': 'ready'})
    url = '/download/versions/school/version/甲大学.xlsx'
    assert bob.get(url).status_code == 200
    assert TestClient(web.app).get(url).status_code == 401


def test_daily_report_lists_everyone_who_worked_that_day(tmp_path, monkeypatch):
    queue, store = _setup(tmp_path, monkeypatch)
    alice = store.sign_in('alice')
    store.sign_in('idle')
    queue.submit_task('甲大学', '甲学院', 'https://a.edu', owner_user_id=alice.id)
    bob, _ = _signed_in('bob')
    report = bob.get('/api/reports/daily').json()
    assert report['submitted_colleges'] == 0
    assert [(row['display_name'], row['submitted_colleges']) for row in report['employees']] == [('alice', 1)]


@pytest.mark.asyncio
async def test_lan_colleague_signs_in_over_plain_http_but_cannot_reach_worker_routes(tmp_path, monkeypatch):
    queue, _store = _setup(tmp_path, monkeypatch)
    task = queue.submit_task('甲大学', '甲学院', 'https://a.edu')
    claim = queue.claim_task('server-worker', task_id=task.task_id)
    transport = httpx.ASGITransport(app=web.app, client=('192.168.1.23', 12345))
    async with httpx.AsyncClient(transport=transport, base_url='http://192.168.1.5:18000') as client:
        assert (await client.post('/api/auth/name', json={'display_name': 'alice'})).status_code == 200
        assert (await client.get('/api/auth/me')).json()['display_name'] == 'alice'
        renew = await client.post(f'/api/queue/renew/{task.task_id}', json={
            'worker_id': 'server-worker', 'lease_seconds': 120, 'lease_token': claim.lease_token})
        assert renew.status_code == 403
        csrf = client.cookies['faculty_csrf']
        assert (await client.post(f'/api/queue/claim/{task.task_id}',
                                  headers={'X-CSRF-Token': csrf})).status_code == 403


def test_local_worker_can_renew_and_sync_without_browser_session(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    task = queue.submit_task('甲大学', '甲学院', 'https://a.edu')
    claim = queue.claim_task('server-worker', task_id=task.task_id)
    browserless = TestClient(web.app)
    renew = browserless.post(f'/api/queue/renew/{task.task_id}', json={
        'worker_id': 'server-worker', 'lease_seconds': 120,
        'lease_token': claim.lease_token})
    assert renew.status_code == 200
    monkeypatch.setattr('server.university_delivery.publish_delivery', lambda *_: {
        'delivery_scope': {'state': 'ready'}, 'university_excel': None})
    synced = browserless.post(f'/api/task/{task.task_id}/sync', json={
        'records': [{'姓名': '教师甲', '邮箱': '', '主页': 'https://a.edu/a'}],
        'stats': {'total': 1, 'with_email': 0, 'no_email': 1},
        'worker_id': 'server-worker', 'lease_token': claim.lease_token})
    assert synced.status_code == 200
    assert queue.get_task(task.task_id).status == 'completed'
