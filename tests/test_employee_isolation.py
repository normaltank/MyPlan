"""Two independent employees must never receive one another's work."""
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


def test_accounts_sessions_and_passwords(tmp_path):
    store = AccountStore(tmp_path / 'tasks.db')
    admin = store.create_user('admin', 'Administrator', 'correct horse battery staple', 'admin')
    alice = store.create_user('alice', 'Alice', 'alice password 12345', 'employee')
    assert admin.role == 'admin'
    assert store.authenticate('alice', 'alice password 12345').id == alice.id
    assert store.authenticate('alice', 'wrong password') is None
    assert 'alice password 12345' not in (tmp_path / 'tasks.db').read_bytes().decode('latin1')
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
    token, _ = store.create_session(alice.id)
    assert store.change_password(alice.id, 'alice password 12345', 'alice replacement 67890')
    assert store.resolve_session(token) is None
    assert store.authenticate('alice', 'alice password 12345') is None
    assert store.authenticate('alice', 'alice replacement 67890').id == alice.id
    token, _ = store.create_session(alice.id)
    with store._connect() as con:
        con.execute('UPDATE users SET enabled=0 WHERE id=?', (alice.id,))
    assert store.resolve_session(token) is None
    assert store.authenticate('alice', 'alice replacement 67890') is None


