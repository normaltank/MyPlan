"""Site access decisions retain failed work until an explicit, gated retry."""
import json

from fastapi.testclient import TestClient
import pytest

from server import app as web
from server.access_control import AccountStore
from server.agent_queue import AgentTaskQueue


def setup_site(tmp_path, monkeypatch, *, field_review=False):
    queue = AgentTaskQueue(tmp_path / 'queue.db')
    store = AccountStore(queue.db_path)
    reviewer = store.sign_in('site-review-fixture')
    task = queue.submit_task('样本大学', '样本学院', 'https://college.example.edu/', owner_user_id=reviewer.id)
    root = tmp_path / 'outputs'
    attempt = root / 'tasks' / task.task_id / 'attempt-1'
    attempt.mkdir(parents=True)
    if field_review:
        from test_authenticated_image_review import _sample_audit
        attempt.rmdir()
        audit = _sample_audit(root, task)
    else:
        audit = attempt / 'old_audit.json'
        audit.write_text(json.dumps({'school': task.school, 'college': task.college,
            'audit_path': str(audit), 'unresolved': []}), encoding='utf8')
    records = [{'姓名': '旧观察', '简介': '保留的完整原始记录'}]
    with queue._connect() as con:
        queue._write_records(con, task.task_id, records)
        con.execute("UPDATE tasks SET status='failed',error='旧侦察未找到名录',stats_json=?,download_url=? WHERE task_id=?",
                    (json.dumps({'total': 1, 'delivery_ready': False}), '/download/old.xlsx', task.task_id))
    assert queue.record_official_site_issue(task.task_id, [{
        'url': task.url, 'method': 'browser_navigate', 'observed_at': '2026-10-11T00:10:00+00:00',
        'error_kind': 'connectionClosed'}])
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(root))
    client = TestClient(web.app)
    assert client.post('/api/auth/name', json={'display_name': reviewer.display_name}).status_code == 200
    csrf = client.get('/api/auth/me').json()['csrf_token']
    return queue, task, reviewer, client, {'X-CSRF-Token': csrf}, audit, records


@pytest.mark.parametrize('verdict', ['available', 'unavailable', 'unknown'])
def test_authenticated_site_review_retains_failed_records_and_never_queues(tmp_path, monkeypatch, verdict):
    queue, task, reviewer, client, headers, audit, records = setup_site(tmp_path, monkeypatch)
    original_audit = audit.read_bytes()
    issue = queue.get_task(task.task_id).stats['official_site_issue']
    page = client.get(f'/review/task/{task.task_id}/official-site')
    assert page.status_code == 200 and '官网异常' in page.text
    assert task.url in page.text and 'browser_navigate' in page.text and 'connectionClosed' in page.text
    assert '2026-10-11' in page.text and 'no-faculty' not in page.text
    endpoint = f'/api/task/{task.task_id}/review/official-site'
    payload = {'issue_id': issue['id'], 'verdict': verdict, 'reason': '人工查看访问情况'}
    assert TestClient(web.app).post(endpoint, json=payload).status_code == 401
    assert client.post(endpoint, json=payload).status_code == 403
    assert client.post(endpoint, json=payload, headers=headers).status_code == 204
    saved = queue.get_task(task.task_id)
    assert saved.status == 'failed' and saved.records == records and not saved.download_url
    assert saved.stats['official_site_issue']['review']['verdict'] == verdict
    assert saved.stats['official_site_issue']['review']['reviewed_by'] == reviewer.id
    assert not saved.stats.get('no_faculty') and not queue.get_pending_tasks()
    assert audit.read_bytes() == original_audit
    assert client.post(f'/api/task/{task.task_id}/no-faculty', headers=headers).status_code == 409
    data = client.get(f'/api/task/{task.task_id}').json()
    assert data['official_site_review_url'].endswith('/official-site') and not data.get('download_url')


