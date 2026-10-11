"""Local native caller regressions; reviewer decisions below are synthetic fixtures."""
import copy
import json
from pathlib import Path

from bs4 import BeautifulSoup
import pytest
from playwright.async_api import async_playwright

from server import identity_review, missing_media_review
from server.browser_batch_tool import run_plan
from server.browser_context import new_browser_context
from server.detail_mecc import LayoutAwareTokenizer
from server.profile_content import scope_profile_html
from server.review_evidence import register_source, sha


def _archive(attempt, pages, fields, **payload):
    snapshots = []
    (attempt / 'snapshots').mkdir(exist_ok=True)
    for index, (url, html) in enumerate(pages.items()):
        path = attempt / 'snapshots' / f'{index}.html'
        path.write_bytes(html.encode())
        snapshots.append({'url': url, 'kind': 'profile', 'path': str(path)})
    audit = attempt / 'original_audit.json'
    audit.write_text(json.dumps({'audit_path': str(audit), 'snapshots': snapshots,
        'coverage': {'field_sources': fields}, **payload}), encoding='utf-8')
    register_source(attempt.parent, audit, attempt.parent.name)
    return audit


def _read(html):
    return LayoutAwareTokenizer.get_clean_text(BeautifulSoup(html, 'lxml').body, preserve_content=True)


