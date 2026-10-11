"""Checkpoint-only reviewed identity is reparsed from normal browser empty evidence."""

import hashlib
import json
from pathlib import Path
import pytest
from playwright.async_api import async_playwright
from server import identity_review
from server.browser_batch_tool import _scope_captured_profiles
from server.faculty_extractor import FacultyCandidate, FacultyProfileExtractor
from server.harvest_workflow import capture_profiles

URL = "https://checkpoint.example.edu/info/1042/1852.htm"


def document(nonce, count):
    return (
        '<html><head><script src="/system/resource/js/dynclicks.js"></script></head><body><nav>College nav</nav><main><h1>王芳</h1><p>点击数：<script>_showDynClicks("wbnews", 2062419276, 1852)</script>'
        + f'<span id="dynclicks_wbnews_1852_{nonce}" name="dynclicks_wbnews_1852_{nonce}">{count}</span>'
        + '<article id="empty" style="min-height:1px"></article></main></body></html>'
    )


@pytest.mark.asyncio
@pytest.mark.browser
async def test_checkpoint_human_identity_uses_browser_empty_proof_and_keeps_unlinked_observation(
    tmp_path,
):
    root = tmp_path / "tasks" / "fixture-task"
    attempt = root / "attempt-1"
    attempt.mkdir(parents=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context()

        async def route(request):
            await request.fulfill(
                body="function _showDynClicks(){}"
                if request.request.url.endswith(".js")
                else document(13, 451),
                content_type="application/javascript"
                if request.request.url.endswith(".js")
                else "text/html; charset=utf-8",
            )

        await context.route("**/*", route)
        proofs = {}
        pages, issues = await capture_profiles(
            context,
            [URL],
            1,
            profile_content_selectors={URL: {("#empty", None)}},
            profile_content_evidence=proofs,
        )
        assert not issues
        fresh = pages[URL]
        old = fresh.replace("1852_13", "1852_98").replace(">451<", ">450<")
        digest = hashlib.sha256(old.encode()).hexdigest()
        checkpoint = root / "page-state" / "plan" / "html" / (digest + ".html")
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_bytes(old.encode())
        first = {
            "姓名": "王芳",
            "主页": URL,
            "学校": "样本大学",
            "学院": "样本学院",
            "职称": "教师",
            "邮箱": "",
            "简介": FacultyProfileExtractor.extract(
                old, URL, fallback_name="王芳"
            ).full_bio,
        }
        second = {
            **first,
            "主页": "https://checkpoint.example.edu/list",
            "简介": "正在更新",
        }
        old_hashes = {URL: digest, second["主页"]: "a" * 64}
        old_item = identity_review.pending_items([first, second], old_hashes)[0]
        path = attempt / "identity_reviews.json"
        identity_review.save_decision(path, old_item, "same", URL, reviewed_by=7)
        saved = path.read_bytes()
        _, evidence, blocked = _scope_captured_profiles(
            pages,
            {URL: {("#empty", None)}},
            proofs,
            {URL: [{"roster_url": "https://checkpoint.example.edu/list"}]},
            [FacultyCandidate("王芳", URL)],
        )
        assert not blocked and evidence[URL]["empty_profile_evidence"]
        current = {**first, "简介": "", '职称': ''}
        extra = {**first, "主页": "", "简介": "", "职称": "副教授"}
        hashes = {**old_hashes, URL: evidence[URL]["selected_html_sha256"]}
        rows, pending, certs = identity_review.apply_decisions(
            [current, second, extra],
            hashes,
            path,
            source_html=pages,
            source_evidence=evidence,
        )
        assert (
            not pending
            and len(rows) == 2
            and any({key: row.get(key) for key in extra} == extra for row in rows)
            and len(certs) == 1
            and certs[0]["id"] == old_item["id"]
        )
        identity_review.validated_pairs(rows, json.loads(json.dumps(certs)), path)
        assert path.read_bytes() == saved
        for bad in ("body", "facts", "proof", 'default_title'):
            record = {**current}
            meta = json.loads(json.dumps(evidence))
            if bad == "body":
                record["简介"] = "捏造简介"
            elif bad == "facts":
                record["学校"] = "另一大学"
            elif bad == 'default_title':
                record['职称'] = '教师'
            else:
                meta[URL]["profile_content_browser_evidence"]["confirmed"] = False
            _, p, c = identity_review.apply_decisions(
                [record, second], hashes, path, source_html=pages, source_evidence=meta
            )
            assert p and not c
        payload = json.loads(saved)
        payload["decisions"] = []
        path.write_text(json.dumps(payload), encoding="utf8")
        _, p, c = identity_review.apply_decisions(
            [current, second], hashes, path, source_html=pages, source_evidence=evidence
        )
        assert p and not c
        await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', [None, 'bio', 'email', 'title', 'name', 'school', 'html', 'pdf', 'owner', 'certificate', 'withdrawn'])
async def test_same_pdf_owner_reader_recovers_existing_identity_without_widening_facts(tmp_path, change):
    """Real PDF reader/owner proof; the already verified DOM scope is stipulated."""
    from server.image_biography import audit_profile_media
    from server.profile_content import scope_profile_html
    from server.review_evidence import register_source
    from test_pdf_biography_owner import document

    root = tmp_path / 'tasks' / 'pdf-task'
    attempt = root / 'attempt-1'
    snapshots = attempt / 'snapshots'
    snapshots.mkdir(parents=True)
    url = 'https://faculty.example.edu/p/1'
    roster = 'https://faculty.example.edu/list'
    html = '<html><body><article><object data="/cv.pdf" type="application/pdf"></object></article></body></html>'
    raw = document([[('王芳 副教授', 16), ('完整本人履历。', 12), ('本人邮箱：149848243@qq.com', 12)]])
    async def fetch(_):
        return raw
    media = await audit_profile_media({url: html}, {url: {'王芳'}}, attempt / 'media',
        fetch_asset=fetch, known_names={'王芳'}, ocr_engine=lambda _: pytest.fail('native PDF must not use OCR'))
    assert not media['unresolved'] and media['evidence'][0]['pdf_owner_scope']['status'] == 'single_owner'
    first = {'姓名': '王芳', '主页': url, '学校': '样本大学', '学院': '样本学院',
             '职称': '教师', '邮箱': '', '简介': ''}
    second = {**first, '主页': roster}
    _, scope, blocked = scope_profile_html(html, 'article', url)
    assert not blocked
    hashes = {url: scope['selected_html_sha256'], roster: 'a' * 64}
    old = identity_review.pending_items([first, second], hashes)[0]
    path = attempt / identity_review.DECISIONS_FILE
    identity_review.save_decision(path, old, 'same', url, reviewed_by=7)
    saved = path.read_bytes()
    snapshot = snapshots / 'profile.html'
    snapshot.write_bytes(html.encode())
    audit = attempt / 'old_audit.json'
    audit.write_text(json.dumps({'task_id': root.name, 'audit_path': str(audit),
        'identity_review_items': [old], 'snapshots': [{'url': url, 'kind': 'profile', 'path': str(snapshot)}],
        'coverage': {'field_sources': [{'profile_url': url, 'profile_content_selector': 'article', **scope}]},
        'image_biography_evidence': media['evidence'],
        'unresolved': [{'status': 'image_bio_owner_unresolved', 'profile_url': url}]}), encoding='utf-8')
    register_source(root, audit, root.name)
    current = {**first, '职称': '副教授', '邮箱': '149848243@qq.com', '简介': media['biographies'][url]}
    evidence = {url: {'profile_content_selector': 'article', **scope,
        'profile_content_browser_evidence': {'confirmed': True, 'url': url,
            'html_sha256': hashlib.sha256(html.encode()).hexdigest(),
            'selected_html_sha256': hashes[url]}, 'image_biography_evidence': json.loads(json.dumps(media['evidence']))}}
    if change in {'bio', 'email', 'title', 'name', 'school'}:
        field, value = {'bio': ('简介', '捏造简介'), 'email': ('邮箱', 'other@example.edu'),
                        'title': ('职称', '教授'), 'name': ('姓名', '李华'),
                        'school': ('学校', '别的大学')}[change]
        current[field] = value
    elif change == 'html':
        html = html.replace('<article>', '<article><p>新增事实</p>')
    elif change == 'pdf':
        pdf = Path(evidence[url]['image_biography_evidence'][0]['original_path'])
        pdf.write_bytes(document([[('王芳 教授', 16), ('新的本人事实。', 12)]]))
    elif change == 'owner':
        evidence[url]['image_biography_evidence'][0]['pdf_owner_scope']['status'] = 'unknown'
    elif change == 'withdrawn':
        identity_review.save_decision(path, old, 'insufficient', reviewed_by=7)
        saved = path.read_bytes()
    rows, pending, certs = identity_review.apply_decisions([current, second], hashes, path,
        source_html={url: html}, source_evidence=evidence)
    if change == 'name':
        assert not certs and len(rows) == 2
    elif change not in {None, 'certificate'}:
        assert pending and not certs
    else:
        assert not pending and len(rows) == len(certs) == 1
        if change == 'certificate':
            certs[0]['source_equivalence'][0]['pdf_source_sha256'] = 'b' * 64
            with pytest.raises(ValueError):
                identity_review.validated_pairs(rows, certs, path)
        else:
            identity_review.validated_pairs(rows, json.loads(json.dumps(certs)), path)
    assert path.read_bytes() == saved


@pytest.mark.parametrize('change', [None, 'missing_ledger', 'source_hash', 'admitted_aux',
    'new_url', 'bio', 'withdrawn', 'certificate'])
def test_existing_same_is_projected_only_after_current_source_qualification(tmp_path, change):
    """Synthetic source-proof producer; archived originals and real classifier remain in the chain."""
    from server.faculty_extractor import FacultyProfileExtractor
    from server.harvest_eligibility import classify_candidates
    from server.review_evidence import register_source, sha
    from server.source_qualification import SourceEvidence
    root = tmp_path / 'tasks' / 'projection-task'
    attempt = root / 'attempt-1'
    attempt.mkdir(parents=True)
    urls = [f'https://fixture.example.edu/p/{i}' for i in range(4)]
    pages = {u: '<html><body><article><h1>王芳</h1><p>职称：教授</p><p>'
             + ('正式履历' if i < 2 else '辅助不可捐字段') + str(i) + '</p></article></body></html>'
             for i,u in enumerate(urls)}
    rows = []
    for i,u in enumerate(urls):
        parsed = FacultyProfileExtractor.extract(pages[u], u, '王芳', preserve_content=True)
        rows.append({'姓名':'王芳', '主页':u, '学校':'样本大学', '学院':'样本学院',
                     '职称':parsed.title, '邮箱':'' if i < 2 else 'aux@example.edu', '简介':parsed.full_bio})
    hashes = {u:sha(html) for u,html in pages.items()}
    old = identity_review.pending_items(rows, hashes)[0]
    decision_path = attempt / identity_review.DECISIONS_FILE
    identity_review.save_decision(decision_path, old, 'same', urls[0], reviewed_by=7)
    snapshots = []
    (attempt / 'snapshots').mkdir()
    for i,u in enumerate(urls):
        target = attempt / 'snapshots' / f'{i}.html'
        target.write_bytes(pages[u].encode())
        snapshots.append({'url':u, 'kind':'profile', 'path':str(target)})
    audit = attempt / 'old_audit.json'
    audit.write_text(json.dumps({'task_id':root.name, 'audit_path':str(audit),
        'identity_review_items':[old], 'snapshots':snapshots,
        'coverage':{'field_sources':[{'profile_url':u} for u in urls]}}),encoding='utf-8')
    register_source(root,audit,root.name)
    candidates = [FacultyCandidate('王芳',u) for u in urls]
    sources = {('王芳',u):{SourceEvidence(u,'faculty_roster' if i < 2 else 'unverified_people_source',
                'verified',hashes[u])} for i,u in enumerate(urls)}
    admitted,ledger,pending_sources = classify_candidates(candidates,sources,pages,
        {'school':'样本大学', 'college':'样本学院'})
    assert len(admitted)==len(pending_sources)==2
    current = [dict(r) for r in rows[:2]]
    meta = identity_review.source_scope_evidence({},ledger,[])
    if change == 'missing_ledger':
        meta = {}
    elif change == 'source_hash':
        meta[urls[0]]['source_eligibility']['王芳']['source_evidence'][0]['source_html_sha256']='0'*64
    elif change == 'admitted_aux':
        meta[urls[2]]['source_eligibility']['王芳']['status']='eligible'
    elif change == 'new_url':
        current[1]['主页']=urls[1]+'/new'
    elif change == 'bio':
        current[0]['简介']='无原件支持的新简介'
    elif change == 'withdrawn':
        identity_review.save_decision(decision_path,old,'insufficient',reviewed_by=7)
    saved = decision_path.read_bytes()
    output,pending,certs=identity_review.apply_decisions(current,hashes,decision_path,
        source_html=pages,source_evidence=meta)
    if change not in {None,'certificate'}:
        assert pending and not certs
    else:
        assert not pending and len(output)==len(certs)==1
        assert '辅助不可捐字段' not in output[0]['简介'] and output[0]['邮箱']==''
        assert certs[0]['id']==old['id']
        if change=='certificate':
            certs[0]['source_equivalence'][0]['qualification_projection']['admitted_urls'].append(urls[2])
            with pytest.raises(ValueError):
                identity_review.validated_pairs(output,certs,decision_path)
        else:
            identity_review.validated_pairs(output,json.loads(json.dumps(certs)),decision_path)
    assert decision_path.read_bytes()==saved