def test_explicit_site_retry_keeps_existing_field_review_gate(tmp_path, monkeypatch):
    from server.image_bio_review import collect_review_items
    queue, task, _, client, headers, audit, records = setup_site(tmp_path, monkeypatch, field_review=True)
    before = audit.read_bytes()
    issue = queue.get_task(task.task_id).stats['official_site_issue']
    assert client.post(f'/api/task/{task.task_id}/review/official-site', json={
        'issue_id': issue['id'], 'verdict': 'available'}, headers=headers).status_code == 204
    retry = f'/api/task/{task.task_id}/official-site/retry'
    assert client.post(retry, headers=headers).status_code == 409
    assert queue.get_task(task.task_id).status == 'failed'
    [item] = collect_review_items(audit)
    assert client.post(f'/api/task/{task.task_id}/review/ocr', json={
        'id': item['id'], 'verified_text': '确认文字'}, headers=headers).status_code == 204
    assert client.post(retry, headers=headers).status_code == 200
    saved = queue.get_task(task.task_id)
    assert saved.status == 'queued' and saved.records == records and not saved.download_url
    assert saved.stats['official_site_issue']['review']['verdict'] == 'available'
    assert audit.read_bytes() == before and (audit.parent.parent / 'review-resume.json').is_file()


def test_access_issue_does_not_expand_person_404_or_unknown_source_purpose(tmp_path):
    queue = AgentTaskQueue(tmp_path / 'queue.db')
    task = queue.submit_task('样本大学', '样本学院', 'https://college.example.edu/')
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (task.task_id,))
    for observation in [
        {'url': 'https://college.example.edu/person/1', 'method': 'browser_navigate',
         'observed_at': '2026-10-11T00:10:00+00:00', 'http_status': 404},
        {'url': task.url, 'reason': 'source_relationship_unverified', 'status': 'pending',
         'method': 'browser_navigate', 'observed_at': '2026-10-11T00:10:00+00:00', 'error_kind': 'unknown purpose'},
    ]:
        with pytest.raises(ValueError):
            queue.record_official_site_issue(task.task_id, [observation])
    assert 'official_site_issue' not in queue.get_task(task.task_id).stats


@pytest.mark.parametrize('location', ['top_level', 'runtime_report'])
def test_known_roster_access_evidence_uses_core_source_disposition_contract(tmp_path, location):
    queue = AgentTaskQueue(tmp_path / 'queue.db')
    task = queue.submit_task('样本大学', '样本学院', 'https://college.example.edu/')
    evidence = {'url': 'https://college.example.edu/faculty', 'reason': 'source_unreadable',
                'status': 'unavailable', 'method': 'browser_navigate',
                'observed_at': '2026-10-11T00:10:00Z', 'http_status': 502,
                'error_kind': None, 'source_html_sha256': None,
                'original_path': None, 'browser_proof': None}
    evidence_stats = {'source_disposition_evidence': [evidence]}
    stats = evidence_stats if location == 'top_level' else {'runtime_report': evidence_stats}
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',stats_json=? WHERE task_id=?",
                    (json.dumps(stats), task.task_id))
    assert queue.record_official_site_issue(task.task_id, [evidence])
    saved = queue.get_task(task.task_id)
    assert all(saved.stats[key] == value for key, value in stats.items())
    assert saved.stats['official_site_issue']['observations'][0]['error'] == 'HTTP 502'
    if location == 'runtime_report':
        with queue._connect() as con:
            con.execute("UPDATE tasks SET stats_json=? WHERE task_id=?",
                        (json.dumps({**stats, 'source_disposition_evidence': []}), task.task_id))
        with pytest.raises(ValueError):
            queue.record_official_site_issue(task.task_id, [evidence])


def test_old_field_retry_cannot_bypass_unconfirmed_site_access(tmp_path, monkeypatch):
    from server.image_bio_review import collect_review_items
    queue, task, _, client, headers, audit, _ = setup_site(tmp_path, monkeypatch, field_review=True)
    [item] = collect_review_items(audit)
    assert client.post(f'/api/task/{task.task_id}/review/ocr', json={
        'id': item['id'], 'verified_text': '确认文字'}, headers=headers).status_code == 204
    assert client.post(f'/api/task/{task.task_id}/review/retry', headers=headers).status_code == 409
    assert not queue.retry_failed_review_task(task.task_id)
    assert queue.get_task(task.task_id).status == 'failed'


