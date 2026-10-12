"""A deterministic SDK producer consumes actual phase inputs and real routed scout tools.

The producer is not a language model. Only DeepSeekHarness is replaced: runtime
composition, phase assembly, browser proofs, submit and final recon gates remain real.
"""
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from playwright.async_api import async_playwright

from server import agent_recon
from server.recon_browser_mcp import BrowserScout, phase_tools
from server import source_qualification

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]


class MissingPromptProtocol(agent_recon.ReconError):
    pass


# Compact structural observations, not raw HTML, names or preconstructed plans.
OBSERVE = """() => ({url:location.href,
  navigation:document.querySelectorAll('.groups').length,
  rankGroups:document.querySelectorAll('.rankgroup').length,
  cards:document.querySelectorAll('.cards > li').length,
  next:document.querySelectorAll('a.next').length,
  article:document.querySelectorAll('article').length,
  source: ['main','h1','header'].every(s=>document.querySelectorAll(s).length===1),
  childUrls:[...document.querySelectorAll('.groups a[href]')].map(a=>a.href)
})"""


def documents(legacy=False):
    host = 'https://prompt-fixture.example.edu'
    root = host + '/faculty'
    prefix = '<meta charset="utf-8"><title>教师名录</title><h1>教师名录</h1><header>样本大学 样本学院</header>'
    if legacy:
        return root, {root: prefix + '<main><ul class="cards"><li>王芳</li></ul></main>'}
    pages = {
        root: prefix + '<main><div class="groups"><a href="/grid">师资名录</a>'
                      '<a href="/cards">教师名单</a></div></main>',
        host + '/grid': prefix + '<main><section class="rankgroup"><h2>教授</h2><div class="names">'
                      '<a href="/p/a">王芳</a>、<a href="/p/b">李华</a></div></section></main>'
                      '<a class="next" href="/grid?page=2">下一页</a>',
        host + '/grid?page=2': prefix + '<main><section class="rankgroup"><h2>副教授</h2>'
                      '<div class="names"><a href="/p/c">赵明</a></div></section></main>',
        host + '/cards': prefix + '<main><ul class="cards"><li><a href="/p/d">刘敏</a></li></ul></main>',
    }
    for key, name in [('a', '王芳'), ('b', '李华'), ('c', '赵明'), ('d', '刘敏')]:
        pages[host + '/p/' + key] = f'<article><h1>{name}</h1><p>职称：教授</p><p>完整任职履历与研究方向。</p></article>'
    return root, pages


