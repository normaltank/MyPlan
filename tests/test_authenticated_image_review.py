"""Task image review needs a signed-in name; everyone signed in may review any task."""
from __future__ import annotations

from hashlib import sha256
import json

from fastapi.testclient import TestClient
from PIL import Image

from server import app as web
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue
from server.image_bio_review import (collect_review_items, collect_email_conflict_items,
                                     apply_review_decisions)
from server.task_image_review import carry_review_decisions, task_review_audit
from server.task_image_review import legacy_review_audits


def _sample_audit(root, task):
    attempt = root / 'tasks' / task.task_id / 'attempt-1'
    attempt.mkdir(parents=True)
    image = attempt / 'original.png'
    Image.new('RGB', (200, 100), 'white').save(image)
    digest = sha256(image.read_bytes()).hexdigest()
    ocr = attempt / 'original_ocr.json'
    ocr.write_text(json.dumps({'kind': 'image', 'source_sha256': digest,
        'pages': [{'page': 1, 'source': 'local_ocr', 'lines': [
            {'text': '待核文字', 'confidence': .55,
             'box': [[10, 40], [150, 40], [150, 60], [10, 60]]}]}]},
        ensure_ascii=False), encoding='utf-8')
    audit = attempt / 'sample_audit.json'
    audit.write_text(json.dumps({'school': task.school, 'college': task.college,
        'audit_path': str(audit), 'image_biography_evidence': [{
            'media_url': 'https://faculty.example.edu/cv.png', 'kind': 'image',
            'sha256': digest, 'original_path': str(image), 'ocr_path': str(ocr)}],
        'unresolved': [{'status': 'image_bio_low_confidence',
                        'media_url': 'https://faculty.example.edu/cv.png'}]},
        ensure_ascii=False), encoding='utf-8')
    return audit


def _client(store, name):
    client = TestClient(web.app)
    assert client.post('/api/auth/name', json={'display_name': name}).status_code == 200
    return client, client.get('/api/auth/me').json()['csrf_token']


