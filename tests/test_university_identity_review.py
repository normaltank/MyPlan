"""L1: a cross-college homonym blocks the university version until an admin reviews it."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from server import app as web
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue
from server.university_delivery import publish_delivery

SCHOOL = '样本大学'


def rec(college, name, url, bio='简介'):
    return {'序号': 1, '姓名': name, '职称': '教授', '邮箱': '', '学校': SCHOOL, '学院': college,
            '采集人': 'w', '采集日期': '2026-10-01', '简介': bio, '主页': url}


def finish(queue, task, rows):
    claimed = queue.claim_task('w', task_id=task.task_id)
    queue.complete_task(task.task_id, rows, {'total': len(rows), 'with_email': 0, 'no_email': len(rows)},
                        worker_id='w', lease_token=claimed.lease_token)


def l1_batch(queue, owner_user_id=None):
    chem, phys = queue.submit_batch([(SCHOOL, '化学学院', 'https://chem.example.edu/'),
                                     (SCHOOL, '物理学院', 'https://phys.example.edu/')],
                                    'b1', owner_user_id=owner_user_id)
    finish(queue, chem, [rec('化学学院', '王伟', 'https://chem.example.edu/p/1', '化学王伟的完整简介'),
                         rec('化学学院', '李娜', 'https://chem.example.edu/p/2')])
    finish(queue, phys, [rec('物理学院', '王伟', 'https://phys.example.edu/p/9', '物理王伟的完整简介')])
    return chem, phys


def clients(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    alice = store.sign_in('alice')
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(tmp_path / 'outputs'))
    browsers = {}
    for name in ('admin', 'alice', 'bob'):
        browser = TestClient(web.app)
        assert browser.post('/api/auth/name', json={'display_name': name}).status_code == 200
        browser.headers['X-CSRF-Token'] = browser.get('/api/auth/me').json()['csrf_token']
        browsers[name] = browser
    return queue, alice, browsers


def test_distinct_decision_by_anyone_releases_l1_with_versioned_evidence(tmp_path, monkeypatch):
    queue, alice_user, b = clients(tmp_path, monkeypatch)
    chem, phys = l1_batch(queue, owner_user_id=alice_user.id)
    batch = b['alice'].get('/api/batch/b1').json()
    assert batch['delivery_state'] == 'blocked' and not batch['university_files']
    review_url = batch['delivery_scopes'][0]['review_url']
    assert all(st['university_review_url'] == review_url for st in batch['subtasks'])

    page = b['alice'].get(review_url)
    assert page.status_code == 200
    assert '化学王伟的完整简介' in page.text and '物理王伟的完整简介' in page.text
    assert '确认是不同教师' in page.text
    assert b['bob'].get(review_url).status_code == 200
    groups = b['alice'].get('/api/university-review/b1', params={'school': SCHOOL}).json()['groups']
    assert len(groups) == 1 and len(groups[0]['observations']) == 2
    group_id = groups[0]['group_id']

    decide = '/api/university-review/b1/decisions'
    body = {'school': SCHOOL, 'group_id': group_id, 'verdict': 'distinct',
            'reason': '两学院主页照片、研究方向与学历均不同', 'expected_revision': 0}
    assert b['admin'].post(decide, json=body, headers={'X-CSRF-Token': 'forged'}).status_code == 403
    assert b['admin'].post(decide, json={**body, 'reason': ' '}).status_code == 422
    assert b['admin'].post(decide, json={**body, 'verdict': 'same'}).status_code == 422
    assert b['admin'].post(decide, json={**body, 'group_id': 'f' * 64}).status_code == 409

    insufficient = {**body, 'verdict': 'insufficient', 'reason': '主页均无照片，暂无法判断'}
    assert b['admin'].post(decide, json=insufficient).json()['revision'] == 1
    publish = b['admin'].post('/api/university-review/b1/publish', json={'school': SCHOOL}).json()
    assert publish['state'] == 'blocked' and publish['download_url'] is None
    assert b['admin'].post(decide, json=body).status_code == 409  # stale page
    saved = b['admin'].post(decide, json={**body, 'expected_revision': 1})
    assert saved.status_code == 201 and saved.json()['revision'] == 2
    assert b['admin'].post(decide, json={**body, 'expected_revision': 1}).status_code == 200  # idempotent

    batch = b['alice'].get('/api/batch/b1').json()
    assert batch['delivery_state'] == 'ready'
    delivery = batch['university_files'][0]
    assert delivery['total'] == 3
    download = b['alice'].get(delivery['download_url'])
    assert download.status_code == 200
    excel = Path(delivery['excel_path'])
    names = [row[1] for row in load_workbook(excel)['无邮箱教师列表'].iter_rows(min_row=2, values_only=True)]
    assert sorted(names) == ['李娜', '王伟', '王伟']
    manifest = json.loads(Path(delivery['manifest_path']).read_text(encoding='utf-8'))
    [certificate] = manifest['university_identity_reviews']
    assert certificate['verdict'] == 'distinct' and certificate['revision'] == 2
    assert certificate['reason'] == body['reason'] and len(certificate['observation_ids']) == 2
    assert all(queue.get_task(t.task_id).status == 'completed' for t in (chem, phys))

    # Withdrawing the decision makes the version that depended on it unavailable.
    original = excel.read_bytes()
    assert b['admin'].post(decide, json={**insufficient, 'expected_revision': 2}).status_code == 201
    assert b['alice'].get(delivery['download_url']).status_code == 409
    assert excel.read_bytes() == original


def test_decision_stops_applying_when_an_observation_changes(tmp_path):
    from server import university_identity_review as review
    from server.university_delivery import scope_identity_review
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    _, phys = l1_batch(queue)
    [group] = scope_identity_review(queue, 'b1', SCHOOL)['groups']
    with queue._connect() as con:
        review.save_decision(con, SCHOOL, group, 'distinct', '已核对', reviewed_by=1, expected_revision=0)
    assert publish_delivery(queue, 'b1', SCHOOL, tmp_path / 'out')['delivery_scope']['state'] == 'ready'
    with queue._connect() as con:
        payload = json.loads(con.execute('SELECT payload_json FROM records WHERE task_id=?',
                                         (phys.task_id,)).fetchone()[0])
        payload['简介'] = '另一段简介'
        con.execute('UPDATE records SET payload_json=? WHERE task_id=?',
                    (json.dumps(payload, ensure_ascii=False), phys.task_id))
    result = scope_identity_review(queue, 'b1', SCHOOL)
    assert result['scope']['state'] == 'blocked'
    assert result['groups'][0]['group_id'] != group['group_id'] and not result['groups'][0]['applied']


def test_observations_on_one_profile_page_cannot_be_declared_distinct(tmp_path):
    import pytest
    from server import university_identity_review as review
    from server.university_delivery import scope_identity_review
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    tasks = queue.submit_batch([(SCHOOL, college, f'https://{college}.example.edu/')
                                for college in ('化学学院', '物理学院', '数学学院')], 'b1')
    shared = 'https://www.example.edu/people/wangwei'
    for task, url, bio in zip(tasks, [shared, shared, 'https://math.example.edu/p/3'], ['甲', '乙', '丙']):
        finish(queue, task, [rec(task.college, '王伟', url, bio)])
    [group] = scope_identity_review(queue, 'b1', SCHOOL)['groups']
    assert len(group['observations']) == 3 and not group['merge_keys_distinct']
    with queue._connect() as con, pytest.raises(ValueError):
        review.save_decision(con, SCHOOL, group, 'distinct', '已核对', reviewed_by=1, expected_revision=0)