class Producer:
    def __init__(self, root, pages, evidence, *, omit=None):
        self.root, self.pages, self.evidence, self.omit = root, pages, evidence, omit
        self.inputs, self.requests, self.acceptance = [], [], []
        evidence.mkdir(parents=True, exist_ok=True)

    def harness_class(self):
        producer = self
        class OfflineHarness:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                pass
            def run(self, prompt, *, session_id, on_notification):
                return asyncio.run(producer.produce(prompt, session_id, self.kwargs, on_notification))
        return OfflineHarness

    async def produce(self, prompt, session, kwargs, on_notification):
        config = yaml.safe_load(Path(kwargs['patches'][0]).read_text())
        inserted = [entry for block in config for entry in block.get('insert', [])]
        mcp = next(entry['config'] for entry in inserted if entry['config']['serverName'] == 'playwright')
        phase = mcp['env']['FACULTY_RECON_PHASE']
        assert phase == kwargs['env']['FACULTY_RECON_PHASE']
        target = json.loads(mcp['args'][mcp['args'].index('--target') + 1])
        tools = {item['name']: item for item in phase_tools(phase)}
        self.inputs.append({'phase': phase, 'prompt': prompt, 'tools': tools, 'runtime': config})
        (self.evidence / f'{phase}-input.json').write_text(json.dumps(self.inputs[-1], ensure_ascii=False, indent=2))
        if phase == 'branch':
            # The machine-readable binding example is parsed, not a magic whole plan.
            examples = [line for line in prompt.splitlines() if line.startswith('{"page_types":')]
            if not examples:
                raise MissingPromptProtocol('Actual branch prompt has no allowed page_types binding protocol')
            shape = json.loads(examples[0])['page_types']['roster']
            if set(shape) != {'type_id', 'implementation_version', 'params'}:
                raise MissingPromptProtocol('Actual branch binding protocol is incomplete')
            if 'child_branches' not in prompt or 'browser_verify_stage' not in prompt:
                raise MissingPromptProtocol('Actual branch prompt has no heterogeneous/stage evidence protocol')
        events = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            context = await browser.new_context(service_workers='block')
            async def serve(route):
                self.requests.append(route.request.url)
                await route.fulfill(status=200 if route.request.url in self.pages else 404,
                    content_type='text/html; charset=utf-8', body=self.pages.get(route.request.url, ''))
            await context.route('**/*', serve)
            await context.add_init_script(Path('server/recon_dom_probe.js').read_text())
            scout = BrowserScout(await context.new_page(), self.evidence / session, phase, target)
            async def call(name, args):
                assert name in tools, f'{name} is not exposed in {phase}'
                schema = tools[name]['inputSchema']
                assert set(args) <= set(schema['properties'])
                assert set(schema.get('required', [])) <= set(args)
                for key, value in args.items():
                    if 'enum' in schema['properties'][key]:
                        assert value in schema['properties'][key]['enum']
                reply = await scout.call(name, args)
                cid = str(len(events))
                current = [{'type': 'tool/call', 'data': {'callId': cid,
                    'name': 'mcp__playwright__' + name, 'arguments': json.dumps(args)}},
                    {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': cid, **reply}]}}}]
                events.extend(current)
                for event in current:
                    on_notification(SimpleNamespace(method='session.event', payload={
                        'sessionId': session, 'event': event}))
                return json.loads(reply['content'][0]['text'])
            async def catalog(role, contract):
                offset, entries = 0, []
                while True:
                    result = await call('page_type_catalog', {'role': role, 'offset': offset, 'limit': 4})
                    entries.extend(result['items'])
                    if result.get('nextOffset') is None:
                        break
                    offset = result['nextOffset']
                selected = [item for item in entries if contract in item['applicability_contract'].lower()
                            or contract in item['input_contract'].lower()]
                assert len(selected) == 1, (role, contract, entries)
                return selected[0]
            def ref(definition, **params):
                assert set(params) <= set(definition['params_schema']['properties'])
                return {'type_id': definition['type_id'],
                        'implementation_version': definition['implementation_version'], 'params': params}
            async def observe(url):
                await call('browser_navigate', {'url': url})
                layout = await call('browser_evaluate', {'function': OBSERVE})
                # browser_evaluate wraps compact JSON in result.
                if 'result' in layout:
                    layout = layout['result']
                assert layout['source']
                source = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
                          'role_evidence_selector': 'h1', 'source_context_selector': 'header'}
                return layout, source
            async def source_proof(source, unit=None):
                if self.omit != 'source' or phase == 'discovery':
                    result = await call('browser_verify_source', source | ({'unit_selector': unit} if unit else {}))
                    assert result['confirmed'], result
            async def leaf(url):
                layout, source = await observe(url)
                branch = {'url': layout['url'], **source}
                if layout['rankGroups']:
                    selected = await catalog('roster', 'rank heading')
                    roster = ref(selected, card_selector='.names', container_selector='.rankgroup', record_start_selector='h2')
                    branch.update(card_selector=roster['params']['card_selector'],
                        container_selector=roster['params']['container_selector'],
                        record_start_selector=roster['params']['record_start_selector'], page_types={'roster': roster})
                else:
                    assert layout['cards'] > 0
                    await catalog('roster', 'ordinary roster')
                    branch['card_selector'] = '.cards > li'
                if layout['next']:
                    selected = await catalog('pagination', 'absent terminal control')
                    branch['next_selector'] = 'a.next'
                    branch.setdefault('page_types', {})['pagination'] = ref(selected, next_selector='a.next')
                else:
                    branch['single_page'] = True
                await source_proof(source, branch['card_selector'])
                verification = await call('browser_verify', {key: branch[key] for key in
                    ('card_selector', 'container_selector', 'record_start_selector', 'next_selector', 'page_types') if key in branch})
                assert verification['cardCount'] > 0
                if layout['next'] and self.omit != 'pagination':
                    await call('browser_verify_stage', {'role': 'pagination', 'branch': branch})
                # A multi-person cell can hold several links; the card preview
                # samples only its first. Observe every selected card's actual
                # descendant anchors rather than inventing URL siblings.
                selected_links = await call('browser_evaluate', {'function':
                    '() => ({urls:[...document.querySelectorAll(' + json.dumps(branch['card_selector']) +
                    ')].flatMap(n=>[...(n.matches("a[href]")?[n]:n.querySelectorAll("a[href]"))].map(a=>a.href))})'})
                selected_links = selected_links.get('result', selected_links)
                profiles = list(dict.fromkeys(selected_links['urls']))[:2]
                if profiles:
                    # Discover the profile region from actual DOM before selecting its catalog mechanism.
                    await call('browser_navigate', {'url': profiles[0]})
                    profile = await call('browser_evaluate', {'function': '() => ({article:document.querySelectorAll("article").length})'})
                    profile = profile.get('result', profile)
                    assert profile['article'] == 1
                    selected = await catalog('detail', 'semantic article')
                    branch['profile_content_selector'] = 'article'
                    branch.setdefault('page_types', {})['detail'] = ref(selected, profile_content_selector='article')
                    await call('browser_verify_stage', {'role': 'detail', 'branch': branch, 'urls': profiles})
                return branch
            try:
                if phase == 'discovery':
                    layout, source = await observe(target['url'])
                    await source_proof(source)
                    plan = {'school': target['school'], 'college': target['college'],
                            'branches': [{'url': layout['url'], **source}]}
                else:
                    layout, source = await observe(target['url'])
                    if layout['navigation']:
                        selected = await catalog('entrance', 'descendant anchors')
                        entrance = ref(selected, child_link_selector='.groups')
                        await source_proof(source, entrance['params']['child_link_selector'])
                        children = [await leaf(url) for url in layout['childUrls']]
                        plan = {'url': layout['url'], **source, 'kind': 'navigation', 'single_page': True,
                                'child_link_selector': entrance['params']['child_link_selector'],
                                'child_branches': children, 'page_types': {'entrance': entrance}}
                        await call('browser_verify_stage', {'role': 'entrance', 'branch': plan})
                    else:
                        plan = await leaf(layout['url'])
                result = await call('submit_plan', {'plan': plan})
                self.acceptance.append(result)
                (self.evidence / f'{phase}-submission.json').write_text(json.dumps({'plan': plan, 'result': result}, ensure_ascii=False, indent=2))
                (self.evidence / f'{phase}-events.json').write_text(json.dumps(events, ensure_ascii=False, indent=2))
                if not result['accepted']:
                    error = agent_recon.ReconPlanError('Actual scout submission rejected the producer plan')
                    error.safe_feedback = result
                    raise error
                return SimpleNamespace(events=events, final_response='计划已提交', finish_reason='completed')
            finally:
                await context.close()
                await browser.close()


async def run_producer(monkeypatch, tmp_path, *, legacy=False, omit=None):
    import deepseek_harness
    assert source_qualification.verify_source.__module__ == 'server.source_qualification'
    assert agent_recon._run_staged_harness.__module__ == 'server.agent_recon'
    root, pages = documents(legacy)
    evidence = Path(os.getenv('RUNTIME_PROMPT_EVIDENCE_DIR', str(tmp_path))) / ('legacy' if legacy else (omit or 'complete'))
    producer = Producer(root, pages, evidence, omit=omit)
    monkeypatch.setattr(deepseek_harness, 'DeepSeekHarness', producer.harness_class())
    config = {'dsh_home': str(tmp_path / 'sdk-home'), 'dsh_bin': None, 'workspace': str(Path.cwd()),
              'profile': 'sdk', 'provider': 'offline', 'model': 'no-model', 'effort': 'high',
              'api_key': 'offline-not-used', 'patches': (str(Path('server/recon_runtime.cordis.yml').resolve()),),
              'parallel_branches': False, 'template_reuse': False, 'require_submission': True}
    try:
        result = await agent_recon.reconnoiter('样本大学', '样本学院', root, config=config,
            timeout_seconds=180, session_id='runtime-prompt-' + ('legacy' if legacy else (omit or 'complete')))
        (evidence / 'accepted-plan.json').write_text(json.dumps(result.plan, ensure_ascii=False, indent=2))
        assert result.diagnostics['model_calls'] == 0
        return result, producer
    finally:
        (evidence / 'requests.json').write_text(json.dumps(producer.requests, indent=2))
        assert all(url in pages for url in producer.requests)


async def test_actual_staged_prompt_guides_catalog_heterogeneous_bindings_and_structured_acceptance(monkeypatch, tmp_path):
    result, producer = await run_producer(monkeypatch, tmp_path)
    [parent] = result.plan['branches']
    assert parent['page_types']['entrance']['type_id'] == 'entrance.scoped_links'
    assert len(parent['child_branches']) == 2
    assert len({branch['url'] for branch in parent['child_branches']}) == 2
    assert parent['child_branches'][0]['page_types']['roster']['type_id'] == 'roster.rank_grid'
    assert 'page_types' not in parent['child_branches'][1] or 'roster' not in parent['child_branches'][1]['page_types']
    assert all(reply['accepted'] for reply in producer.acceptance)
    assert [item['phase'] for item in producer.inputs] == ['discovery', 'branch']
    assert result.finish_reason == 'completed'


async def test_legacy_card_plan_still_passes_actual_runtime_and_source_gate(monkeypatch, tmp_path):
    result, _producer = await run_producer(monkeypatch, tmp_path, legacy=True)
    [branch] = result.plan['branches']
    assert branch['single_page'] is True and 'page_types' not in branch and 'child_branches' not in branch


@pytest.mark.parametrize('omit', ['pagination', 'source'])
async def test_missing_stage_or_source_proof_is_rejected_by_actual_scout(monkeypatch, tmp_path, omit):
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        await run_producer(monkeypatch, tmp_path, omit=omit)
    evidence = Path(os.getenv('RUNTIME_PROMPT_EVIDENCE_DIR', str(tmp_path))) / omit
    actual_reply = json.loads((evidence / 'branch-submission.json').read_text())['result']
    assert actual_reply['accepted'] is False
    issues = caught.value.safe_feedback['issues']
    assert any(item['status'] == ('stage_type_evidence_unverified' if omit == 'pagination'
                                else 'source_role_evidence_unverified') for item in issues)
