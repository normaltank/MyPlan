"""Explicit trusted-source boundary for component tests unrelated to purpose.

These tests already stipulate a formal faculty source and exercise pagination,
parsing, cost or repair controls. This fixture replaces only the source-proof
producer, while the production schema/gate/eligibility still execute. Source
qualification, false-claim and actual DOM/browser tests never use this fixture.
"""
import json
from types import SimpleNamespace

from server.source_qualification import SOURCE_FIELDS, SourceEvidence, digest, witness, source_unit_selector

CLAIM = {'source_role': 'faculty_roster', 'source_container_selector': 'main',
         'role_evidence_selector': 'h1', 'source_context_selector': 'header'}


def upgrade(node):
    if not isinstance(node, dict):
        return node
    if 'branches' in node:
        for branch in node['branches']:
            upgrade(branch)
            if node.get('source_kind') == 'hospital_doctor_roster' and not branch.get('source_kind'):
                branch['source_role'] = 'unverified_people_source'
    elif 'url' in node or 'card_selector' in node or 'container_selector' in node:
        for key, value in CLAIM.items():
            node.setdefault(key, value)
        if node.get('source_kind') == 'hospital_doctor_roster':
            node['source_role'] = 'unverified_people_source'
        if 'child_branch' in node:
            upgrade(node['child_branch'])
    return node


def proof(branch, target=None):
    target = {**{'school': '样本大学', 'college': '样本学院'}, **(target or {})}
    html = '<title>教师名单</title><header>' + target['school'] + target['college'] + '</header><h1>教师名单</h1><main><li>固定组件来源</li></main>'
    data = witness(html, branch.get('url', 'https://example.edu/'), branch, target)
    data.update(confirmed=True, role_supported=True, dom_stable=True, network_idle=True, witness_visible=True,
                unit_selector=source_unit_selector(branch), units_within_scope=True)
    return data


def source_events(branch, target=None, cid='fixture-source'):
    upgrade(branch)
    args = {key: branch[key] for key in SOURCE_FIELDS}
    if source_unit_selector(branch):
        args['unit_selector'] = source_unit_selector(branch)
    return [{'type': 'tool/call', 'data': {'callId': cid, 'name': 'mcp__playwright__browser_verify_source', 'arguments': json.dumps(args)}},
            {'type': 'tool/result', 'data': {'message': {'content': [{'toolCallId': cid, 'content': [{'type': 'text', 'text': json.dumps(proof(branch, target))}]}]}}}]


def install_component_source_boundary(monkeypatch):
    from server import agent_recon, browser_batch_tool, harvest_workflow
    from server import recon_contract
    original_validation = agent_recon.validate_model_plan
    original_batch_validation = browser_batch_tool._validate_plan
    original_selector = agent_recon.selector_evidence_issues
    original_staged = agent_recon._run_staged_harness
    original_submission = recon_contract.validate_submission
    from server.harvest_state import HarvestState
    original_state_init = HarvestState.__init__
    def state_init(self, directory, plan, **kwargs):
        upgrade(plan)
        return original_state_init(self, directory, plan, **kwargs)

    def validate(plan, **kwargs):
        upgrade(plan)
        return original_validation(plan, **kwargs)
    def batch_validate(plan):
        upgrade(plan)
        return original_batch_validation(plan)
    def submission(plan, phase, target, events):
        upgrade(plan)
        added = list(events)
        observed = agent_recon.observed_browser_urls(events)
        for i, branch in enumerate(plan.get('branches', [plan])):
            if phase != 'discovery' or branch.get('url') in observed:
                added.extend(source_events(branch, target, 'fixture-submit-'+str(i)))
        return original_submission(plan, phase, target, added)
    def selector(plan, events, *args):
        upgrade(plan)
        added = list(events)
        for i, branch in enumerate(plan['branches']):
            added.extend(source_events(branch, plan, 'fixture-source-'+str(i)))
            if branch.get('child_branch'):
                # Child purpose is a separate stipulated boundary too. Card
                # evidence is still required by the original checker.
                for event in events:
                    data = event.get('data') or {}
                    for block in (data.get('message') or {}).get('content', []):
                        for content in block.get('content', []):
                            if content.get('type') == 'text':
                                try:
                                    result = json.loads(content['text'])
                                except (TypeError, ValueError):
                                    continue
                                if isinstance(result, dict) and result.get('url') and result['url'] != branch['url']:
                                    added.extend(source_events({**branch['child_branch'], 'url': result['url']}, plan,
                                                               'fixture-child-'+str(i)+result['url']))
        return original_selector(plan, added, *args)
    def staged(sid, cfg, patches):
        current_model = agent_recon._execute_harness
        def model(*args, **kwargs):
            result = current_model(*args, **kwargs)
            value = agent_recon.extract_plan_json(getattr(result, 'final_response', '') or '')
            if isinstance(value, dict):
                upgrade(value)
                events = list(getattr(result, 'events', []))
                branches = value.get('branches', [value])
                for i, branch in enumerate(branches):
                    if 'url' in branch:
                        events.extend(source_events(branch, cfg['target'], sid+'-source-'+str(i)))
                result = SimpleNamespace(**{**getattr(result, '__dict__', {}),
                    'final_response': json.dumps(value), 'events': events,
                    'finish_reason': getattr(result, 'finish_reason', 'completed')})
            return result
        monkeypatch.setattr(agent_recon, '_execute_harness', model)
        if cfg.get('resume_discovery'):
            checkpoint = cfg['resume_discovery']
            upgrade(checkpoint['plan'])
            for i, branch in enumerate(checkpoint['plan']['branches']):
                checkpoint['events'].extend(source_events(branch, cfg['target'], sid+'-resume-source-'+str(i)))
        try:
            return original_staged(sid, cfg, patches)
        finally:
            monkeypatch.setattr(agent_recon, '_execute_harness', current_model)
    def captured(html, url, branch, target, proof_data=None):
        upgrade(branch)
        for key, value in CLAIM.items():
            branch.setdefault(key, value)
        return SourceEvidence(url, branch['source_role'], 'verified', digest(html),
                              source_kind=branch.get('source_kind') or target.get('source_kind') or '')
    async def verify(page, branch, target, local_capture=None):
        upgrade(branch)
        html = await page.content()
        if local_capture is not None:
            local_capture['html'] = html
        return proof(branch, target)
    monkeypatch.setattr(agent_recon, 'validate_model_plan', validate)
    monkeypatch.setattr(browser_batch_tool, '_validate_plan', batch_validate)
    monkeypatch.setattr(agent_recon, 'selector_evidence_issues', selector)
    monkeypatch.setattr(agent_recon, '_run_staged_harness', staged)
    monkeypatch.setattr(recon_contract, 'validate_submission', submission)
    monkeypatch.setattr(HarvestState, '__init__', state_init)
    monkeypatch.setattr(harvest_workflow, 'captured_source_evidence', captured)
    monkeypatch.setattr(browser_batch_tool, 'captured_source_evidence', captured)
    monkeypatch.setattr(browser_batch_tool, 'verify_source', verify)
    # Modules that imported the callable directly still use the explicit test
    # boundary, without changing any production function or accepting skip flags.
    for module in __import__('sys').modules.copy().values():
        if getattr(module, '__file__', '') and '/tests/' in str(module.__file__).replace('\\', '/'):
            for name, original, replacement in [('_validate_plan', original_batch_validation, batch_validate),
                    ('validate_model_plan', original_validation, validate), ('selector_evidence_issues', original_selector, selector)]:
                if getattr(module, name, None) is original:
                    monkeypatch.setattr(module, name, replacement)
