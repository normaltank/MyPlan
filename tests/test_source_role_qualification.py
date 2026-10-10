"""Actual committee originals cannot donate university faculty eligibility."""
import hashlib
import json
from pathlib import Path
import pytest

from bs4 import BeautifulSoup

from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
from server.harvest_eligibility import classify_candidates
from server.source_qualification import SOURCE_FIELDS, captured_source_evidence, witness
from server.source_qualification import source_event_issues

FIXTURES = Path(__file__).parent/'fixtures'/'source-qualification'
PLAN = {'school': '皖西学院', 'college': '法学院', 'source_kind': 'university_faculty_roster'}
CASES = [
    ('https://zfxy.wxc.edu.cn/2019/0620/c323a114577/page.htm', '73b37664977d55a513c59e31c133a06123f3a6868a822705469b4bb22cf3b62c', 'div.wp_articlecontent > div.Article_Content > p.MsoNormal', 'organizational_membership'),
    ('https://zfxy.wxc.edu.cn/2009/1001/c323a20946/page.htm', 'b7ab38d9765d4386e3bbc9943836989fc0b7af0d2124fab7f22e3d2177f6389c', 'div.Article_Content > p[style*="widow-orphan"]:has(> span)', 'organizational_membership'),
    ('https://zfxy.wxc.edu.cn/327/list.htm', 'b966d1bf9301faff0f53fdfccb12d963d2468ece1dec64598523a057dbd8ecea', '#wp_news_w3 ul.wp_article_list > li.list_item', 'faculty_roster'),
]


def test_saved_committee_membership_is_not_faculty_relationship():
    observations = 0
    for url, sha, selector, _ in CASES[:2]:
        raw = (FIXTURES/f'{sha}.html').read_bytes()
        assert hashlib.sha256(raw).hexdigest() == sha
        parsed = reconcile_rosters([CapturedRosterPage(url, raw.decode('utf8'), {'url': url, 'card_selector': selector})], PLAN)
        admitted, decisions, unresolved = classify_candidates(parsed.candidates, parsed.candidate_sources, {}, PLAN)
        observations += len(parsed.candidates)
        assert not admitted, 'a global university flag cannot qualify committee members'
        assert len(decisions) == len(parsed.candidates) == len(unresolved)
        assert all(item['status'] == 'source_qualification_missing' for item in unresolved)
    assert observations == 15


def claim(url, sha, selector, role):
    html = (FIXTURES/f'{sha}.html').read_text(encoding='utf8')
    soup = BeautifulSoup(html, 'html.parser')
    footer = next(node for node in soup.find_all('div') if node.get('class') and 'Copyright' in node.get_text() and len(node.get_text()) < 230)
    context_selector = 'div.' + '.'.join(footer['class'])
    return html, {'url': url, 'card_selector': selector, 'source_role': role,
                  'source_container_selector': '#wp_news_w3' if role == 'faculty_roster' else 'div.Article_Content',
                  'role_evidence_selector': '.subpostion', 'source_context_selector': context_selector}


def test_saved_faculty_source_has_positive_relationship_witness():
    url, sha, selector, role = CASES[2]
    html, branch = claim(url, sha, selector, role)
    observed = witness(html, url, branch, PLAN)
    assert observed['role_supported']
    proof = {**observed, 'confirmed': True, 'dom_stable': True, 'network_idle': True, 'witness_visible': True}
    evidence = captured_source_evidence(html, url, branch, PLAN, proof)
    assert evidence.status == 'verified'
    assert captured_source_evidence(html, url, branch, PLAN).status == 'unknown'
    # A pager can change the current URL while its terminal breadcrumb still
    # names the actual document title; this is not URL-prefix inference.
    assert witness(html, 'https://zfxy.wxc.edu.cn/327/list2.htm', branch, PLAN)['role_supported']
    parsed = reconcile_rosters([CapturedRosterPage(url, html, branch, source_role_evidence=proof)], PLAN)
    admitted, decisions, unresolved = classify_candidates(parsed.candidates, parsed.candidate_sources, {}, PLAN)
    assert len(admitted) == 5 and not unresolved
    assert all(item['evidence_kind'] == 'verified_faculty_relationship' for item in decisions)


def test_claimed_faculty_role_does_not_qualify_saved_committee_page():
    for url, sha, selector, role in CASES[:2]:
        html, branch = claim(url, sha, selector, role)
        branch['source_role'] = 'faculty_roster'
        observed = witness(html, url, branch, PLAN)
        assert not observed['role_supported']
        assert all(key in observed for key in SOURCE_FIELDS)


