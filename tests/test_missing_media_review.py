"""Unavailable image data stays blocked until an exact human role decision."""
import json

import pytest

from server import missing_media_review as review
from server import media_kind_review
from server.image_biography import BrowserAssetError, audit_profile_media
from server.review_registry import has_review, review_complete

URL = 'https://faculty.example.edu/p/1'
IMAGE = 'https://faculty.example.edu/photo.png'
BODY = '<p>张伟 教授，完整学习经历与工作经历。</p><p>研究成果及邮箱：person@example.edu</p>'
HTML = '<article><hr>' + BODY + '<img src="/photo.png" width="200" height="300"></article>'


@pytest.fixture
def local_faculty_site():
    """Loopback transport covers Chromium and its APIRequestContext alike."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    state = {'redirected': False, 'image': None}
    class Handler(BaseHTTPRequestHandler):
        def serve(self, head=False):
            if self.path == '/p/1' and state['redirected']:
                self.send_response(302)
                self.send_header('Location', '/redirected/current')
                self.end_headers()
                return
            if self.path == '/list':
                body = '<main><ul><li><a href="/p/1">张伟</a>教授</li></ul></main>'.encode('utf8')
                kind, status = 'text/html; charset=utf-8', 200
            elif self.path in ('/p/1', '/redirected/current'):
                html = HTML.replace('src="/photo.png"', 'src="photo.png"') if state['redirected'] else HTML
                body, kind, status = html.encode('utf8'), 'text/html; charset=utf-8', 200
            elif self.path == ('/redirected/photo.png' if state['redirected'] else '/photo.png'):
                body, kind = state['image'] or b'missing', 'image/png'
                status = 200 if state['image'] else 404
            else:
                body, kind, status = b'not found', 'text/plain', 404
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
        def do_GET(self):
            self.serve()
        def do_HEAD(self):
            self.serve(head=True)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def item(html=HTML, start=None):
    value = review.prepare_item(html, URL, 'article', start, IMAGE, 404)
    assert value is not None
    return value


def audit_file(tmp_path, value=None):
    value = dict(value or item())
    snapshot = tmp_path / 'profile.html'
    snapshot.write_text(HTML, encoding='utf8')
    value['snapshot_path'] = str(snapshot)
    path = tmp_path / 'sample_audit.json'
    path.write_text(json.dumps({'missing_profile_media_evidence': [value], 'unresolved': [
        {'status': review.STATUS, 'evidence_id': value['id'], 'profile_url': URL, 'media_url': IMAGE}
    ]}), encoding='utf8')
    return path


def test_missing_image_has_actionable_review_and_exact_source_bound_decision(tmp_path):
    audit = audit_file(tmp_path)
    assert has_review(audit) and not review_complete(audit)
    [value] = media_kind_review.collect_items(audit)
    assert '完整学习经历与工作经历' in value['context_text']
    assert '研究成果及邮箱：person@example.edu' in value['context_text']
    path = tmp_path / review.DECISIONS_FILE
    media_kind_review.save_decision(path, value, 'unreadable', 1)
    assert not review_complete(audit)
    media_kind_review.save_decision(path, value, 'photo', 1)
    assert review_complete(audit)
    changed = item(HTML.replace('工作经历', '新增完整工作经历'))
    assert not review.confirmed(path, changed)
    changed = {**value, 'media_url': IMAGE + '?v=2'}
    assert not review.confirmed(path, changed)
    assert not review.review_complete(audit, [])


def test_human_actor_is_required_and_chat_provenance_is_distinct(tmp_path):
    value = item()
    path = tmp_path / review.DECISIONS_FILE
    with pytest.raises(ValueError, match='human decision'):
        review.save_decision(path, value, 'photo')
    review.save_decision(path, value, 'photo', confirmation_ref='explicit-user-answer')
    assert review.confirmed(path, value)
    stored = json.loads(path.read_text(encoding='utf8'))
    assert 'chat' in json.dumps(stored) and 'user_id' not in json.dumps(stored)


@pytest.mark.parametrize('html', [
    '<article><img src="/photo.png"></article>',
    '<article style="display:none">' + BODY + '<img src="/photo.png"></article>',
    '<article>' + BODY + '<picture><img src="/photo.png"></picture></article>',
    '<article>' + BODY + '<img src="/photo.png" srcset="/other.png 2x"></article>',
    '<article>' + BODY + '<img src="/other.png"></article>',
])
def test_unavailable_profile_or_alternative_rendition_cannot_become_photo_review(html):
    assert review.prepare_item(html, URL, 'article', None, IMAGE, 404) is None


@pytest.mark.parametrize('mutation', ['snapshot', 'escape', 'missing', 'not_twice'])
def test_review_rejects_unbacked_source_evidence(tmp_path, mutation):
    audit = audit_file(tmp_path)
    payload = json.loads(audit.read_text(encoding='utf8'))
    if mutation == 'snapshot':
        (tmp_path / 'profile.html').write_text(HTML + '<p>changed</p>', encoding='utf8')
        # Changes outside the verified subtree do not invalidate its identity.
        assert media_kind_review.collect_items(audit)
        (tmp_path / 'profile.html').write_text(HTML.replace('工作经历', 'changed'), encoding='utf8')
    elif mutation == 'escape':
        payload['missing_profile_media_evidence'][0]['snapshot_path'] = str(tmp_path.parent / 'foreign.html')
    elif mutation == 'missing':
        payload['missing_profile_media_evidence'] = []
    else:
        payload['missing_profile_media_evidence'][0]['missing_on_two_opens'] = False
    audit.write_text(json.dumps(payload), encoding='utf8')
    with pytest.raises(ValueError):
        media_kind_review.collect_items(audit)


@pytest.mark.asyncio
@pytest.mark.parametrize('start', [None, 'article > hr'])
async def test_media_audit_requires_human_role_and_preserves_complete_dom(tmp_path, start):
    from server.profile_content import scope_profile_html
    value = item(start=start)
    scoped, _, issue = scope_profile_html(HTML, 'article', URL, start_selector=start)
    assert not issue
    async def fetch(_url):
        raise BrowserAssetError(404, IMAGE)
    async def run():
        return await audit_profile_media({URL: scoped}, {URL: {'张伟'}}, tmp_path / 'media',
            fetch_asset=fetch, scoped_urls={URL}, missing_media_evidence={URL: [value]},
            profile_source_html={URL: HTML}, media_kind_decisions_path=tmp_path / review.DECISIONS_FILE)
    initial = await run()
    assert [issue['status'] for issue in initial['unresolved']] == [review.STATUS]
    assert initial['missing_media_evidence'][0]['id'] == value['id']
    review.save_decision(tmp_path / review.DECISIONS_FILE, value, 'photo', 1)
    final = await run()
    assert not final['unresolved'] and final['unpublished_media'][0]['status'] == 'image_missing_photo_human_reviewed'
    assert BODY in scoped  # No biography removal or media OCR fiction.


@pytest.mark.parametrize('error', [ValueError('connection closed'), BrowserAssetError(403, IMAGE),
                                   BrowserAssetError(500, IMAGE)])
@pytest.mark.asyncio
async def test_download_failures_cannot_invent_missing_image_role(tmp_path, error):
    async def fetch(_url):
        raise error
    result = await audit_profile_media({URL: HTML}, {URL: {'张伟'}}, tmp_path / 'media',
        fetch_asset=fetch, missing_media_evidence={URL: [item()]})
    assert result['unresolved'][0]['status'] == 'image_bio_asset_failed'
    assert not result['missing_media_evidence']


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize('first_status', [404, 403])
@pytest.mark.parametrize('image_markup', [
    '<img src="/photo.png" width="200" height="300">',
    '<style>#portrait{width:200px;height:300px}</style><img id="portrait" src="/photo.png">',
    '<img src="/photo.png" width="20" height="20">',
    '<img src="/photo.png">',
])
async def test_browser_scope_requires_two_permanent_missing_image_observations(first_status, image_markup):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.browser_readiness import install_request_tracking
    from server.profile_structure import verify_profile_container
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser, polite=False)
            status = first_status
            async def route(request):
                if request.request.url == IMAGE:
                    await request.fulfill(status=status, body='missing')
                else:
                    html = HTML.replace('<img src="/photo.png" width="200" height="300">', image_markup)
                    await request.fulfill(body=html, content_type='text/html; charset=utf-8')
            await context.route('**/*', route)
            page = await context.new_page()
            install_request_tracking(page)
            await page.goto(URL, wait_until='networkidle')
            first = {}
            proof = await verify_profile_container(page, 'article', local_capture=first)
            assert not proof['confirmed'] and not proof['missing_content_images']
            status = 404
            await page.reload(wait_until='networkidle')
            proof = await verify_profile_container(page, 'article',
                recurring_failures=first['failed_requests'],
                recurring_missing_media=first['missing_media_failures'])
            assert proof['confirmed'] is (first_status == 404)
            assert bool(proof['missing_content_images']) is (first_status == 404)
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_recovered_image_uses_original_audit_despite_old_missing_photo_decision(tmp_path):
    from io import BytesIO
    from PIL import Image
    from test_media_kind_review import unread_lines
    value = item()
    path = tmp_path / review.DECISIONS_FILE
    review.save_decision(path, value, 'photo', 1)
    data = BytesIO()
    Image.new('RGB', (1000, 1500), 'white').save(data, format='PNG')
    async def fetch(_url):
        return data.getvalue()
    result = await audit_profile_media({URL: HTML}, {URL: {'张伟'}}, tmp_path / 'media',
        fetch_asset=fetch, missing_media_evidence={URL: [value]},
        media_kind_decisions_path=path, ocr_engine=unread_lines)
    assert result['unresolved'][0]['status'] == 'image_bio_ocr_incomplete'
    assert not result['missing_media_evidence']


def test_missing_image_web_review_preserves_authentication_and_retry_decisions(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from server import app as web
    from server.access_control import AccountStore
    from server.agent_queue import AgentTaskQueue
    from server.task_image_review import carry_review_decisions
    from test_authenticated_image_review import _client
    queue = AgentTaskQueue(tmp_path / 'queue.db')
    store = AccountStore(queue.db_path)
    user = store.sign_in('reviewer')
    task = queue.submit_task('测试大学', '测试学院', URL, owner_user_id=user.id)
    out = tmp_path / 'outputs'
    attempt = out / 'tasks' / task.task_id / 'attempt-1'
    attempt.mkdir(parents=True)
    audit = audit_file(attempt)
    payload = json.loads(audit.read_text(encoding='utf8'))
    payload.update(school=task.school, college=task.college, audit_path=str(audit))
    audit.write_text(json.dumps(payload), encoding='utf8')
    with queue._connect() as con:
        con.execute("UPDATE tasks SET status='failed' WHERE task_id=?", (task.task_id,))
    monkeypatch.setattr(web, 'agent_queue', queue)
    monkeypatch.setattr(web, 'account_store', store)
    monkeypatch.setattr(web, 'OUTPUT_DIR', str(out))
    client, csrf = _client(store, 'reviewer')
    response = client.get(f'/review/task/{task.task_id}')
    assert response.status_code == 200 and '失效图片用途复核' in response.text
    assert '完整学习经历与工作经历' in response.text and '原图不可查看' in response.text
    [value] = media_kind_review.collect_items(audit)
    endpoint = f'/api/task/{task.task_id}/review/media-kind'
    decision = {'id': value['id'], 'verdict': 'photo'}
    assert TestClient(web.app).post(endpoint, json=decision).status_code == 401
    assert client.post(endpoint, json=decision).status_code == 403
    assert client.post(endpoint, json=decision, headers={'X-CSRF-Token': csrf}).status_code == 204
    assert review_complete(audit)
    assert client.post(f'/api/task/{task.task_id}/review/retry',
                       headers={'X-CSRF-Token': csrf}).status_code == 200
    target = attempt.parent / 'attempt-2'
    carry_review_decisions(attempt.parent, target)
    assert review.confirmed(target / review.DECISIONS_FILE, value)


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize('redirected', [False, True])
async def test_real_browser_batch_emits_review_then_keeps_full_biography_after_decision(tmp_path, local_faculty_site, redirected):
    from pathlib import Path
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.browser_batch_tool import run_plan
    base, state = local_faculty_site
    state['redirected'] = redirected
    requested_url = base + '/p/1'
    final_url = base + '/redirected/current' if redirected else requested_url
    media_url = base + '/redirected/photo.png' if redirected else base + '/photo.png'
    listing = base + '/list'
    plan = {'school': '测试大学', 'college': '测试学院', 'source_kind': 'university_faculty_roster',
            'branches': [{'url': listing, 'card_selector': 'main li', 'single_page': True,
                          'profile_content_selector': 'article',
                          'profile_content_start_selector': 'article > hr'}]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser, polite=False)
            first = await run_plan(plan, context, tmp_path)
            assert not first['delivery_ready']
            assert [issue['status'] for issue in first['unresolved']] == [review.STATUS]
            [value] = media_kind_review.collect_items(Path(first['audit_path']))
            assert value['profile_url'] == final_url and value['requested_profile_url'] == requested_url
            assert value['media_url'] == media_url
            media_kind_review.save_decision(tmp_path / review.DECISIONS_FILE, value, 'photo', 1)
            final = await run_plan(plan, context, tmp_path)
            assert final['delivery_ready'] and final['total'] == 1 and final['with_email'] == 1
            [record] = json.loads(Path(final['records_path']).read_text(encoding='utf8'))
            assert '完整学习经历与工作经历' in record['简介']
            assert '研究成果及邮箱：person@example.edu' in record['简介']
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_redirected_image_recovery_reads_final_resource_base(local_faculty_site, tmp_path):
    from io import BytesIO
    from PIL import Image
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.harvest_workflow import capture_profiles
    from server.image_biography import fetch_browser_asset
    from server.profile_content import scope_profile_html
    from test_media_kind_review import unread_lines
    base, state = local_faculty_site
    state['redirected'] = True
    data = BytesIO()
    Image.new('RGB', (1000, 1500), 'white').save(data, format='PNG')
    state['image'] = data.getvalue()
    requested = base + '/p/1'
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            context = await new_browser_context(browser, polite=False)
            live = {}
            pages, errors = await capture_profiles(context, [requested], 1,
                profile_content_selectors={requested: {('article', None)}}, profile_content_evidence=live)
            assert not errors and live[requested]['confirmed']
            assert not live[requested]['missing_content_images']
            scoped, _, _ = scope_profile_html(pages[requested], 'article', requested)
            fetched = []
            async def fetch(address):
                fetched.append(address)
                return await fetch_browser_asset(context, address)
            result = await audit_profile_media({requested: scoped}, {requested: {'张伟'}}, tmp_path,
                fetch_asset=fetch, scoped_urls={requested}, profile_source_html=pages,
                profile_source_urls={requested: live[requested]['url']}, ocr_engine=unread_lines)
            assert fetched == [base + '/redirected/photo.png']
            assert result['unresolved'][0]['status'] == 'image_bio_ocr_incomplete'
            assert result['evidence'][0]['original_path']
        finally:
            await browser.close()
