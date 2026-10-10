"""Per-person relation decisions survive native retries only for exact evidence."""
import json
from pathlib import Path

import pytest

from server import source_review, review_registry
from server.faculty_extractor import FacultyCandidate
from server.source_qualification import SourceEvidence
from server.task_image_review import prepare_review_resume, carry_review_decisions


def fixture(tmp_path, *, name='王芳', role='organizational_membership'):
    task_id = 'source-task'
    out = tmp_path/task_id/'attempt-1'
    out.mkdir(parents=True)
    url = 'https://example.edu/members'
    html = '<main><p>王芳、李华，组织成员原文。</p></main>'
    original = out/'original.html'
    original.write_bytes(html.encode())
    candidate = FacultyCandidate(name, url, '讲师', '原始组织名单中的本人观察')
    sources = {(candidate.name, url): {SourceEvidence(url, role, 'verified', 'a'*64)}}
    plan = {'school': '样本大学', 'college': '样本学院'}
    items = source_review.build_items([candidate], sources, {}, plan, task_id,
                                      [{'kind': 'directory', 'url': url, 'path': str(original)}], [])
    audit = out/'sample_audit.json'
    audit.write_text(json.dumps({**plan, 'task_id': task_id, 'audit_path': str(audit), 'source_review_items': items,
        'unresolved': [{'status': source_review.STATUS, 'name': name, 'url': url}]}), encoding='utf8')
    return candidate, sources, plan, items[0], audit


def save(item, audit, verdict='confirm', basis='official_source'):
    return source_review.save_decision(audit.parent/source_review.DECISIONS_FILE, item, verdict,
        evidence_basis=basis, reason='逐人核对本学院身份的依据', source_refs=[item['profile_url']], reviewed_by=7)


def test_registry_save_prepare_carry_native_disposition_keeps_original_fields(tmp_path):
    from server.harvest_eligibility import classify_candidates
    candidate, sources, plan, item, audit = fixture(tmp_path)
    assert review_registry.has_review(audit) and not review_registry.review_complete(audit)
    save(item, audit)
    assert review_registry.review_complete(audit)
    prepare_review_resume(audit, item['binding']['task_id'])
    target = audit.parent.parent/'attempt-2'
    carry_review_decisions(audit.parent.parent, target)
    assert (target/source_review.DECISIONS_FILE).is_file()
    admitted, decisions, unresolved = classify_candidates([candidate], sources, {}, plan)
    admitted, decisions, unresolved, applied = source_review.apply_decisions(
        [candidate], admitted, decisions, unresolved, [item], target, item['binding']['task_id'])
    assert admitted == [candidate] and not unresolved and applied[0]['reviewed_by'] == 7
    assert candidate.name == '王芳' and candidate.title == '讲师' and candidate.card_context == '原始组织名单中的本人观察'
    assert decisions[0]['evidence_kind'] == 'human_confirmed_relationship'


@pytest.mark.parametrize('change', ['name', 'task', 'target', 'scope', 'raw', 'contract', 'withdrawal', 'artifact'])
def test_decision_never_survives_changed_observation_target_source_or_withdrawal(tmp_path, change):
    _, _, _, item, audit = fixture(tmp_path)
    decision = save(item, audit)
    if change == 'withdrawal':
        revoked = save(item, audit, verdict='revoked')
        assert source_review.valid_decision(item, revoked) is None
        assert not review_registry.review_complete(audit)
        return
    if change == 'artifact':
        Path(item['artifacts'][0]['path']).write_bytes(b'new original')
    else:
        replacement = json.loads(json.dumps(item))
        field = {'name': 'candidate_name', 'task': 'task_id', 'target': 'college', 'scope': 'sources',
                 'raw': 'native_sha256', 'contract': 'contract'}[change]
        replacement['binding'][field] = 'changed'
        replacement['id'] = source_review.item_id(replacement)
        item = replacement
    assert source_review.valid_decision(item, decision) is None


def test_attestation_stays_human_unknown_stays_blocked_and_one_person_never_qualifies_a_peer(tmp_path):
    from server.harvest_eligibility import classify_candidates
    candidate, sources, plan, item, audit = fixture(tmp_path)
    save(item, audit, basis='reviewer_attestation')
    peer = FacultyCandidate('李华', candidate.url, '教授')
    sources[(peer.name, peer.url)] = set(sources[(candidate.name, candidate.url)])
    admitted, decisions, unresolved = classify_candidates([candidate, peer], sources, {}, plan)
    admitted, decisions, unresolved, _ = source_review.apply_decisions(
        [candidate, peer], admitted, decisions, unresolved, [item], audit.parent, item['binding']['task_id'])
    assert admitted == [candidate] and unresolved[0]['name'] == '李华'
    assert decisions[0]['evidence_kind'] == 'human_attested_relationship'
    save(item, audit, verdict='unknown')
    assert not review_registry.review_complete(audit)