def events_for(branch, observed):
    args = {key: branch[key] for key in SOURCE_FIELDS}
    if branch.get('card_selector'):
        args['unit_selector'] = branch['card_selector']
    return [{'type': 'tool/call', 'data': {'callId': 'source', 'name': 'mcp__playwright__browser_verify_source', 'arguments': json.dumps(args)}},
            {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': 'source', 'content': [{'type': 'text', 'text': json.dumps(observed)}]}]}}}]


def test_source_gate_requires_current_dedicated_bound_proof():
    url, sha, selector, role = CASES[2]
    html, branch = claim(url, sha, selector, role)
    observed = {**witness(html, url, branch, PLAN), 'confirmed': True, 'dom_stable': True, 'network_idle': True, 'witness_visible': True}
    events = events_for(branch, observed)
    assert not source_event_issues(branch, events)
    assert source_event_issues(branch, [])
    assert source_event_issues({**branch, 'source_container_selector': '#other'}, events)
    assert source_event_issues({**branch, 'url': url+'?changed'}, events)
    assert captured_source_evidence(html+'<!--changed-->', url, branch, PLAN, observed).status == 'unknown'
    assert captured_source_evidence(html, url, branch, PLAN, {**observed, 'network_idle': False}).status == 'unknown'


def test_cards_outside_relationship_scope_are_not_qualified():
    html = '<title>教师名单</title><header>皖西学院法学院</header><h1>教师名单</h1><main id="scope"><li>王芳</li></main><aside><li>李华</li></aside>'
    branch = {'url': 'https://example.edu/list', 'source_role': 'faculty_roster', 'source_container_selector': '#scope',
              'role_evidence_selector': 'h1', 'source_context_selector': 'header', 'card_selector': 'li'}
    assert not witness(html, branch['url'], branch, PLAN)['role_supported']
    branch['card_selector'] = '#scope > li'
    assert witness(html, branch['url'], branch, PLAN)['role_supported']
    ambiguous_title = html.replace('教师名单', '师资队伍建设委员会')
    assert not witness(ambiguous_title, branch['url'], branch, PLAN)['role_supported']


def test_same_name_cannot_transfer_source_qualification():
    from server.faculty_extractor import FacultyCandidate
    from server.faculty_identity import merge_same_name_observations
    from server.source_qualification import SourceEvidence
    faculty = FacultyCandidate('王芳', 'https://example.edu/p/1')
    committee = FacultyCandidate('王芳', 'https://example.edu/members')
    sources = {(faculty.name, faculty.url): {SourceEvidence('https://example.edu/faculty', 'faculty_roster', 'verified', 'a'*64)},
               (committee.name, committee.url): {SourceEvidence(committee.url, 'organizational_membership', 'verified', 'b'*64)}}
    kept, current, merges = merge_same_name_observations([faculty, committee], sources, {committee.url})
    assert kept == [faculty, committee] and current == sources and not merges
    admitted, _, unresolved = classify_candidates(kept, current, {}, PLAN)
    assert admitted == [faculty] and len(unresolved) == 1 and unresolved[0]['url'] == committee.url


def test_structure_contract_and_tools_carry_roles_without_teacher_fields():
    import pytest
    from server.recon_contract import Branch, DiscoveredBranch
    from server.recon_browser_mcp import phase_tools
    from server.agent_recon import PlanHygieneError, validate_model_plan
    _, branch = claim(*CASES[2])
    branch['single_page'] = True
    assert Branch.model_validate(branch)
    assert DiscoveredBranch.model_validate({key: value for key, value in branch.items() if key in SOURCE_FIELDS or key == 'url'})
    validate_model_plan({**PLAN, 'branches': [branch]}, school=PLAN['school'], college=PLAN['college'])
    with pytest.raises(PlanHygieneError):
        validate_model_plan({**PLAN, 'branches': [{**branch, 'teacher_name': '王芳'}]}, school=PLAN['school'], college=PLAN['college'])
    from server.browser_batch_tool import _validate_plan
    for field in ('verified_candidates', 'verified_appointments', 'reviewed_exclusions'):
        with pytest.raises(ValueError, match='no longer accepted'):
            _validate_plan({**PLAN, 'branches': [branch], field: []})
    with pytest.raises(ValueError, match='only supported structure fields'):
        _validate_plan({**PLAN, 'branches': [branch], 'arbitrary_teacher_json': [{'name': '王芳'}]})
    for phase in ('discovery', 'branch', 'full'):
        assert 'browser_verify_source' in {tool['name'] for tool in phase_tools(phase)}


def test_loaded_recon_prompts_format_the_new_self_contained_contract():
    from server import agent_recon
    from server.recon_plan_cache import _contract
    for prompt in (agent_recon.DISCOVERY_PROMPT, agent_recon.PROMPT):
        rendered = prompt.format(school=PLAN['school'], college=PLAN['college'], url=CASES[2][0])
        assert 'browser_verify_source' in rendered and 'source_role' in rendered
    assert 'browser_verify_source' in agent_recon.PROVIDED_LINKS_PROMPT
    assert len(_contract()) == 64


def test_all_discovery_role_proofs_are_required_and_literal_plan_values_stay_safe():
    from server.recon_contract import validate_submission
    url, sha, selector, role = CASES[2]
    html, branch = claim(url, sha, selector, role)
    discovery_branch = {key: value for key, value in branch.items() if key in SOURCE_FIELDS or key == 'url'}
    discovery = {'school': PLAN['school'], 'college': PLAN['college'], 'branches': [discovery_branch]}
    proof = {**witness(html, url, discovery_branch, PLAN), 'confirmed': True, 'dom_stable': True, 'network_idle': True, 'witness_visible': True}
    navigation = [{'type': 'tool/call', 'data': {'callId': 'nav', 'name': 'mcp__playwright__browser_navigation', 'arguments': '{}'}},
                  {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': 'nav', 'content': [{'type': 'text', 'text': json.dumps({'items': [{'url': url}]})}]}]}}}]
    target = {'school': PLAN['school'], 'college': PLAN['college'], 'url': url}
    assert not validate_submission(discovery, 'discovery', target, navigation + events_for(discovery_branch, proof))
    assert any(issue['status'] == 'source_role_evidence_unverified' for issue in validate_submission(discovery, 'discovery', target, navigation))


@pytest.mark.asyncio
async def test_actual_run_plan_keeps_membership_observations_and_formal_records(tmp_path, monkeypatch):
    from server import browser_batch_tool as batch
    from server.harvest_workflow import SourceCapture
    pages, branches = [], []
    for case in CASES:
        html, branch = claim(*case)
        branch['single_page'] = True
        proof = {**witness(html, branch['url'], branch, PLAN), 'confirmed': True, 'dom_stable': True, 'network_idle': True, 'witness_visible': True}
        pages.append(CapturedRosterPage(branch['url'], html, branch, source_role_evidence=proof))
        branches.append(branch)
    plan = {**PLAN, 'branches': branches}
    parsed = reconcile_rosters(pages, plan)
    name_by_url = {candidate.url: candidate.name for candidate in parsed.candidates if candidate.url not in {page.url for page in pages}}
    async def capture(*args):
        return SourceCapture(roster_pages=pages), {}
    async def profiles(_context, urls, *_args, **_kwargs):
        return {url: f'<article><h1>{name_by_url[url]}</h1><p>职称：教授</p><p>完整本人科研经历。</p></article>' for url in urls}, []
    monkeypatch.setattr(batch, '_capture_sources_with_state', capture)
    monkeypatch.setattr(batch, 'capture_profiles', profiles)
    result = await batch.run_plan(plan, object(), tmp_path/'output')
    assert result['candidate_count'] == 20 and result['total'] == 5
    assert not result['delivery_ready'] and result['excel_path'] is None
    pending = [item for item in result['unresolved'] if item['status'] == 'source_qualification_missing']
    assert len(pending) == 15 and len(result['eligibility']) == 20
    assert all(item['status'] != 'excluded' for item in result['eligibility'])
    assert len(result['source_qualification_evidence']) == 3
    for snapshot in result['snapshots']:
        assert Path(snapshot['path']).is_file()
    json.dumps(result, ensure_ascii=False)


@pytest.mark.asyncio
async def test_pdf_reader_propagates_relationship_proof_without_legacy_admission(tmp_path):
    from server.pdf_roster import read_pdf_rosters
    from test_pdf_roster import TEXT_PAGES, _pdf
    from server.source_qualification import SourceEvidence
    from server.faculty_extractor import FacultyCandidate
    evidence = SourceEvidence('https://example.edu/roster', 'organizational_membership', 'verified', 'a'*64)
    data = _pdf(TEXT_PAGES)
    async def fetch(_):
        return data
    result = await read_pdf_rosters([{'url': evidence.source_url, 'media_urls': ['https://example.edu/list.pdf'],
        'source_kind': 'university_faculty_roster', 'source_evidence': evidence}], fetch_asset=fetch,
        evidence_dir=tmp_path, ocr_engine=object())
    assert len(result['records']) == 3
    assert all(record.source_evidence is evidence for record in result['records'])
    candidates = [FacultyCandidate(record.name, record.page_url) for record in result['records']]
    sources = {(record.name, record.page_url): {record.source_evidence} for record in result['records']}
    admitted, _, unresolved = classify_candidates(candidates, sources, {}, PLAN)
    assert not admitted and len(unresolved) == 3


@pytest.mark.asyncio
async def test_auxiliary_profile_media_original_is_kept_without_donating_a_faculty_row(tmp_path, monkeypatch):
    from io import BytesIO
    import sys
    from types import SimpleNamespace
    from PIL import Image
    from server import browser_batch_tool as batch
    from server.harvest_workflow import SourceCapture
    url = 'https://example.edu/membership'
    html = '<title>组织成员</title><header>皖西学院法学院</header><h1>组织成员</h1><main><p>王芳 教授</p><img src="/resume.png"></main>'
    branch = {'url': url, 'source_role': 'organizational_membership', 'source_container_selector': 'main',
              'role_evidence_selector': 'h1', 'source_context_selector': 'header', 'card_selector': 'main > p', 'single_page': True}
    proof = {**witness(html, url, branch, PLAN), 'confirmed': True, 'dom_stable': True, 'network_idle': True, 'witness_visible': True}
    buffer = BytesIO()
    Image.new('RGB', (300, 400), 'white').save(buffer, format='PNG')
    original = buffer.getvalue()
    class Response:
        ok = True
        async def body(self):
            return original
    class Request:
        async def get(self, *_args, **_kwargs):
            return Response()
    async def capture(*args):
        return SourceCapture(roster_pages=[CapturedRosterPage(url, html, branch, source_role_evidence=proof)]), {}
    async def profiles(*args, **kwargs):
        return {}, []
    def ocr(_):
        return [( [[0, i*30], [280, i*30], [280, i*30+20], [0, i*30+20]], text, .99)
                for i, text in enumerate(['王芳 教授', '完整本人研究方向与科研经历。', '完整教师原始履历继续。'])], None
    monkeypatch.setitem(sys.modules, 'rapidocr_onnxruntime', SimpleNamespace(RapidOCR=lambda: ocr))
    monkeypatch.setattr(batch, '_capture_sources_with_state', capture)
    monkeypatch.setattr(batch, 'capture_profiles', profiles)
    result = await batch.run_plan({**PLAN, 'branches': [branch]}, SimpleNamespace(request=Request()), tmp_path/'output')
    assert result['candidate_count'] == 1 and result['total'] == 0 and not result['delivery_ready']
    assert any(issue['status'] == 'source_qualification_missing' for issue in result['unresolved'])
    saved, = result['image_biography_evidence']
    assert Path(saved['original_path']).read_bytes() == original
    assert saved['sha256'] == hashlib.sha256(original).hexdigest()


@pytest.mark.parametrize('change,eligible', [
    ('', True),
    ('missing_browser_proof', False),
    ('education_only', False),
    ('wrong_owner', False),
    ('shared_owner', False),
    ('wrong_college', False),
    ('rank_only', False),
])
def test_independent_owned_current_profile_relationship(change, eligible):
    from server.faculty_extractor import FacultyCandidate
    from server.profile_content import scope_profile_html
    from server.source_qualification import SourceEvidence
    url = 'https://example.edu/p/1'
    html = '<article><h1>王芳</h1><p>职称：讲师（外聘副教授）</p><p>身份：外聘教师</p><p>工作单位：皖西学院法学院</p><p>完整履历。</p></article>'
    if change == 'education_only':
        html = html.replace('工作单位：', '毕业院校：')
    if change == 'wrong_owner':
        html = html.replace('<h1>王芳</h1>', '<h1>李华</h1>')
    if change == 'wrong_college':
        html = html.replace('皖西学院法学院', '皖西学院理学院')
    if change == 'rank_only':
        html = html.replace('<p>身份：外聘教师</p>', '')
    _, current, issue = scope_profile_html(html, 'article', url)
    assert not issue
    proof = {'confirmed': True, 'container_visible': True, 'dom_stable': True, 'network_idle': True,
             'html_sha256': hashlib.sha256(html.encode()).hexdigest(), 'selected_html_sha256': current['selected_html_sha256']}
    scope = {url: {**current, 'profile_content_browser_evidence': proof}}
    if change == 'missing_browser_proof':
        scope = {}
    candidates = [FacultyCandidate('王芳', url)]
    if change == 'shared_owner':
        candidates.append(FacultyCandidate('李华', url))
    sources = {('王芳', url): {SourceEvidence('https://example.edu/membership', 'organizational_membership', 'verified', 'a'*64)}}
    admitted, decisions, unresolved = classify_candidates(candidates, sources, {url: html}, PLAN,
                                                         profile_scope_evidence=scope)
    assert bool(admitted) is eligible
    assert bool(unresolved) is not eligible
    if eligible:
        independent = decisions[0]['source_evidence'][-1]
        assert independent['role'] == 'faculty_profile_appointment' and independent['owner_name'] == '王芳'