def test_review_page_needs_a_name_and_is_shared_by_everyone(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    alice = store.sign_in('alice')
    store.sign_in('bob')
    task = queue.submit_task('甲大学', '甲学院', 'https://a.edu', owner_user_id=alice.id)
    audit = _sample_audit(tmp_path / 'outputs', task)
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',error='审计未通过交付门禁' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(tmp_path / 'outputs'))
    owner, csrf = _client(store, 'alice')
    other, other_csrf = _client(store, 'bob')
    url = f'/review/task/{task.task_id}'
    decision_url = f'/api/task/{task.task_id}/review/ocr'
    assert TestClient(web.app).get(url).status_code == 401
    assert other.get(url).status_code == 200
    page = owner.get(url)
    assert page.status_code == 200 and '待核文字' in page.text
    assert decision_url in page.text and '8765' not in page.text
    assert owner.get(f'/api/task/{task.task_id}').json()['review_url'] == url
    item = collect_review_items(audit)[0]
    payload = {'id': item['id'], 'verified_text': '人工核对文字'}
    assert owner.post(decision_url, json=payload).status_code == 403
    assert other.post(decision_url, json=payload, headers={'X-CSRF-Token': csrf}).status_code == 403
    assert owner.post(decision_url, json=payload, headers={'X-CSRF-Token': csrf}).status_code == 204
    assert json.loads((audit.parent / 'image_bio_reviews.json').read_text(encoding='utf-8'))['decisions'][0]['verified_text'] == '人工核对文字'
    assert other.post(decision_url, json=payload, headers={'X-CSRF-Token': other_csrf}).status_code == 204


def test_review_retry_requires_complete_decisions_and_preserves_owner(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    alice = store.sign_in('alice')
    task = queue.submit_task('甲大学', '甲学院', 'https://a.edu', owner_user_id=alice.id)
    audit = _sample_audit(tmp_path / 'outputs', task)
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',error='审计未通过交付门禁' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(tmp_path / 'outputs'))
    owner, csrf = _client(store, 'alice')
    retry_url = f'/api/task/{task.task_id}/review/retry'
    headers = {'X-CSRF-Token': csrf}
    assert owner.post(retry_url, headers=headers).status_code == 409
    item = collect_review_items(audit)[0]
    assert owner.post(f'/api/task/{task.task_id}/review/ocr',
                      json={'id': item['id'], 'verified_text': '确认文字'}, headers=headers).status_code == 204
    response = owner.post(retry_url, headers=headers)
    assert response.status_code == 200
    assert queue.get_task(task.task_id).status == 'queued'
    assert queue.get_task(task.task_id).owner_user_id == alice.id
    assert (audit.parent.parent / 'review-resume.json').is_file()
    assert owner.post(retry_url, headers=headers).status_code == 409
    next_attempt = audit.parent.parent / 'attempt-2'
    carry_review_decisions(audit.parent.parent, next_attempt)
    scanned = json.loads((audit.parent / 'original_ocr.json').read_text(encoding='utf-8'))
    adjusted, reviewed = apply_review_decisions(scanned, item['media_url'],
        item['source_sha256'], next_attempt / 'image_bio_reviews.json')
    assert reviewed == {(1, 0)}
    assert adjusted['pages'][0]['lines'][0]['text'] == '确认文字'


def test_legacy_scratch_review_is_open_to_everyone_signed_in(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    store.sign_in('admin')
    store.sign_in('alice')
    legacy = queue.submit_task('旧大学', '旧学院', 'https://old.edu')
    root = tmp_path / 'scratch'
    audit = _sample_audit(root, legacy)
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'LEGACY_REVIEW_ROOT', root)
    admin, csrf = _client(store, 'admin')
    employee, _ = _client(store, 'alice')
    assert TestClient(web.app).get('/review/legacy').status_code == 401
    listing = employee.get('/review/legacy')
    assert listing.status_code == 200 and '旧大学' in listing.text
    from hashlib import sha256 as hash_value
    key = hash_value(str(audit.resolve()).encode('utf-8')).hexdigest()[:24]
    url = f'/review/legacy/{key}'
    assert employee.get(url).status_code == 200
    assert '待核文字' in admin.get(url).text
    item = collect_review_items(audit)[0]
    response = admin.post(f'/api/review/legacy/{key}/ocr',
        json={'id': item['id'], 'verified_text': '历史人工复核'},
        headers={'X-CSRF-Token': csrf})
    assert response.status_code == 204
    assert (audit.parent / 'image_bio_reviews.json').is_file()


def test_completed_legacy_review_remains_in_admin_archive(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    store.sign_in('admin')
    legacy = queue.submit_task('旧大学', '旧学院', 'https://old.edu')
    root = tmp_path / 'scratch'
    audit = _sample_audit(root, legacy)
    (audit.parent / 'image_bio_reviews.json').write_text(
        json.dumps({'schema': 1, 'decisions': [{'id': 'saved'}]}), encoding='utf-8')
    data = json.loads(audit.read_text(encoding='utf-8'))
    data['unresolved'] = []
    audit.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'LEGACY_REVIEW_ROOT', root)
    admin, _ = _client(store, 'admin')
    assert any(path == audit.resolve() for _, path, _ in legacy_review_audits(root))
    listing = admin.get('/review/legacy')
    assert '已复核' in listing.text


def test_latest_review_audit_uses_capture_time_after_retry(tmp_path):
    import os
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    task = queue.submit_task('甲大学', '甲学院', 'https://a.edu')
    root = tmp_path / 'outputs'
    first = _sample_audit(root, task)
    second = first.parent.parent / 'attempt-2' / first.name
    second.parent.mkdir()
    second_audit = json.loads(first.read_text(encoding='utf-8'))
    second_audit['audit_path'] = str(second)
    second.write_text(json.dumps(second_audit, ensure_ascii=False), encoding='utf-8')
    os.utime(first, (1000, 1000))
    os.utime(second, (2000, 2000))
    assert task_review_audit(root, task) == second.resolve()
    os.utime(first, (3000, 3000))
    assert task_review_audit(root, task) == first.resolve()


def test_email_review_choice_in_main_web(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    alice_user = store.sign_in('alice')
    store.sign_in('bob')
    task = queue.submit_task('甲大学', '甲学院', 'https://a.edu', owner_user_id=alice_user.id)
    audit = _sample_audit(tmp_path / 'outputs', task)
    data = json.loads(audit.read_text(encoding='utf-8'))
    evidence = data['image_biography_evidence'][0]
    evidence.update(profile_url='https://faculty.example.edu/profile', assigned_name='张富有')
    ocr_path = audit.parent / 'original_ocr.json'
    scanned = json.loads(ocr_path.read_text(encoding='utf-8'))
    scanned['pages'][0]['lines'] = [
        {'text': '张富有 教授', 'confidence': .98,
         'box': [[10, 5], [150, 5], [150, 20], [10, 20]]},
        {'text': '邮箱 123456@qq.com', 'confidence': .98,
         'box': [[10, 40], [150, 40], [150, 55], [10, 55]]},
        {'text': '邮箱 234567@163.com', 'confidence': .98,
         'box': [[10, 60], [150, 60], [150, 75], [10, 75]]}]
    scanned['pages'][0]['text'] = scanned['text'] = '\n'.join(
        line['text'] for line in scanned['pages'][0]['lines'])
    ocr_path.write_text(json.dumps(scanned, ensure_ascii=False), encoding='utf-8')
    records_path = audit.parent / 'records.json'
    records_path.write_text(json.dumps([{'主页': evidence['profile_url'], '姓名': '张富有', '邮箱': ''}],
                                  ensure_ascii=False), encoding='utf-8')
    data['records_path'] = str(records_path)
    data['unresolved'] = [{'url': evidence['profile_url'], 'name': '张富有',
                           'status': 'image_bio_email_conflict'}]
    audit.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',error='审计未通过交付门禁' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(tmp_path / 'outputs'))
    alice, alice_csrf = _client(store, 'alice')
    bob, bob_csrf = _client(store, 'bob')
    item = collect_email_conflict_items(audit)[0]
    url = f'/api/task/{task.task_id}/review/email'
    assert bob.post(url, json={'id': item['id'], 'selected_email': item['choices'][0]},
                    headers={'X-CSRF-Token': alice_csrf}).status_code == 403
    assert alice.post(url, json={'id': item['id'], 'selected_email': 'fake@example.edu'},
                      headers={'X-CSRF-Token': alice_csrf}).status_code == 409
    assert alice.post(url, json={'id': item['id'], 'selected_email': item['choices'][1]},
                      headers={'X-CSRF-Token': alice_csrf}).status_code == 204
    assert alice.post(f'/api/task/{task.task_id}/review/retry',
                      headers={'X-CSRF-Token': alice_csrf}).status_code == 200
