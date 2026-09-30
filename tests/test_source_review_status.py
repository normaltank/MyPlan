"""An unresolved PDF roster stays blocked and exposes its original sources."""

import json

from server import app as web
from server.agent_queue import AgentTaskQueue


def test_source_review_exposes_links_without_claiming_a_complete_collection(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    task = queue.submit_task('测试大学', '测试学院', 'https://example.edu/entry')
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',error='PDF名录待核验' WHERE task_id=?", (task.task_id,))
    root = tmp_path / 'outputs'
    path = root / 'tasks' / task.task_id / 'attempt-1' / 'sample_audit.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'school': task.school, 'college': task.college,
        'audit_path': str(path), 'delivery_ready': False, 'unresolved': [
            {'status': 'embedded_pdf_roster_requires_review', 'url': 'https://example.edu/mentors',
             'media_urls': ['https://example.edu/mentors.pdf', 'javascript:alert(1)', '/private/file'],
             'private_page_text': '原始材料仅留本机'}]}), encoding='utf-8')
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(root))
    data = web.present_task(queue.get_task(task.task_id).to_dict(),
                            {'state': 'blocked', 'identity_conflicts': []})
    assert data['status'] == 'failed'
    assert data['source_review_required'] is True
    assert data['source_review_urls'] == ['https://example.edu/mentors', 'https://example.edu/mentors.pdf']
    assert not data.get('download_url')
    assert 'review_url' not in data  # there is no automatic approval for an unread PDF
    assert '原始材料仅留本机' not in json.dumps(data, ensure_ascii=False)


def test_a_failed_collection_does_not_imply_a_source_review(tmp_path, monkeypatch):
    queue = AgentTaskQueue(tmp_path / 'tasks.db')
    task = queue.submit_task('测试大学', '测试学院', 'https://example.edu/entry')
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(tmp_path / 'outputs'))
    data = web.present_task(queue.get_task(task.task_id).to_dict(),
                            {'state': 'blocked', 'identity_conflicts': []})
    assert not data.get('source_review_required')