@pytest.mark.parametrize('field_review', [True, False])
def test_missing_audit_keeps_known_field_review_blocked_but_allows_pure_access_retry(tmp_path, monkeypatch, field_review):
    queue, task, _, client, headers, audit, records = setup_site(tmp_path, monkeypatch, field_review=field_review)
    stats = queue.get_task(task.task_id).stats
    if field_review:
        stats.update(failure_codes=['image_bio_low_confidence'],
                     failure_issues=[{'status': 'image_bio_low_confidence', 'url': task.url}])
        with queue._connect() as con:
            con.execute('UPDATE tasks SET stats_json=? WHERE task_id=?', (json.dumps(stats), task.task_id))
    issue = stats['official_site_issue']
    assert client.post(f'/api/task/{task.task_id}/review/official-site', json={
        'issue_id': issue['id'], 'verdict': 'available'}, headers=headers).status_code == 204
    original = audit.read_bytes()
    archived = audit.with_name('original-audit-kept.raw')
    audit.rename(archived)
    assert client.post(f'/api/task/{task.task_id}/review/retry', headers=headers).status_code == 404
    response = client.post(f'/api/task/{task.task_id}/official-site/retry', headers=headers)
    assert response.status_code == (409 if field_review else 200)
    saved = queue.get_task(task.task_id)
    assert saved.status == ('failed' if field_review else 'queued')
    assert saved.records == records and not saved.download_url
    assert archived.read_bytes() == original


@pytest.mark.parametrize('was_applied', [True, False])
def test_site_available_cannot_bypass_withdrawn_source_relationship(tmp_path, monkeypatch, was_applied):
    from server import source_review
    from server.faculty_extractor import FacultyCandidate
    from server.source_qualification import SourceEvidence
    queue, task, reviewer, client, headers, audit, records = setup_site(tmp_path, monkeypatch)
    original = audit.parent / 'source-original.html'
    original.write_bytes(b'<main>original organization membership</main>')
    candidate = FacultyCandidate('王芳', task.url, '讲师', '原生观察')
    [item] = source_review.build_items([candidate], {
        (candidate.name, task.url): {SourceEvidence(task.url,
            'organizational_membership' if was_applied else 'faculty_roster', 'verified', 'a' * 64)}},
        {}, {'school': task.school, 'college': task.college}, task.task_id,
        [{'url': task.url, 'path': str(original), 'kind': 'directory'}], [])
    decision = source_review.save_decision(audit.parent / source_review.DECISIONS_FILE, item, 'confirm',
        evidence_basis='official_source', reason='合成测试逐人核验', source_refs=[task.url], reviewed_by=reviewer.id)
    audit.write_text(json.dumps({'task_id': task.task_id, 'school': task.school, 'college': task.college,
        'audit_path': str(audit), 'unresolved': [], 'source_review_items': [item],
        'source_relationship_reviews': [decision] if was_applied else []}), encoding='utf8')
    before = audit.read_bytes()
    issue = queue.get_task(task.task_id).stats['official_site_issue']
    assert client.post(f'/api/task/{task.task_id}/review/official-site',
        json={'issue_id': issue['id'], 'verdict': 'available'}, headers=headers).status_code == 204
    assert client.post(f'/api/task/{task.task_id}/review/source-relationship', json={
        'id': item['id'], 'verdict': 'revoked', 'evidence_basis': 'official_source',
        'reason': '合成撤回测试', 'source_refs': [task.url]}, headers=headers).status_code == 204
    assert client.post(f'/api/task/{task.task_id}/official-site/retry', headers=headers).status_code == 409
    assert queue.get_task(task.task_id).status == 'failed'
    assert queue.get_task(task.task_id).records == records
    assert audit.read_bytes() == before


@pytest.mark.browser
@pytest.mark.asyncio
async def test_site_page_save_does_not_queue_and_button_requests_retry_explicitly(tmp_path, monkeypatch):
    from playwright.async_api import async_playwright
    queue, task, _, client, _, _, records = setup_site(tmp_path, monkeypatch)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        async def route(request):
            req = request.request
            body = json.loads(req.post_data) if req.post_data else None
            response = client.request(req.method, req.url.removeprefix('http://testserver'),
                json=body, headers={'X-CSRF-Token': req.headers.get('x-csrf-token', '')})
            await request.fulfill(status=response.status_code, body=response.content,
                                  content_type=response.headers.get('content-type', 'text/plain'))
        await context.route('http://testserver/**', route)
        page = await context.new_page()
        await page.goto(f'http://testserver/review/task/{task.task_id}/official-site')
        assert await page.locator('h2').inner_text() == '官网异常，待人工确认'
        await page.locator('#verdict').select_option('available')
        await page.locator('button[type=submit]').click()
        await page.wait_for_function("document.querySelector('#status').textContent.includes('不会自动入队')")
        assert queue.get_task(task.task_id).status == 'failed'
        await page.locator('#retry').click()
        await page.wait_for_function("document.querySelector('#status').textContent.includes('进入原采集队列')")
        assert queue.get_task(task.task_id).status == 'queued'
        assert queue.get_task(task.task_id).records == records
        await browser.close()
