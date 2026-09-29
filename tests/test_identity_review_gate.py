"""Different profile URLs cannot establish that homonyms are distinct people."""
import json
from types import SimpleNamespace
import pytest
from server.agent_queue import AgentTaskQueue
from server.university_delivery import publish_delivery


def row(url,**extra):
    return {'姓名':'Same Person','主页':url,'邮箱':'','职称':'Professor','简介':'Original full biography',**extra}


def finish(queue,task,records):
    claim=queue.claim_task('worker',task_id=task.task_id)
    queue.complete_task(task.task_id,records,{'total':len(records),'with_email':0,'no_email':len(records)},
        worker_id='worker',lease_token=claim.lease_token)


def test_receipt_rejects_same_name_different_profiles_without_losing_lease(tmp_path):
    queue=AgentTaskQueue(tmp_path/'tasks.db')
    task=queue.submit_task('University','College','https://example.edu/')
    claim=queue.claim_task('worker',task_id=task.task_id)
    with pytest.raises(ValueError,match='identity'):
        queue.complete_task(task.task_id,[row('https://example.edu/p/1'),row('https://example.edu/p/2')],
            {'total':2,'with_email':0,'no_email':2},worker_id='worker',lease_token=claim.lease_token)
    current=queue.get_task(task.task_id)
    assert current.status=='running' and not current.records and current.lease_token==claim.lease_token


def test_university_keeps_both_homonym_observations_and_blocks_delivery(tmp_path):
    queue=AgentTaskQueue(tmp_path/'tasks.db')
    for college,url,bio in [('A','https://example.edu/1','Original biography A'),('B','https://example.edu/2','Different full biography B')]:
        task=queue.submit_task('University',college,mode='batch',batch_id='batch')
        finish(queue,task,[row(url,简介=bio)])
    snapshot=queue.get_university_snapshot('University')
    assert len(snapshot['records'])==2
    assert snapshot['identity_conflicts'][0]['requires_review'] is True
    result=publish_delivery(queue,'batch','University',tmp_path/'out')
    assert result['delivery_scope']['state']=='blocked' and result['university_excel'] is None
    assert not (tmp_path/'out/University.xlsx').exists()


def test_legacy_english_same_profile_conflict_preserves_both_bios(tmp_path):
    queue=AgentTaskQueue(tmp_path/'tasks.db')
    for college,bio in [('A','Original biography A'),('B','Different biography B')]:
        task=queue.submit_task('University',college)
        finish(queue,task,[{'name':'Same Person','profile_url':'https://example.edu/1','email':'','title':'Professor','bio':bio}])
    snapshot=queue.get_university_snapshot('University')
    assert len(snapshot['records'])==2
    assert any(c['requires_review'] and '简介' in c.get('fields',[]) for c in snapshot['identity_conflicts'])


@pytest.mark.asyncio
async def test_identity_review_does_not_trigger_more_structure_model_calls(tmp_path,monkeypatch):
    from server import agent_worker,agent_recon
    worker=agent_worker.AgentWorker(db_path=tmp_path/'tasks.db',output_dir=tmp_path/'out')
    task=SimpleNamespace(school='University',college='College',url='https://example.edu/')
    plan={'school':task.school,'college':task.college,'source_kind':'university_faculty_roster',
          'branches':[{'url':task.url,'card_selector':'main li','single_page':True}]}
    calls=[]
    async def recon(*args,**kwargs):
        calls.append(1)
        return agent_recon.ReconResult(plan,0,'completed','fixture')
    async def collect(*args):
        return {'delivery_ready':False,'unresolved':[{'status':'distinct_profile_identity_unverified','url':'https://example.edu/p/2'}]}
    monkeypatch.setattr(agent_worker,'reconnoiter',recon)
    monkeypatch.setattr(worker,'_run_browser_plan',collect)
    result=await worker._harvest_with_repair(task,tmp_path/'out')
    assert not result['delivery_ready'] and len(calls)==1


