"""Dedicated role proof on saved originals in a fully routed local browser."""
import json
from pathlib import Path

import pytest
from playwright.async_api import async_playwright

from server.recon_browser_mcp import BrowserScout
from server.source_qualification import SOURCE_FIELDS, captured_source_evidence
from test_source_role_qualification import CASES, FIXTURES, PLAN, claim


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('navigation', [
    '<nav><a href="/law/">法学院</a></nav>',
    '<nav><a href="/law/"><img alt="法学院" src="/logo.png"></a></nav>',
    '<div class="wp_nav"><a href="/law/"><img alt="法学院" src="/logo.png"></a></div>',
])
async def test_outgoing_college_navigation_cannot_qualify_another_colleges_people(tmp_path, navigation):
    from server.browser_batch_tool import run_plan
    from server.source_qualification import witness
    url = 'https://faculty.example.edu/business'
    plan = {'school': '样本大学', 'college': '法学院', 'source_kind': 'university_faculty_roster',
            'branches': [{'url': url, 'card_selector': 'main ul > li', 'single_page': True,
                'profile_content_selector': 'article', 'source_role': 'faculty_roster',
                'source_container_selector': 'main ul', 'role_evidence_selector': 'h1',
                'source_context_selector': 'header,nav,.wp_nav'}]}
    roster = ('<title>教师名录</title><header>样本大学 商学院</header>' + navigation
              + '<h1>教师名录</h1><main><ul><li><a href="/person">张富有</a>教授</li></ul></main>')
    profile = ('<article><h1>张富有</h1><p>职称：教授</p><p>工作单位：样本大学商学院</p>'
               '<p>张富有的完整学习与教学履历。</p><p>邮箱 zhangfu@example.edu</p></article>')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        async def serve(route):
            if route.request.url.endswith('logo.png'):
                await route.fulfill(status=200, content_type='image/svg+xml', body=(
                    '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="30"><rect width="80" height="30"/></svg>'))
            else:
                await route.fulfill(status=200, content_type='text/html; charset=utf-8',
                                    body=roster if route.request.url == url else profile)
        await context.route('**/*', serve)
        try:
            report = await run_plan(plan, context, tmp_path)
        finally:
            await browser.close()
    assert not witness(roster, url, plan['branches'][0], plan)['role_supported']
    assert not report['source_qualification_evidence'][0]['browser_proof']['confirmed']
    assert not report['delivery_ready']
    assert json.loads(Path(report['records_path']).read_text(encoding='utf8')) == []
    audit = json.loads(Path(report['audit_path']).read_text(encoding='utf8'))
    assert audit['eligibility'][0]['status'] == 'unresolved'
    assert audit['eligibility'][0]['source_evidence'][0]['status'] == 'unknown'
    held = audit['source_review_items'][0]
    assert held['native_observation']['name'] == '张富有'
    captured_profile = next(item for item in held['artifacts'] if item['kind'] == 'profile')
    assert profile in Path(captured_profile['path']).read_text(encoding='utf8')


@pytest.mark.browser
@pytest.mark.asyncio
async def test_real_browser_role_witness_preserves_auxiliary_originals(tmp_path):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        await context.add_init_script(Path('server/recon_dom_probe.js').read_text(encoding='utf-8'))
        seen = []
        hidden_url = 'https://example.edu/hidden-affiliation'
        profile_url = 'https://example.edu/current-profile'
        async def route(request):
            seen.append(request.request.url)
            case = next((row for row in CASES if row[0] == request.request.url), None)
            if request.request.url == profile_url:
                await request.fulfill(status=200, content_type='text/html; charset=utf-8', body=(
                    '<article><h1>王芳</h1><p>身份：外聘教师</p><p>职称：讲师（外聘副教授）</p>'
                    '<p>工作单位：皖西学院法学院</p><p>完整原始科研履历。</p></article>'))
            elif request.request.url == hidden_url:
                await request.fulfill(status=200, content_type='text/html; charset=utf-8', body=(
                    '<title>教师名录</title><h1>教师名录</h1><header style="height:30px">公开标题'
                    '<span style="display:none">皖西学院法学院</span></header><main><ul><li>王芳</li></ul></main>'))
            elif case:
                await request.fulfill(status=200, content_type='text/html', body=(FIXTURES/f'{case[1]}.html').read_bytes())
            else:
                # No outbound request reaches a live host. Missing presentation
                # resources use the existing published-page 404 contract.
                await request.fulfill(status=404, body='')
        await context.route('**/*', route)
        try:
            for case in CASES:
                url, _, _, role = case
                _, branch = claim(*case)
                args = {key: branch[key] for key in SOURCE_FIELDS}
                args['unit_selector'] = branch['card_selector']
                page = await context.new_page()
                scout = BrowserScout(page, tmp_path/role, 'branch', PLAN)
                await page.goto(url, wait_until='networkidle')
                result = await scout.call('browser_verify_source', args)
                proof = json.loads(result['content'][0]['text'])
                assert proof['confirmed'], proof
                evidence = captured_source_evidence(await page.content(), url, branch, PLAN, proof)
                assert evidence.status == 'verified' and evidence.role == role
                assert list((tmp_path/role).glob('*.source.html'))
                if role == 'organizational_membership':
                    false_claim = {**args, 'source_role': 'faculty_roster'}
                    rejected = json.loads((await scout.call('browser_verify_source', false_claim))['content'][0]['text'])
                    assert not rejected['confirmed'] and rejected['reason'] == 'faculty_relationship_unverified'
                await page.close()
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path/'hidden-context', 'branch', PLAN)
            await page.goto(hidden_url, wait_until='networkidle')
            hidden = await scout.call('browser_verify_source', {'source_role': 'faculty_roster',
                'source_container_selector': 'main ul', 'role_evidence_selector': 'h1',
                'source_context_selector': 'header', 'unit_selector': 'main ul > li'})
            assert not json.loads(hidden['content'][0]['text'])['confirmed']
            await page.close()
            page = await context.new_page()
            scout = BrowserScout(page, tmp_path/'current-profile', 'branch', PLAN)
            await page.goto(profile_url, wait_until='networkidle')
            profile_reply = await scout.call('browser_verify_profile', {'profile_content_selector': 'article'})
            live = json.loads(profile_reply['content'][0]['text'])
            assert live['confirmed'], live
            from server.browser_batch_tool import _scope_captured_profiles
            from server.source_qualification import owned_profile_relationship
            raw = await page.content()
            _, scopes, issues = _scope_captured_profiles({profile_url: raw}, {profile_url: {('article', None)}},
                                                         {profile_url: live}, {})
            assert not issues
            independent = owned_profile_relationship(raw, profile_url, '王芳', PLAN, scopes[profile_url], unique_owner=True)
            assert independent is not None and independent.role == 'faculty_profile_appointment'
            await page.close()
            # Cache reuse must make a fresh role call for the actual page; the
            # organizational branch remains eligible for observation, not faculty.
            from server.recon_plan_cache import validate_cached_plan
            _, branch = claim(*CASES[0])
            branch['single_page'] = True
            cached = await validate_cached_plan({'plan': {**PLAN, 'branches': [branch]}}, context,
                                                tmp_path/'cache', verify_discovery=False)
            assert cached['valid'], cached
            assert any(event.get('data', {}).get('name') == 'mcp__playwright__browser_verify_source'
                       for event in cached['events'])
        finally:
            await browser.close()
        assert seen


