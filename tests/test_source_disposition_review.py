"""Source-only reviews must unblock a retry without qualifying their people."""
import hashlib
import json
from pathlib import Path

import pytest

from server import source_disposition_review as review, review_registry


def fixture(tmp_path, *, unreadable=False, task_id='task', school='样本大学', college='样本学院'):
    root = tmp_path / 'tasks' / task_id / 'attempt-1'
    root.mkdir(parents=True)
    original = root / 'source.html'
    original.write_text('<main>样本大学 样本学院 机构设置，成员包括王芳</main>', encoding='utf-8')
    plan = {'task_id': task_id, 'school': school, 'college': college}
    source = {'url': 'https://example.edu/structure', 'reason': 'source_structure_unresolved',
              'status': 'pending', 'source_html_sha256': hashlib.sha256(original.read_bytes()).hexdigest(),
              'original_path': str(original), 'http_status': 200, 'error_kind': None}
    if unreadable:
        source.update(original_path=None, source_html_sha256=None, http_status=503, error_kind='HTTP503')
    audit = root / 'college_audit.json'
    payload = {**plan, 'audit_path': str(audit), 'source_disposition_evidence': [source],
               'unresolved': [{'url': source['url'], 'status': source['reason']}]}
    audit.write_text(json.dumps(payload), encoding='utf-8')
    return audit, payload, review.collect_items(audit)[0]


def test_only_source_items_render_and_unknown_never_settles_retry(tmp_path):
    audit, _, item = fixture(tmp_path)
    assert review_registry.has_review(audit)
    assert not review_registry.review_complete(audit)
    review.save_decision(audit.parent, item, 'unknown', '不能确认是否还有师资入口', 7)
    assert not review_registry.review_complete(audit)
    review.save_decision(audit.parent, item, 'exclude', '原件明确为机构设置页，不是师资名录', 7)
    assert review_registry.review_complete(audit)
    review.save_decision(audit.parent, item, 'revoked', '误选后撤回，须重新核验', 7)
    assert not review_registry.review_complete(audit)


def test_source_purpose_choice_does_not_require_a_redundant_free_text_note(tmp_path):
    audit, _, item = fixture(tmp_path)
    decision = review.save_decision(audit.parent, item, 'exclude', '', 7)
    assert decision['reason'] == review.DECISION_LABELS['exclude']
    assert decision['note'] == ''
    assert decision['binding'] == item['binding'] and decision['reviewed_by'] == 7
    assert review_registry.review_complete(audit)
    review.save_decision(audit.parent, item, 'unknown', '', 7)
    assert not review_registry.review_complete(audit)


@pytest.mark.parametrize('change', ['raw', 'college', 'url', 'role', 'task'])
def test_exclusion_never_survives_a_changed_source_or_target(tmp_path, change):
    audit, payload, item = fixture(tmp_path)
    decision = review.save_decision(audit.parent, item, 'exclude', '已核验来源用途', 7)
    if change == 'raw':
        Path(item['original_path']).write_text('新页面现在显示师资名单', encoding='utf-8')
    elif change == 'college':
        payload['college'] = '其他学院'
    elif change == 'task':
        payload['task_id'] = 'other-task'
    else:
        payload['source_disposition_evidence'][0]['url' if change == 'url' else 'source_role'] = 'https://example.edu/new' if change == 'url' else 'faculty_roster'
    audit.write_text(json.dumps(payload), encoding='utf-8')
    assert review.valid_decision(review.collect_items(audit)[0], decision) is None
    assert not review_registry.review_complete(audit)


def test_unavailable_original_can_request_inspection_but_cannot_prove_exclusion(tmp_path):
    audit, _, item = fixture(tmp_path, unreadable=True)
    with pytest.raises(ValueError):
        review.save_decision(audit.parent, item, 'exclude', '打不开', 7)
    review.save_decision(audit.parent, item, 'recon', '官网恢复后重新检查', 7)
    assert review_registry.review_complete(audit)


@pytest.mark.parametrize('reason', ['source_unreadable', 'source_structure_unresolved'])
def test_saved_http503_error_html_never_justifies_exclusion(tmp_path, reason):
    audit, payload, _ = fixture(tmp_path)
    source = payload['source_disposition_evidence'][0]
    source.update(reason=reason, http_status=503, error_kind='HTTP503')
    payload['unresolved'] = [{'url': source['url'], 'status': reason}]
    audit.write_text(json.dumps(payload), encoding='utf-8')
    item = review.collect_items(audit)[0]
    assert item['original_path'] and not review.can_exclude(item)
    with pytest.raises(ValueError):
        review.save_decision(audit.parent, item, 'exclude', '错误页不是师资名单', 7)
    html = review.render_section([item], endpoint='/save', artifact_endpoint='/original', csrf='token')
    assert 'data-verdict="exclude" disabled' in html
    review.save_decision(audit.parent, item, 'recon', '网站恢复后重新检查', 7)
    issues, applied = review.apply_decisions(payload, payload['source_disposition_evidence'], payload['unresolved'], audit.parent, 'task')
    assert issues == payload['unresolved'] and not applied