def test_review_source_artifacts_and_teacher_input_schema_are_not_replaceable(tmp_path):
    from server.app import SourceRelationshipRequest
    from pydantic import ValidationError
    _, _, _, item, audit = fixture(tmp_path)
    with pytest.raises(ValueError):
        source_review.save_decision(audit.parent/source_review.DECISIONS_FILE, item, 'confirm',
            evidence_basis='official_source', reason='同名', source_refs=['https://example.edu/unseen'], reviewed_by=1)
    with pytest.raises(ValidationError):
        SourceRelationshipRequest(id=item['id'], verdict='confirm', evidence_basis='official_source',
                                  reason='证据', teacher_title='教授')


@pytest.mark.asyncio
async def test_authenticated_ui_save_retry_carry_then_real_native_pipeline(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import app as web, browser_batch_tool as batch
    from server.access_control import AccountStore
    from server.agent_queue import AgentTaskQueue
    from server.harvest_workflow import CapturedRosterPage, SourceCapture
    from server.source_qualification import witness
    queue = AgentTaskQueue(tmp_path/'tasks.db')
    task = queue.submit_task('样本大学', '样本学院', 'https://example.edu/members')
    store = AccountStore(queue.db_path)
    root = tmp_path/'outputs'
    out = root/'tasks'/task.task_id/'attempt-1'
    html = '<title>组织成员</title><h1>组织成员</h1><header>样本大学样本学院</header><main><p>王芳 教授</p></main>'
    branch = {'url': task.url, 'source_role': 'organizational_membership', 'source_container_selector': 'main',
              'role_evidence_selector': 'h1', 'source_context_selector': 'header', 'card_selector': 'main > p', 'single_page': True}
    plan = {'school': task.school, 'college': task.college, 'branches': [branch]}
    proof = {**witness(html, task.url, branch, plan), 'confirmed': True, 'dom_stable': True, 'network_idle': True, 'witness_visible': True}
    async def capture(*args):
        return SourceCapture(roster_pages=[CapturedRosterPage(task.url, html, branch, source_role_evidence=proof)],
            branches=[{'url': task.url, 'end': 'single_page_no_pagination_control_seen', 'visited_pages': 1}]), {}
    async def profiles(*args, **kwargs):
        return {}, []
    monkeypatch.setattr(batch, '_capture_sources_with_state', capture)
    monkeypatch.setattr(batch, 'capture_profiles', profiles)
    first = await batch.run_plan(plan, object(), out, task_id=task.task_id)
    assert not first['delivery_ready'] and first['candidate_count'] == 1
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed',error='来源关系未核' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(root))
    client = TestClient(web.app)
    assert client.get('/review/task/'+task.task_id).status_code == 401
    assert client.post('/api/auth/name', json={'display_name': '人工审核员'}).status_code == 200
    headers = {'X-CSRF-Token': client.get('/api/auth/me').json()['csrf_token']}
    page = client.get('/review/task/'+task.task_id)
    assert page.status_code == 200 and '学院师资关系' in page.text and '整份委员会' in page.text
    item, = source_review.collect_items(Path(first['audit_path']))
    endpoint = '/api/task/'+task.task_id+'/review/source-relationship'
    payload = {'id': item['id'], 'verdict': 'confirm', 'evidence_basis': 'official_source',
               'reason': '逐人核对官网原件中的身份', 'source_refs': [task.url]}
    assert client.post(endpoint, json=payload).status_code == 403
    assert client.post(endpoint, headers=headers, json={**payload, 'title': '教授'}).status_code == 422
    assert client.post(endpoint, headers=headers, json=payload).status_code == 204
    original = client.get('/api/task/'+task.task_id+'/review/source-original', params={'item_id': item['id'], 'index': 0})
    assert original.status_code == 200 and original.headers['content-type'].startswith('text/plain')
    assert client.post('/api/task/'+task.task_id+'/review/retry', headers=headers).status_code == 200
    target = out.parent/'attempt-2'
    carry_review_decisions(out.parent, target)
    second = await batch.run_plan(plan, object(), target, task_id=task.task_id)
    assert second['delivery_ready'] and second['total'] == 1
    records = json.loads(Path(second['records_path']).read_text(encoding='utf8'))
    assert records[0]['姓名'] == '王芳' and records[0]['职称'] == '教授'
    assert second['eligibility'][0]['evidence_kind'] == 'human_confirmed_relationship'
    claimed = queue.claim_task('review-native', task_id=task.task_id)
    queue.complete_task(task.task_id, records, {'total': 1, 'with_email': 0, 'no_email': 1},
                        worker_id=claimed.worker_id, lease_token=claimed.lease_token)
    assert client.get('/review/task/'+task.task_id+'/source').status_code == 200
    assert client.post(endpoint, headers=headers, json={**payload, 'verdict': 'revoked', 'reason': '撤回此前排除或确认依据'}).status_code == 204
    invalidated = queue.get_task(task.task_id)
    assert invalidated.status == 'failed' and not invalidated.stats['delivery_ready']
    with queue._connect() as con:
        assert con.execute('SELECT COUNT(*) FROM records WHERE task_id=?', (task.task_id,)).fetchone()[0] == 1
    # The copied attempt file cannot overrule the authoritative withdrawal.
    third = await batch.run_plan(plan, object(), out.parent/'attempt-3', task_id=task.task_id)
    assert not third['delivery_ready'] and third['total'] == 0


def test_new_decision_on_automatically_qualified_observation_requires_native_resume(tmp_path):
    _, _, _, item, audit = fixture(tmp_path, role="faculty_roster")
    data = json.loads(audit.read_text(encoding="utf8"))
    data["unresolved"] = []
    data["delivery_ready"] = True
    audit.write_text(json.dumps(data), encoding="utf8")
    assert not review_registry.has_review(audit)
    save(item, audit, verdict="unknown")
    assert review_registry.has_review(audit) and not review_registry.review_complete(audit)
    assert source_review.collect_items(audit) == [item]
    save(item, audit, verdict="exclude")
    assert review_registry.review_complete(audit)
    prepare_review_resume(audit, item["binding"]["task_id"])
    target = audit.parent.parent/"attempt-2"
    carry_review_decisions(audit.parent.parent, target)
    assert source_review.decisions_for(target, item["binding"]["task_id"])[item["id"]]["verdict"] == "exclude"


@pytest.mark.asyncio
async def test_changed_relationship_before_export_keeps_current_coverage_blocked(tmp_path, monkeypatch):
    from server import browser_batch_tool as batch
    from server.harvest_workflow import CapturedRosterPage, SourceCapture
    from server.source_qualification import witness
    out = tmp_path/"source-native"/"attempt-1"
    url = "https://example.edu/members"
    html = '<title>组织成员</title><h1>组织成员</h1><header>样本大学样本学院</header><main><p>王芳 教授</p></main>'
    branch = {"url": url, "source_role": "organizational_membership", "source_container_selector": "main",
              "role_evidence_selector": "h1", "source_context_selector": "header", "card_selector": "main > p", "single_page": True}
    plan = {"school": "样本大学", "college": "样本学院", "branches": [branch]}
    proof = {**witness(html, url, branch, plan), "confirmed": True, "dom_stable": True,
             "network_idle": True, "witness_visible": True}
    async def capture(*args):
        return SourceCapture(roster_pages=[CapturedRosterPage(url, html, branch, source_role_evidence=proof)],
            branches=[{"url": url, "end": "single_page_no_pagination_control_seen", "visited_pages": 1}]), {}
    async def profiles(*args, **kwargs):
        return {}, []
    monkeypatch.setattr(batch, "_capture_sources_with_state", capture)
    monkeypatch.setattr(batch, "capture_profiles", profiles)
    first = await batch.run_plan(plan, object(), out, task_id="source-native")
    item, = source_review.collect_items(Path(first["audit_path"]))
    save(item, Path(first["audit_path"]))
    original = source_review.apply_decisions
    def withdraw_after_qualification(*args):
        result = original(*args)
        if result[3]:
            save(item, Path(first["audit_path"]), verdict="revoked")
        return result
    monkeypatch.setattr(source_review, "apply_decisions", withdraw_after_qualification)
    second = await batch.run_plan(plan, object(), out.parent/"attempt-2", task_id="source-native")
    assert not second["delivery_ready"] and second["excel_path"] is None and second["total"] == 1
    assert not second["coverage"]["complete"]
    assert second["coverage"]["issues"] == second["unresolved"]
    assert second["coverage"]["unresolved_count"] == len(second["unresolved"])
    assert json.loads(Path(second["coverage_path"]).read_text(encoding="utf8")) == second["coverage"]