@pytest.mark.browser
@pytest.mark.asyncio
async def test_actual_capture_preserves_original_and_people_when_purpose_proof_throws(tmp_path):
    from server.browser_batch_tool import _capture_branch
    from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
    from server.harvest_eligibility import classify_candidates
    url, sha, _, _ = CASES[0]
    _, branch = claim(*CASES[0])
    branch.update(single_page=True, role_evidence_selector="[")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        async def serve(route):
            if route.request.url == url:
                await route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                    body=(FIXTURES/f"{sha}.html").read_bytes())
            else:
                await route.fulfill(status=404, body="")
        await context.route("**/*", serve)
        pages, errors, evidence = await _capture_branch(context, branch, source_target=PLAN)
        await browser.close()
    assert len(pages) == 1, "failed purpose proof must not erase the readable current source"
    proof = evidence["source_roles"][url]
    assert not proof["confirmed"]
    assert any(issue["status"] == "source_role_evidence_unverified" for issue in errors)
    parsed = reconcile_rosters([CapturedRosterPage(url, pages[0][1], branch, source_role_evidence=proof)], PLAN)
    admitted, _, pending = classify_candidates(parsed.candidates, parsed.candidate_sources, {}, PLAN)
    assert len(parsed.candidates) == len(pending) == 13 and not admitted
    original = tmp_path/"source.html"
    original.write_bytes(pages[0][1].encode("utf8"))
    assert "法学专业建设与发展委员会" in original.read_text(encoding="utf8")


@pytest.mark.browser
@pytest.mark.asyncio
async def test_saved_normal_faculty_branding_is_verified_in_actual_browser_and_preserves_all_observations(tmp_path):
    import hashlib
    from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
    from server.harvest_eligibility import classify_candidates
    from test_source_role_qualification import events_for
    from server.source_qualification import source_event_issues
    directory = Path(__file__).parent/"fixtures"/"harvest_acceptance"
    metadata = json.loads((directory/"real_source.json").read_text(encoding="utf8"))
    original = (directory/"real_hnucm_directory.html").read_bytes()
    assert hashlib.sha256(original).hexdigest() == metadata["sha256"]
    target = {"school": "湖南中医药大学", "college": "信息科学与工程学院"}
    branch = {"url": metadata["url"], "card_selector": metadata["selector"], "source_role": "faculty_roster",
              "source_container_selector": "div.list-box ul.list-name", "role_evidence_selector": "ul.current",
              "source_context_selector": ".top-box,.bottom-box"}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        async def serve(route):
            if route.request.url == branch["url"]:
                await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=original)
            else:
                await route.fulfill(status=404, body="")
        await context.route("**/*", serve)
        page = await context.new_page()
        scout = BrowserScout(page, tmp_path/"saved-normal", "branch", target)
        await page.goto(branch["url"], wait_until="networkidle")
        args = {key: branch[key] for key in SOURCE_FIELDS}
        args["unit_selector"] = branch["card_selector"]
        reply = await scout.call("browser_verify_source", args)
        proof = json.loads(reply["content"][0]["text"])
        assert proof["confirmed"] and proof["context_count"] == 2, proof
        assert not source_event_issues(branch, events_for(branch, proof))
        html = await page.content()
        parsed = reconcile_rosters([CapturedRosterPage(branch["url"], html, branch, source_role_evidence=proof)], target)
        admitted, _, pending = classify_candidates(parsed.candidates, parsed.candidate_sources, {}, target)
        assert len(parsed.candidates) == len(admitted) == parsed.observations[0]["visible_cards"] == 60
        assert not pending
        saved, = (tmp_path/"saved-normal").glob("*.source.html")
        assert saved.read_bytes() == html.encode("utf8")
        assert (directory/"real_hnucm_directory.html").read_bytes() == original
        await browser.close()