def test_first_admin_can_be_created_once_from_local_page(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    browser = TestClient(web.app)
    assert '首次创建管理员' in browser.get('/').text
    response = browser.post('/api/auth/bootstrap', json={
        'username': 'admin', 'display_name': 'Administrator',
        'password': 'correct horse battery staple'})
    assert response.status_code == 200
    assert store.has_admin()
    assert '提交采集请求' in browser.get('/').text
    browser.post('/api/auth/logout', headers={'X-CSRF-Token': browser.get('/api/auth/me').json()['csrf_token']})
    assert '请使用管理员分配的账号登录' in browser.get('/').text
    assert browser.post('/api/auth/bootstrap', json={'username': 'other',
        'display_name': 'Other', 'password': 'another secret 12345'}).status_code == 409


def test_batch_ownership_history_and_daily_counts(tmp_path):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    alice = store.create_user('alice', 'Alice', 'alice password 12345', 'employee')
    bob = store.create_user('bob', 'Bob', 'bob password 12345', 'employee')
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


def test_http_isolation_and_no_lease_leak(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    store.create_user('admin', 'Administrator', 'correct horse battery staple', 'admin')
    store.create_user('alice', 'Alice', 'alice password 12345', 'employee')
    store.create_user('bob', 'Bob', 'bob password 12345', 'employee')
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    alice = TestClient(web.app)
    bob = TestClient(web.app)
    admin = TestClient(web.app)
    assert '请使用管理员分配的账号登录' in alice.get('/').text
    assert alice.get('/api/history').status_code == 401
    assert alice.post('/api/auth/login', json={'username': 'alice', 'password': 'alice password 12345'}).status_code == 200
    assert bob.post('/api/auth/login', json={'username': 'bob', 'password': 'bob password 12345'}).status_code == 200
    assert admin.post('/api/auth/login', json={'username': 'admin', 'password': 'correct horse battery staple'}).status_code == 200
    alice_csrf = alice.get('/api/auth/me').json()['csrf_token']
    assert '提交采集请求' in alice.get('/').text
    response = alice.post('/api/collect', json={'school': '甲大学', 'college': '甲学院', 'url': 'https://a.edu'},
                          headers={'X-CSRF-Token': alice_csrf})
    assert response.status_code == 200
    task_id = response.json()['task_id']
    assert bob.get(f'/api/task/{task_id}').status_code == 404
    assert bob.get('/api/history').json()['requests'] == []
    own = alice.get(f'/api/task/{task_id}')
    assert own.status_code == 200
    assert 'lease_token' not in own.json()
    assert 'worker_id' not in own.json()
    queue.claim_task('server-worker', task_id=task_id)
    assert 'lease_token' not in alice.get(f'/api/task/{task_id}').json()
    assert admin.get(f'/api/task/{task_id}').status_code == 200
    assert alice.post('/api/collect', json={'school': '甲大学', 'college': '乙学院', 'url': 'https://b.edu'}).status_code == 403
    assert alice.post('/api/collect', json={'school': '甲大学', 'college': '乙学院', 'url': 'https://b.edu'},
                      headers={'X-CSRF-Token': alice_csrf, 'Origin': 'http://evil.test'}).status_code == 403
    assert alice.post('/api/auth/logout', headers={'X-CSRF-Token': alice_csrf}).status_code == 200
    assert alice.get(f'/api/task/{task_id}').status_code == 401


def test_unowned_legacy_task_is_admin_only(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    store.create_user('admin', 'Administrator', 'correct horse battery staple', 'admin')
    store.create_user('alice', 'Alice', 'alice password 12345', 'employee')
    legacy = queue.submit_task('旧大学', '旧学院', 'https://old.edu')
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    alice = TestClient(web.app)
    admin = TestClient(web.app)
    alice.post('/api/auth/login', json={'username': 'alice', 'password': 'alice password 12345'})
    admin.post('/api/auth/login', json={'username': 'admin', 'password': 'correct horse battery staple'})
    assert alice.get(f'/api/task/{legacy.task_id}').status_code == 404
    assert admin.get(f'/api/task/{legacy.task_id}').status_code == 200


def test_batch_and_download_are_owner_scoped(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    alice_user = store.create_user('alice', 'Alice', 'alice password 12345', 'employee')
    store.create_user('bob', 'Bob', 'bob password 12345', 'employee')
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(tmp_path / 'outputs'))
    alice, bob = TestClient(web.app), TestClient(web.app)
    alice.post('/api/auth/login', json={'username': 'alice', 'password': 'alice password 12345'})
    bob.post('/api/auth/login', json={'username': 'bob', 'password': 'bob password 12345'})
    csrf = alice.get('/api/auth/me').json()['csrf_token']
    response = alice.post('/api/collect_batch', json={'items_text': '甲大学 甲学院 https://a.edu'},
                          headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    batch_id = response.json()['batch_id']
    task_id = queue.get_batch_status(batch_id)['subtasks'][0]['task_id']
    assert queue.get_task(task_id).owner_user_id == alice_user.id
    assert bob.get(f'/api/batch/{batch_id}').status_code == 404
    assert alice.get(f'/api/batch/{batch_id}').status_code == 200
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
    assert bob.get(url).status_code == 404
    assert alice.get(url).status_code == 200
    # A current all-college alias could combine other employees' submissions.
    alias = tmp_path / 'outputs' / '甲大学.xlsx'
    alias.write_bytes(b'current aggregate')
    (tmp_path / 'outputs' / '甲大学.manifest.json').write_text(json.dumps({
        'school': '甲大学', 'college_snapshots': [{'task_id': task_id}]
    }, ensure_ascii=False), encoding='utf-8')
    assert alice.get('/download/甲大学.xlsx').status_code == 404


def test_admin_can_see_employee_daily_rows_and_create_accounts(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    store.create_user('admin', 'Administrator', 'correct horse battery staple', 'admin')
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    admin = TestClient(web.app)
    admin.post('/api/auth/login', json={'username': 'admin', 'password': 'correct horse battery staple'})
    csrf = admin.get('/api/auth/me').json()['csrf_token']
    response = admin.post('/api/admin/users', json={'username': 'alice', 'display_name': 'Alice',
        'password': 'alice password 12345', 'role': 'employee'}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    report = admin.get('/api/reports/daily').json()
    assert report['employees'][0]['username'] == 'alice'
    assert report['employees'][0]['submitted_colleges'] == 0


def test_password_change_revokes_browser_session(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    store.create_user('alice', 'Alice', 'alice password 12345', 'employee')
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    browser = TestClient(web.app)
    assert browser.post('/api/auth/login', json={'username': 'alice',
        'password': 'alice password 12345'}).status_code == 200
    csrf = browser.get('/api/auth/me').json()['csrf_token']
    response = browser.post('/api/auth/change-password', json={'old_password': 'alice password 12345',
        'new_password': 'alice replacement 67890'}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 200
    assert browser.get('/api/auth/me').status_code == 401
    assert browser.post('/api/auth/login', json={'username': 'alice',
        'password': 'alice replacement 67890'}).status_code == 200


@pytest.mark.asyncio
async def test_remote_plain_http_cannot_accept_password(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    store.create_user('alice', 'Alice', 'alice password 12345', 'employee')
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    transport = httpx.ASGITransport(app=web.app, client=('192.0.2.10', 12345))
    async with httpx.AsyncClient(transport=transport, base_url='http://example.test') as client:
        response = await client.post('/api/auth/login', json={'username': 'alice',
            'password': 'alice password 12345'})
        assert response.status_code == 403
        assert not client.cookies
        assert (await client.post('/api/auth/bootstrap', json={'username': 'other',
            'display_name': 'Other', 'password': 'another secret 12345'})).status_code == 403


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