def test_rerecon_intent_is_retained_but_does_not_remove_the_native_issue(tmp_path):
    audit, payload, item = fixture(tmp_path)
    entry = 'https://example.edu/faculty'
    review.save_decision(audit.parent, item, 'recon', '需要沿正式师资入口检查', 7, entry)
    assert review_registry.review_complete(audit)
    from server.task_image_review import prepare_review_resume
    prepare_review_resume(audit, 'task')
    feedback = review.recon_feedback(audit.parent.parent, 'task')
    assert feedback['known_urls'] == [item['url'], entry]
    issues, applied = review.apply_decisions(payload, payload['source_disposition_evidence'],
                                            payload['unresolved'], audit.parent, 'task')
    assert issues == payload['unresolved'] and applied == []


def test_exclusion_settles_only_this_page_without_any_person_decision(tmp_path):
    audit, payload, item = fixture(tmp_path)
    review.save_decision(audit.parent, item, 'exclude', '机构组织页原件已核对', 7)
    other = {'url': 'https://example.edu/other', 'status': 'source_structure_unresolved'}
    person = {'url': item['url'], 'name': '王芳', 'status': 'source_qualification_missing'}
    issues, applied = review.apply_decisions(payload, payload['source_disposition_evidence'],
                                            [*payload['unresolved'], other, person], audit.parent, 'task')
    assert issues == [other, person] and len(applied) == 1


