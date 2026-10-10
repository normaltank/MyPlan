import json
from hashlib import sha256

import pymupdf

from server.image_bio_review import collect_email_conflict_items, email_conflict_id
from server.image_biography import scan_media


def native_audit(tmp_path):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((30, 40), '张富有 教授', fontname='china-s', fontsize=12)
        page.insert_text((30, 70), '邮箱 123456@qq.com', fontname='china-s', fontsize=12)
        page.insert_text((30, 100), '邮箱 234567@163.com', fontname='china-s', fontsize=12)
        original = document.tobytes()
    digest = sha256(original).hexdigest()
    original_path = tmp_path / 'original.pdf'
    original_path.write_bytes(original)
    scanned = scan_media(original, 'pdf')
    assert scanned['pages'][0]['source'] == 'pdf_text_layer'
    assert scanned['pages'][0]['lines'] == []
    scanned['source_sha256'] = digest
    ocr_path = tmp_path / 'scan.json'
    ocr_path.write_text(json.dumps(scanned, ensure_ascii=False), encoding='utf8')
    profile = 'https://faculty.example.edu/person'
    evidence = {'profile_url': profile, 'assigned_name': '张富有', 'kind': 'pdf',
                'media_url': 'https://faculty.example.edu/cv.pdf', 'sha256': digest,
                'original_path': str(original_path), 'ocr_path': str(ocr_path)}
    records_path = tmp_path / 'records.json'
    records_path.write_text(json.dumps([{'姓名': '张富有', '主页': profile, '邮箱': ''}],
                                       ensure_ascii=False), encoding='utf8')
    audit_path = tmp_path / 'native_audit.json'
    audit_path.write_text(json.dumps({'records_path': str(records_path),
        'image_biography_evidence': [evidence], 'unresolved': [
            {'url': profile, 'name': '张富有', 'status': 'image_bio_email_conflict'}]},
        ensure_ascii=False), encoding='utf8')
    return audit_path, scanned, evidence


def test_native_pdf_email_conflict_has_original_crops_and_same_runtime_id(tmp_path):
    audit_path, scanned, evidence = native_audit(tmp_path)
    items = collect_email_conflict_items(audit_path)
    assert len(items) == 1
    item = items[0]
    assert item['choices'] == ['123456@qq.com', '234567@163.com']
    assert all(item['crops'])
    assert item['id'] == email_conflict_id(evidence['profile_url'], '张富有', scanned['text'].strip(),
                                          [evidence], '', item['choices'])


def test_native_pdf_email_review_ui_decision_and_resume_use_runtime_evidence(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import app as web, review_registry
    from server.access_control import AccountStore
    from server.agent_queue import AgentTaskQueue
    from server.image_bio_review import resolve_email_conflict
    from server.task_image_review import carry_review_decisions
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    store = AccountStore(queue.db_path)
    owner = store.sign_in('review-test-owner')
    task = queue.submit_task('样本大学', '样本学院', 'https://faculty.example.edu', owner_user_id=owner.id)
    root = tmp_path / 'outputs' / 'tasks' / task.task_id
    attempt = root / 'attempt-1'
    attempt.mkdir(parents=True)
    audit_path, _, evidence = native_audit(attempt)
    payload = json.loads(audit_path.read_text(encoding='utf8'))
    payload.update(school=task.school, college=task.college, audit_path=str(audit_path))
    audit_path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf8')
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',error='审计未通过交付门禁' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(tmp_path / 'outputs'))
    client = TestClient(web.app)
    assert client.post('/api/auth/name', json={'display_name': 'review-test-owner'}).status_code == 200
    csrf = client.get('/api/auth/me').json()['csrf_token']
    items = review_registry.collect(audit_path, 'image_bio_email')['image_bio_email']
    assert len(items) == 1 and not review_registry.review_complete(audit_path)
    item = items[0]
    response = client.get(f'/review/task/{task.task_id}')
    assert response.status_code == 200 and '123456@qq.com' in response.text and '234567@163.com' in response.text
    assert 'data:image/png;base64,' in response.text
    selected = item['choices'][1]  # Synthetic fixture only; never select a real teacher address.
    assert client.post(f'/api/task/{task.task_id}/review/email',
        json={'id': item['id'], 'selected_email': selected},
        headers={'X-CSRF-Token': csrf}).status_code == 204
    assert review_registry.review_complete(audit_path)
    assert client.post(f'/api/task/{task.task_id}/review/retry', headers={'X-CSRF-Token': csrf}).status_code == 200
    target = root / 'attempt-2'
    carry_review_decisions(root, target)
    assert resolve_email_conflict(target / 'image_bio_email_reviews.json', item['id'], item['choices']) == selected
    assert resolve_email_conflict(target / 'image_bio_email_reviews.json',
        email_conflict_id(evidence['profile_url'], '张富有', 'changed original biography', [evidence], '', item['choices']),
        item['choices']) is None
