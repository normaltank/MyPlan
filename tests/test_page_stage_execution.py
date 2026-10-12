"""Actual selected stage scripts in a fully routed browser; no remote services."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest
import pytest_asyncio
from playwright.async_api import async_playwright

from server import page_types as types
from server.harvest_state import HarvestState
from server.harvest_workflow import capture_profiles, capture_sources, capture_profile_stage
from server.browser_batch_tool import _capture_branch
from server.page_stage_types import digest

pytestmark = pytest.mark.browser
ROOT = 'https://stages.example.edu'


def binding(type_id, **params):
    return {'type_id': type_id, 'implementation_version': 1, 'params': params}


@pytest_asyncio.fixture
async def routed():
    documents, requests = {}, []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(service_workers='block')
        async def serve(route):
            requests.append(route.request.url)
            await route.fulfill(status=200 if route.request.url in documents else 404,
                content_type='text/html; charset=utf-8', body=documents.get(route.request.url, ''))
        await context.route('**/*', serve)
        try:
            yield context, documents, requests
        finally:
            await context.close()
            await browser.close()


@pytest.mark.asyncio
async def test_container_entrance_returns_sources_and_public_layer_always_pages_each_leaf(routed):
    context, documents, requests = routed
    root, a, b = [ROOT + path for path in ('/groups', '/a', '/b')]
    documents[root] = '<nav class="groups"><a href="/a">A</a><a href="/b">B</a></nav>'
    branch = {'url': root, 'kind': 'navigation', 'child_branches': [
        {'url': a, 'card_selector': '.person', 'single_page': True},
        {'url': b, 'card_selector': '.person', 'single_page': True}], 'page_types': {
        'entrance': binding('entrance.scoped_links', child_link_selector='.groups')}}
    visited = []
    async def leaf_pagination(_context, child):
        visited.append(child['url'])
        return [(child['url'], '<div class="person">王芳</div>')], [], {
            'url': child['url'], 'end': 'single_page_no_pagination_control_seen',
            'executed_page_type_roles': ['pagination']}
    async def no_legacy_navigation(*args):
        pytest.fail('a selected scoped-links handler must discover its own structure')
    captured = await capture_sources({'branches': [branch]}, context,
        capture_branch=leaf_pagination, discover_children=no_legacy_navigation)
    assert captured.unresolved == [] and visited == [a, b]
    assert [page.url for page in captured.roster_pages] == [a, b]
    assert captured.branches[0]['stage_evidence']['payload']['source_urls'] == [a, b]
    assert captured.branches[0]['executed_page_type_roles'] == ['entrance']
    assert all(child['executed_page_type_roles'] == ['entrance', 'pagination']
               for child in captured.branches[0]['child_visits'])
    assert requests == [root]


@pytest.mark.asyncio
async def test_entrance_budget_blocker_keeps_full_discovery_but_bounds_leaf_capture(routed):
    context, documents, _requests = routed
    root, a, b = [ROOT + path for path in ('/groups', '/a', '/b')]
    documents[root] = '<nav class="groups"><a href="/a">A</a><a href="/b">B</a></nav>'
    branch = {'url': root, 'kind': 'navigation', 'child_branches': [
        {'url': a, 'single_page': True}, {'url': b, 'single_page': True}], 'page_types': {
        'entrance': binding('entrance.scoped_links', child_link_selector='.groups', max_children=1)}}
    visited = []
    async def leaf(context, child):
        visited.append(child['url'])
        return [(child['url'], '<main></main>')], [], {'url': child['url']}
    result = await capture_sources({'branches': [branch]}, context, capture_branch=leaf, discover_children=None)
    assert visited == [a]
    assert result.branches[0]['children'] == [a, b]
    assert any(item['status'] == 'child_limit_reached' for item in result.unresolved)
    assert 'stage_evidence' not in result.branches[0]


def pagination_documents(documents):
    first, last = ROOT + '/list', ROOT + '/list?page=2'
    documents[first] = '<main><div class="person">王芳</div></main><a class="next" href="/list?page=2">下一页</a>'
    documents[last] = '<main><div class="person">李华</div></main>'
    return {'url': first, 'card_selector': '.person', 'page_types': {
        'pagination': binding('pagination.verified_next', next_selector='a.next', max_pages=3)}}


@pytest.mark.asyncio
async def test_next_stage_executes_click_and_validates_all_page_originals(routed):
    context, documents, requests = routed
    branch = pagination_documents(documents)
    sink = {}
    async def no_legacy_capture(*args, **kwargs):
        pytest.fail('the new next-stage implementation must execute its own bounded loop')
    proof = await types.run_stage_probe('pagination', branch, no_legacy_capture, context,
        types.normalize_branch(branch), requested_url=branch['url'], local_capture=sink)
    result = sink['result']
    assert len(result[0]) == 2 and result[1] == [] and result[2]['end'] == 'next_control_absent'
    assert proof['payload']['next_clicks'] == 1 and proof['payload']['pages'][1]['forward_control_count'] == 0
    assert types.validate_stage_evidence('pagination', branch, proof, requested_url=branch['url'],
        final_url=result[0][0][0], source_html_sha256=digest(result[0][0][1]),
        source_html=result[0][0][1], result=result)
    assert requests == [ROOT + '/list', ROOT + '/list?page=2']
    assert '王芳' not in json.dumps(proof, ensure_ascii=False)


@pytest.mark.asyncio
async def test_terminal_flags_cannot_hide_an_unknown_control_in_saved_originals(routed):
    context, documents, _requests = routed
    branch = pagination_documents(documents)
    sink = {}
    proof = await types.run_stage_probe('pagination', branch, _capture_branch, context,
        types.normalize_branch(branch), requested_url=branch['url'], local_capture=sink)
    originals = list(sink['result'][0])
    url, html = originals[-1]
    html += '<a class="moved" href="/list?page=3">下一页</a>'
    originals[-1] = (url, html)
    forged = deepcopy(proof)
    forged['payload']['pages'][-1]['sha256'] = digest(html)
    forged['payload']['pagination_termination']['source_sha256'] = digest(html)
    evidence = {'end': forged['payload']['end'], 'next_clicks': forged['payload']['next_clicks'],
                'pagination_termination': forged['payload']['pagination_termination']}
    assert not types.validate_stage_evidence('pagination', branch, forged, requested_url=branch['url'],
        final_url=originals[0][0], source_html_sha256=digest(originals[0][1]),
        source_html=originals[0][1], result=(originals, [], evidence))


@pytest.mark.asyncio
async def test_explicit_profile_result_needs_no_callback_side_effect_and_is_saved_centrally(routed, monkeypatch, tmp_path):
    context, documents, _requests = routed
    url = ROOT + '/person'
    documents[url] = '<article class="bio"><h1>王芳</h1><p>教授，研究代数与教学。</p></article>'
    branch = {'url': ROOT + '/list', 'profile_content_selector': 'article.bio', 'page_types': {
        'detail': binding('detail.article_region', profile_content_selector='article.bio')}}
    sink = {}
    proof = await types.run_stage_probe('detail', branch, capture_profile_stage, context, url,
        types.normalize_branch(branch), requested_url=url, scope_pairs={('article.bio', None)}, local_capture=sink)
    result = sink['result']
    assert isinstance(result, types.ProfileCapture) and result.proof['confirmed']
    assert '王芳' not in json.dumps(proof, ensure_ascii=False)
    # A trusted alternative can supply a previously proved result directly;
    # capture_profiles, rather than a callback closure, owns all persistence.
    monkeypatch.setattr(types, '_REGISTRY', dict(types._REGISTRY))
    original = types._REGISTRY[('detail.article_region', 1)]
    def explicit_result(_operation, *_args, **_kwargs):
        return result
    types.register_type(replace(original, type_id='detail.explicit_result', implementation=explicit_result))
    branch['page_types']['detail']['type_id'] = 'detail.explicit_result'
    state = HarvestState(tmp_path, {'branches': [branch]})
    profiles, execution = {}, {}
    class NoBrowser:
        async def new_page(self):
            pytest.fail('the explicit result handler must not need callback/browser side effects')
    captured, issues = await capture_profiles(NoBrowser(), [url], 1, state=state,
        profile_type_branches={url: [branch]}, profile_content_evidence=profiles, profile_type_evidence=execution)
    assert issues == [] and captured[url] == result.html == state.load_page('profile', url)
    assert profiles[url]['html_sha256'] == digest(result.html)
    assert execution[url]['stage_evidence']['type_id'] == 'detail.explicit_result'


@pytest.mark.asyncio
async def test_detail_parameter_conflict_keeps_raw_original_and_blocks_proof(routed, tmp_path):
    context, documents, _requests = routed
    url = ROOT + '/person'
    documents[url] = '<article class="one"><h1>王芳</h1><p>教授</p></article><article class="two"><p>另一份正文</p></article>'
    branches = [{'url': ROOT + '/list', 'page_types': {'detail': binding('detail.article_region',
                  profile_content_selector=selector)}} for selector in ('article.one', 'article.two')]
    state = HarvestState(tmp_path, {'branches': branches})
    evidence = {}
    pages, issues = await capture_profiles(context, [url], 1, state=state,
        profile_type_branches={url: branches}, profile_content_evidence=evidence)
    assert pages[url] and pages[url] == state.load_page('profile', url)
    assert any(item['status'] == 'profile_page_type_conflict' for item in issues)
    assert evidence == {} and '另一份正文' in pages[url]