@pytest.mark.asyncio
async def test_existing_ambiguous_version_is_hidden_and_not_downloadable(tmp_path,monkeypatch):
    import httpx
    from server import app as web
    queue=AgentTaskQueue(tmp_path/'tasks.db')
    tasks=[]
    for college,url in [('A','https://example.edu/1'),('B','https://example.edu/2')]:
        task=queue.submit_task('University',college,mode='batch',batch_id='legacy')
        finish(queue,task,[row(url)])
        tasks.append(task)
    root=tmp_path/'outputs'
    directory=root/'versions/school/old'
    directory.mkdir(parents=True)
    book=directory/'University.xlsx'
    book.write_bytes(b'original immutable workbook')
    (directory/'manifest.json').write_text(json.dumps({'school':'University','scope':{'batch_id':'legacy'},'college_snapshots':[{'task_id':t.task_id} for t in tasks]}))
    with queue._connect() as con:
        con.execute('UPDATE tasks SET download_url=? WHERE task_id=?',('/download/versions/school/old/University.xlsx',tasks[0].task_id))
    monkeypatch.setattr(web,'agent_queue',queue)
    monkeypatch.setattr(web,'OUTPUT_DIR',str(root))
    from server.access_control import AccountStore
    accounts=AccountStore(queue.db_path)
    accounts.create_user('admin','Administrator','correct horse battery staple','admin')
    monkeypatch.setattr(web,'account_store',accounts)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=web.app),base_url='http://testserver') as client:
        login=await client.post('/api/auth/login',json={'username':'admin','password':'correct horse battery staple'})
        assert login.status_code==200
        status=(await client.get('/api/task/'+tasks[0].task_id)).json()
        assert status['delivery_scope']['state']=='blocked' and not status['download_url']
        assert status['counts_basis']=='profile_observations_not_verified_people'
        batch=(await client.get('/api/batch/legacy')).json()
        assert all(st['counts_basis']=='profile_observations_not_verified_people' for st in batch['subtasks'])
        assert not batch['subtasks'][0]['download_url']
        response=await client.get('/download/versions/school/old/University.xlsx')
        assert response.status_code==409
    assert book.read_bytes()==b'original immutable workbook'



def test_fixed_official_profile_fixture_requires_fourteen_identity_reviews():
    from pathlib import Path
    from server.faculty_identity import identity_review_issues
    fixture=json.loads((Path(__file__).parent/'fixtures/identity_review/official_multi_profile_rows.json').read_text())
    assert len(fixture['records'])==45
    assert len({r['name'] for r in fixture['records']})==31
    issues=identity_review_issues(fixture['records'])
    assert len(issues)==14 and sum(i['observation_count'] for i in issues)==28
    assert all(i['status']=='distinct_profile_identity_unverified' for i in issues)



def test_failed_receipt_preserves_model_audit_reference_under_lease(tmp_path):
    queue=AgentTaskQueue(tmp_path/'tasks.db')
    task=queue.submit_task('University','A')
    claim=queue.claim_task('worker',task_id=task.task_id)
    stats={'recon_audit_path':'local/recon-attempts.json','recon_audit_sha256':'a'*64}
    queue.sync_task_result(task.task_id,[],stats,'','identity review required',
        worker_id='worker',lease_token=claim.lease_token)
    assert queue.get_task(task.task_id).stats==stats


@pytest.mark.asyncio
async def test_alias_download_rechecks_its_own_snapshot_after_new_completion(tmp_path,monkeypatch):
    from server import app as web
    from fastapi import HTTPException
    queue=AgentTaskQueue(tmp_path/'tasks.db')
    old=[]
    for college,url in [('A','https://example.edu/1'),('B','https://example.edu/2')]:
        task=queue.submit_task('University',college,mode='batch',batch_id='legacy')
        finish(queue,task,[row(url)])
        old.append(task)
    root=tmp_path/'outputs';root.mkdir()
    (root/'University.xlsx').write_bytes(b'old ambiguous workbook')
    (root/'University.manifest.json').write_text(json.dumps({'school':'University',
        'college_snapshots':[{'task_id':task.task_id} for task in old]}))
    for college,name in [('A','Distinct One'),('B','Distinct Two')]:
        task=queue.submit_task('University',college,mode='batch',batch_id='legacy')
        finish(queue,task,[row('https://example.edu/new/'+college,姓名=name)])
    assert not queue.get_university_snapshot('University')['identity_conflicts']
    assert queue.get_delivery_scope('legacy','University')['state']=='ready'
    version=root/'versions/school/old/University.xlsx'
    version.parent.mkdir(parents=True)
    version.write_bytes(b'old ambiguous version')
    (version.parent/'manifest.json').write_text(json.dumps({'school':'University','scope':{'batch_id':'legacy'},
        'college_snapshots':[{'task_id':task.task_id} for task in old]}))
    monkeypatch.setattr(web,'agent_queue',queue)
    monkeypatch.setattr(web,'OUTPUT_DIR',str(root))
    with pytest.raises(HTTPException) as error:
        await web.download_file('University.xlsx')
    assert error.value.status_code==409
    with pytest.raises(HTTPException) as version_error:
        await web.download_file('versions/school/old/University.xlsx')
    assert version_error.value.status_code==409
    assert version.read_bytes()==b'old ambiguous version'
    assert (root/'University.xlsx').read_bytes()==b'old ambiguous workbook'