def test_authenticated_source_only_page_original_and_save(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import app as web
    from server.access_control import AccountStore
    from server.agent_queue import AgentTaskQueue
    from test_authenticated_image_review import _client
    audit, payload, _ = fixture(tmp_path)
    queue = AgentTaskQueue(tmp_path / 'queue.db')
    store = AccountStore(queue.db_path)
    owner = store.sign_in('alice')
    task = queue.submit_task(payload['school'], payload['college'], 'https://example.edu/', owner_user_id=owner.id)
    with queue._connect() as con:
        con.execute("UPDATE tasks SET task_id='task',status='failed' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(tmp_path))
    client, csrf = _client(store, 'alice')
    page = client.get('/review/task/task')
    assert page.status_code == 200 and '来源页面用途与结构复核' in page.text
    item = review.collect_items(audit)[0]
    endpoint = '/api/task/task/review/source-disposition'
    body = {'id': item['id'], 'verdict': 'exclude', 'reason': '逐页核对机构设置原文'}
    assert TestClient(web.app).post(endpoint, json=body).status_code == 401
    assert client.post(endpoint, json=body).status_code == 403
    headers = {'X-CSRF-Token': csrf}
    assert client.post(endpoint, json={**body, 'teacher_name': '王芳'}, headers=headers).status_code == 422
    assert client.post(endpoint, json=body, headers=headers).status_code == 204
    assert client.post(endpoint, json={'id': item['id'], 'verdict': 'exclude'}, headers=headers).status_code == 204
    original = client.get('/api/task/task/review/disposition-original', params={'item_id': item['id']})
    assert original.status_code == 200 and original.headers['content-type'].startswith('text/plain')
    assert '机构设置' in original.text and original.headers['x-content-type-options'] == 'nosniff'
    applied = review.valid_decision(review.collect_items(audit)[0], review.review_store.load(review.decision_path(audit.parent, 'task'))[item['id']])
    payload.update(unresolved=[], source_disposition_reviews=[applied])
    audit.write_text(json.dumps(payload), encoding='utf-8')
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='completed',download_url='/saved.xlsx',stats_json=? WHERE task_id='task'",
                    (json.dumps({'source_disposition_reviews': [applied], 'delivery_ready': True, 'total': 1}),))
        con.execute("INSERT INTO records(task_id,seq,name,title,email,bio,profile_url,payload_json) VALUES(?,?,?,?,?,?,?,?)",
                    ('task', 1, '王芳', '教授', '', '原文完整简介', 'https://example.edu/profile', json.dumps({'姓名': '王芳'})))
    before = queue.get_task('task').records
    presented = web.present_task(queue.get_task('task').to_dict(), {'state': 'ready', 'identity_conflicts': []})
    assert presented['source_disposition_review_url'] == '/review/task/task/source-disposition'
    editor = client.get('/review/task/task/source-disposition')
    assert editor.status_code == 200 and '撤回原决定' in editor.text
    from bs4 import BeautifulSoup
    assert BeautifulSoup(editor.text, 'html.parser').select_one('.disposition-reason').get_text() == ''
    assert client.post(endpoint, json={**body, 'verdict': 'revoked', 'reason': '误选后撤回'}, headers=headers).status_code == 204
    assert queue.get_task('task').status == 'failed' and not queue.get_task('task').download_url
    assert queue.get_task('task').records == before
    assert review_registry.has_review(audit) and not review_registry.review_complete(audit)
    assert client.get('/review/task/task').status_code == 200
    assert client.post(endpoint, json=body, headers=headers).status_code == 204
    assert review_registry.review_complete(audit)
    monkeypatch.setattr(web, 'prepare_review_resume', lambda path, task_id: None)
    assert client.post('/api/task/task/review/retry', headers=headers).status_code == 200


@pytest.mark.asyncio
@pytest.mark.verified_source_boundary
async def test_collection_request_runs_discovery_with_freshly_reverified_prior_branches(tmp_path, monkeypatch):
    from test_worker_resume_cache import worker_and_task, recon_result, completed_report
    from server import agent_worker as worker_module
    from server.task_image_review import prepare_review_resume
    from source_contract_fixture import upgrade
    worker, task, root = worker_and_task(tmp_path)
    audit, _, item = fixture(worker.output_dir, task_id=task.task_id, school=task.school, college=task.college)
    review.save_decision(audit.parent, item, 'recon', '来源的人员列表需要重新查明', 7)
    prepare_review_resume(audit, task.task_id)
    prior = recon_result()
    upgrade(prior.plan)
    prior.plan['source_dispositions'] = [{'url': item['url'], 'reason': 'source_structure_unresolved'}]
    monkeypatch.setattr(worker, '_resume_plan', lambda target, directory: prior)
    async def forbidden_cache(*args):
        pytest.fail('an explicit collection request cannot use the unchanged cached plan')
    monkeypatch.setattr(worker, '_cached_recon', forbidden_cache)
    validated = []
    async def reverify(plan, feedback, directory):
        validated.append((plan, feedback))
        return {'https://example.edu/list': {'branch': plan['branches'][0], 'events': []}}
    monkeypatch.setattr(worker, '_unaffected_branches', reverify)
    calls = []
    async def scout(school, college, entry, **kwargs):
        calls.append(kwargs)
        result = recon_result()
        upgrade(result.plan)
        return result
    monkeypatch.setattr(worker_module, 'reconnoiter', scout)
    async def capture(plan, directory):
        return completed_report(directory)
    monkeypatch.setattr(worker, '_run_browser_plan', capture)
    await worker._harvest_with_repair(task, root)
    assert len(calls) == 1 and validated
    assert calls[0]['feedback']['known_urls'] == [item['url']]
    assert calls[0]['feedback']['previous_plan'] == prior.plan
    assert calls[0]['verified_branches']['https://example.edu/list']['branch'] == prior.plan['branches'][0]


@pytest.mark.browser
@pytest.mark.asyncio
async def test_source_disposition_buttons_submit_only_purpose_intent_and_can_be_changed(tmp_path):
    from playwright.async_api import async_playwright
    _, _, item = fixture(tmp_path)
    endpoint = 'https://review.example.edu/disposition'
    html = review.render_section([item], endpoint=endpoint, artifact_endpoint=endpoint + '/original', csrf='token')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            calls, errors = [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            async def respond(route):
                calls.append((route.request.post_data_json, route.request.headers.get('x-csrf-token')))
                if route.request.post_data_json['verdict'] == 'exclude':
                    await route.fulfill(status=409, json={'detail': '保存证据已变化'})
                else:
                    await route.fulfill(status=204)
            await page.route(endpoint, respond)
            await page.set_content('<html>' + html + '</html>')
            await page.locator('.disposition-reason').fill('原件说明仍需检查列表结构')
            await page.locator('.disposition-entry').fill('https://example.edu/faculty')
            await page.locator('button[data-verdict="recon"]').click()
            await page.locator('.status', has_text='已保存重新侦察请求').wait_for()
            await page.locator('button[data-verdict="unknown"]').click()
            await page.locator('.status', has_text='仍待复核，可改选').wait_for()
            await page.locator('button[data-verdict="exclude"]').click()
            await page.locator('.status', has_text='未保存：保存证据已变化').wait_for()
            assert [call[0]['verdict'] for call in calls] == ['recon', 'unknown', 'exclude']
            assert [call[0]['entry_url'] for call in calls] == ['https://example.edu/faculty', '', '']
            assert all(set(call[0]) == {'id', 'verdict', 'reason', 'entry_url'} and call[1] == 'token' for call in calls)
            assert not errors
        finally:
            await browser.close()