@pytest.mark.browser
@pytest.mark.asyncio
async def test_native_run_plan_carries_disposition_originals_and_reparses_only_unchanged_person_content(tmp_path):
    base = 'https://fixture.example.edu'
    urls = [base + f'/person/{i}' for i in range(4)]
    branding = '<header>样本大学 样本学院</header>'
    formal = {u: '<html><body>' + branding + '<article><h4>导师信息-王芳</h4>'
        '<p>日期：2026-01-01</p><section id="content"><div class="inner">'
        '<p>王芳</p><p>职称：副教授(博导/硕导)</p><p>完整本人履历' + str(i)
        + '</p></div></section></article></body></html>' for i, u in enumerate(urls[:2])}
    auxiliary = {u: '<html><body>' + branding + '<h1>科研团队</h1><article>王芳 辅助旧观察不可捐字段'
        '</article></body></html>' for u in urls[2:]}
    pages = {**formal, **auxiliary}
    roster = '<html><head><title>师资队伍</title></head><body>' + branding + '<h1>师资队伍</h1><main><ul>' + ''.join(
        f'<li><a href="{u}">王芳</a></li>' for u in urls[:2]) + '</ul></main></body></html>'
    root = tmp_path / 'tasks' / 'native-disposition-reparse'
    attempt = root / 'attempt-1'
    attempt.mkdir(parents=True)
    old_rows, hashes, fields = [], {}, []
    for i, (url, html) in enumerate(pages.items()):
        if i < 2:
            scoped, evidence, issue = scope_profile_html(html, 'article', url)
            assert not issue
            bio, digest = _read(scoped), evidence['selected_html_sha256']
            fields.append({'profile_url': url, 'profile_content_selector': 'article'})
        else:
            bio, digest = '', sha(html)
            fields.append({'profile_url': url})
        old_rows.append({'姓名': '王芳', '主页': url, '职称': '副教授' if i < 2 else '教师',
                        '简介': bio, '邮箱': '' if i < 2 else 'aux@example.edu',
                        '学校': '样本大学', '学院': '样本学院'})
        hashes[url] = digest
    old_item = identity_review.pending_items(old_rows, hashes)[0]
    path = attempt / identity_review.DECISIONS_FILE
    identity_review.save_decision(path, old_item, 'same', urls[0], reviewed_by=7)
    decision_bytes = path.read_bytes()
    _archive(attempt, pages, fields, identity_review_items=[old_item])
    plan = {'school': '样本大学', 'college': '样本学院', 'branches': [{
        'url': base + '/list', 'card_selector': 'main li', 'single_page': True,
        'source_role': 'faculty_roster', 'source_container_selector': 'main',
        'role_evidence_selector': 'h1', 'source_context_selector': 'header',
        'profile_content_selector': '#content'}], 'source_dispositions': [{
            'url': u, 'reason': 'source_relationship_unverified', 'source_role': 'unverified_people_source',
            'source_container_selector': 'article', 'role_evidence_selector': 'h1',
            'source_context_selector': 'header'} for u in urls[2:]]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await new_browser_context(browser, polite=False)
        await context.route('**/*', lambda route: route.fulfill(
            body=roster if route.request.url == base + '/list' else pages.get(route.request.url, ''),
            content_type='text/html; charset=utf-8'))
        try:
            report = await run_plan(plan, context, attempt)
            records = json.loads(Path(report['records_path']).read_text(encoding='utf-8'))
            pages[urls[0]] = pages[urls[0]].replace('完整本人履历0', '新增真实本人经历0')
            changed_person = await run_plan(plan, context, attempt)
            pages[urls[0]] = formal[urls[0]]
            missing_proof_plan = copy.deepcopy(plan)
            missing_proof_plan['source_dispositions'][0]['role_evidence_selector'] = 'h2.absent'
            missing_proof = await run_plan(missing_proof_plan, context, attempt)
            assert not identity_review.review_complete(Path(missing_proof['audit_path']),
                                                       missing_proof['identity_review_items'])
        finally:
            await browser.close()
    assert not report['identity_review_items'] and len(report['identity_reviews']) == 1
    assert len(changed_person['identity_review_items']) == 1 and not changed_person['identity_reviews']
    assert len(missing_proof['identity_review_items']) == 1 and not missing_proof['identity_reviews']
    assert len(records) == 1 and records[0]['职称'] == '副教授(博导/硕导)'
    assert all(f'完整本人履历{i}' in records[0]['简介'] for i in (0, 1))
    assert '辅助旧观察' not in records[0]['简介'] and records[0]['邮箱'] == ''
    assert '日期' not in records[0]['简介']
    assert report['source_disposition_evidence'] and not report['delivery_ready']
    # Source qualification remains pending; an identity certificate grants no source permission.
    assert path.read_bytes() == decision_bytes
    certificate = report['identity_reviews'][0]
    assert certificate['id'] == old_item['id']
    assert all(p['basis'] == 'same_native_source_scope_projection'
               for p in certificate['source_equivalence'] if not p.get('qualification_source'))
    with pytest.raises(ValueError):
        altered = copy.deepcopy(certificate)
        altered['source_equivalence'][0]['scope_projection']['unchanged_inner_sha256'] = '0' * 64
        identity_review.validated_pairs(records, [altered], path)


@pytest.mark.parametrize('change', [None, 'content', 'image', 'role_context', 'no_decision', 'no_original', 'withdrawn'])
def test_missing_photo_wrapper_change_keeps_exact_old_decision_and_blocks_changed_context(tmp_path, change):
    url, image = 'https://fixture.example.edu/person', 'https://fixture.example.edu/photo.png'
    html = '<html><body><article><h1>导师信息-王芳</h1><section id="content"><div class="inner">'
    html += '<p>王芳 副教授，完整本人履历。</p><img src="/photo.png"></div></section></article></body></html>'
    root = tmp_path / 'tasks' / 'photo-wrapper'
    attempt = root / 'attempt-1'
    attempt.mkdir(parents=True)
    old = missing_media_review.prepare_item(html, url, 'div.inner', None, image, 404)
    path = attempt / missing_media_review.DECISIONS_FILE
    missing_media_review.save_decision(path, old, 'photo', 7)
    # Actual legacy decision predates the audit's broader boundary, so this audit
    # has no fabricated missing-media item for the old decision.
    _archive(attempt, {url: html}, [{'profile_url': url, 'profile_content_selector': 'article'}])
    fresh = html
    if change == 'content':
        fresh = fresh.replace('完整本人履历', '新事实')
    elif change == 'image':
        fresh = fresh.replace('<img src="/photo.png">', '<img src="/photo.png" width="200">')
    elif change == 'role_context':
        fresh = fresh.replace('<div class="inner">', '<p>此图片含履历文字</p><div class="inner">')
    elif change == 'no_decision':
        path.unlink()
    elif change == 'no_original':
        (root / 'review-evidence' / 'objects' / (sha(html) + '.html')).unlink()
    current = missing_media_review.prepare_item(fresh, url, '#content', None, image, 404)
    if change == 'withdrawn':
        missing_media_review.save_decision(path, current, 'unreadable', 7)
    saved = path.read_bytes() if path.exists() else None
    assert missing_media_review.confirmed(path, current, source_html=fresh) is (change is None)
    if change is None:
        assert current['review_equivalence']['original_decision_id'] == old['id']
    assert (path.read_bytes() if path.exists() else None) == saved
