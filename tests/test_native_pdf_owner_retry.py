"""Old native PDF owner errors may retry only after the current reader proves them."""
from hashlib import sha256
import json

from fastapi.testclient import TestClient
from PIL import Image
import pymupdf
import pytest

from server import app as web, review_registry
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue
from server.image_biography import scan_media


@pytest.mark.parametrize('case', ['restored', 'unknown_owner', 'low_confidence',
                                  'source_changed', 'source_sha_missing'])
def test_native_pdf_owner_retry_recomputes_the_original_before_queueing(tmp_path, monkeypatch, case):
    queue = AgentTaskQueue(tmp_path / 'queue.db')
    store = AccountStore(queue.db_path)
    user = store.sign_in('fixture-owner')
    task = queue.submit_task('样本大学', '样本学院', 'https://faculty.example.edu',
                             owner_user_id=user.id)
    root = tmp_path / 'outputs'
    attempt = root / 'tasks' / task.task_id / 'attempt-1'
    attempt.mkdir(parents=True)
    profile, media = 'https://faculty.example.edu/person', 'https://faculty.example.edu/cv.pdf'
    with pymupdf.open() as document:
        page = document.new_page()
        text = ['【个人简介】张富有，副教授，博', '士（后），博士生导师。', '完整学习和科研履历。']
        if case == 'unknown_owner':
            text.extend(['哔甲简历（word）', '另一个人的履历。'])
        for index, line in enumerate(text):
            page.insert_text((60, 70 + index * 25), line, fontname='china-s', fontsize=12)
        original = document.tobytes()
    digest = sha256(original).hexdigest()
    original_path = attempt / 'original.pdf'
    original_path.write_bytes(original)
    scanned = scan_media(original, 'pdf')
    scanned['source_sha256'] = digest
    if case == 'source_sha_missing':
        del scanned['source_sha256']
    scan_path = attempt / 'scan.json'
    scan_path.write_text(json.dumps(scanned, ensure_ascii=False), encoding='utf8')
    issues = [{'status': 'image_bio_owner_unresolved', 'profile_url': profile, 'media_url': media},
              {'status': 'image_bio_unreadable_profile', 'url': profile}]
    evidence = [{'kind': 'pdf', 'profile_url': profile, 'media_url': media,
                 'sha256': digest, 'original_path': str(original_path), 'ocr_path': str(scan_path)}]
    if case == 'low_confidence':
        # A separate image text problem still needs a person, even when the PDF owner is clear.
        image_url = 'https://faculty.example.edu/image.png'
        image_path, image_scan_path = attempt / 'image.png', attempt / 'image_scan.json'
        Image.new('RGB', (200, 100), 'white').save(image_path)
        image_digest = sha256(image_path.read_bytes()).hexdigest()
        image_scan_path.write_text(json.dumps({'kind': 'image', 'source_sha256': image_digest,
            'pages': [{'page': 1, 'source': 'local_ocr', 'text': '待核文字', 'lines': [
                {'text': '待核文字', 'confidence': .55,
                 'box': [[10, 10], [150, 10], [150, 40], [10, 40]]}]}], 'text': '待核文字'},
            ensure_ascii=False), encoding='utf8')
        evidence.append({'kind': 'image', 'profile_url': profile, 'media_url': image_url,
                         'sha256': image_digest, 'original_path': str(image_path),
                         'ocr_path': str(image_scan_path)})
        issues.append({'status': 'image_bio_low_confidence', 'media_url': image_url})
    audit_path = attempt / 'native_audit.json'
    audit_path.write_text(json.dumps({'school': task.school, 'college': task.college,
        'audit_path': str(audit_path), 'unresolved': issues,
        'eligibility': [{'status': 'eligible', 'name': '张富有', 'profile_url': profile}],
        'image_biography_evidence': evidence},
        ensure_ascii=False), encoding='utf8')
    if case == 'source_changed':
        with pymupdf.open(stream=original, filetype='pdf') as document:
            document[0].insert_text((60, 300), 'source changed')
            original_path.write_bytes(document.tobytes())
    before = {path: path.read_bytes() for path in (audit_path, original_path, scan_path)}
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',error='审计未通过交付门禁' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(root))
    client = TestClient(web.app)
    assert client.post('/api/auth/name', json={'display_name': 'fixture-owner'}).status_code == 200
    csrf = client.get('/api/auth/me').json()['csrf_token']
    if case == 'restored':
        assert review_registry.collect(audit_path, 'image_bio_text')['image_bio_text'] == []
        assert review_registry.review_complete(audit_path)
    elif case == 'low_confidence':
        [item] = review_registry.collect(audit_path, 'image_bio_text')['image_bio_text']
        assert item['media_url'] == image_url and item['confidence'] == .55
    response = client.post(f'/api/task/{task.task_id}/review/retry', headers={'X-CSRF-Token': csrf})
    assert response.status_code == (200 if case == 'restored' else 409)
    assert queue.get_task(task.task_id).status == ('queued' if case == 'restored' else 'failed')
    assert all(path.read_bytes() == value for path, value in before.items())
    assert not list(attempt.glob('*reviews.json'))
