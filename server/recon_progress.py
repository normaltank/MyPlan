"""Compare reconnaissance observations, independent of tool event/copy counts."""
from __future__ import annotations

import hashlib
import json


_COPY_FIELDS = frozenset({
    'original_path', 'evidence_path', 'navigation_evidence_path', 'source_original_path', 'root_original_path',
    'elapsed_seconds', 'duration_ms', 'timestamp', 'repeated_lines_omitted', 'seen_omitted',
    'proof_sha256',
})


def _facts(value):
    if isinstance(value, list):
        return [_facts(item) for item in value]
    if not isinstance(value, dict):
        return value
    # A page-wide clock change is not a changed selected source/body. The gate
    # still validates the original full proof on every submission before this
    # fingerprint is used; this normalization never authorizes a source.
    pager = value.get('current_pager_proof')
    scoped = any(value.get(key) for key in (
        'selected_scope_sha256', 'selected_html_sha256', 'role_scope_sha256')) or bool(
            isinstance(pager, dict) and pager.get('selected_html_sha256'))
    return {key: _facts([] if key == 'previous_navigation_failures' and item is None else
                       min(item, 1) if key == 'proofs_on_url' and type(item) is int and item >= 0 else item)
            for key, item in value.items()
            if key not in _COPY_FIELDS
            and not (scoped and key in {'source_html_sha256', 'html_sha256'})}


def observation_fingerprint(events: list[dict], issues: list[dict]) -> str:
    """Keep new ranges, parameters and facts; repeated identical receipts add none.

    Failed conditions are included after actual validation, so a newer failed
    or pending proof that retires an old success changes the state even if all
    its facts were seen earlier. Accepted plans never consult this function.
    """
    from server.source_qualification import read_source_packet

    calls, observations = {}, set()
    for event in events:
        data = event.get('data') or {}
        if event.get('type') == 'tool/call':
            if data.get('name') in {'mcp__playwright__browser_evaluate',
                                    'mcp__playwright__page_type_catalog',
                                    'mcp__playwright__browser_screenshot'}:
                # These can help choose the next probe, but their arbitrary JS,
                # catalog page or image receipt does not itself establish a new
                # source, range or proof. A changed plan or subsequent verified
                # observation still permits another submission.
                continue
            try:
                args = json.loads(data.get('arguments') or '{}')
            except (TypeError, ValueError):
                continue
            calls[data.get('callId')] = {
                'name': data.get('name'), 'arguments': args, 'source_url': data.get('source_url')}
        elif event.get('type') == 'tool/result':
            for block in (data.get('message') or {}).get('content', []):
                call = calls.get(block.get('toolCallId'))
                if not call:
                    continue
                for content in block.get('content', []):
                    if content.get('type') != 'text':
                        continue
                    try:
                        result = json.loads(content.get('text', ''))
                    except (TypeError, ValueError):
                        result = {'status': 'invalid_tool_json'}
                    if isinstance(result, dict) and 'full_source_result' in result:
                        result = read_source_packet(result) or {'status': 'source_packet_invalid'}
                    if isinstance(result, dict) and 'full_stage_result' in result:
                        from server.agent_recon import _read_stage_packet
                        result = _read_stage_packet(result, result.get('role')) or {'status': 'stage_packet_invalid'}
                    if call['name'] == 'mcp__playwright__browser_verify_stage' and isinstance(result, dict):
                        result = _detail_stage_facts(result, call['arguments'])
                    # Navigation receipts bind readable originals but their
                    # whole-document hash alone does not show a changed range.
                    if call['name'] in {'mcp__playwright__browser_navigate', 'mcp__playwright__browser_survey'}:
                        result = _navigation_facts(result)
                    observations.add(json.dumps(_facts({**call, 'isError': block.get('isError', False),
                        'result': result}), sort_keys=True, ensure_ascii=False, separators=(',', ':')))
    failures = {json.dumps(_facts(issue), sort_keys=True, ensure_ascii=False,
                           separators=(',', ':')) for issue in issues}
    state = {'observations': sorted(observations), 'issues': sorted(failures)}
    return hashlib.sha256(json.dumps(state, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf8')).hexdigest()


def _navigation_facts(value):
    if isinstance(value, list):
        return [_navigation_facts(item) for item in value]
    if isinstance(value, dict):
        return {key: _navigation_facts(item) for key, item in value.items()
                if key != 'source_html_sha256'}
    return value


def _detail_stage_facts(packet: dict, arguments: dict) -> dict:
    """A verified article compares its selected original, not its transport hashes."""
    if packet.get('role') != 'detail' or not isinstance(packet.get('results'), list):
        return packet
    from server.agent_recon import _read_stage_capture, _read_stage_originals
    from server.page_types import validate_stage_evidence
    from server.profile_content import scope_profile_html

    branch, results = arguments.get('branch') or {}, []
    for item in packet['results']:
        if not isinstance(item, dict):
            return packet
        documents = _read_stage_originals(item)
        capture = _read_stage_capture('detail', item, documents) if documents else None
        if capture is None or len(documents) != 1:
            results.append({'requested_url': item.get('requested_url'), 'status': 'detail_capture_invalid'})
            continue
        url, html = documents[0]
        proof = item.get('stage_evidence')
        valid = validate_stage_evidence('detail', branch, proof,
            requested_url=item.get('requested_url'), final_url=url,
            source_html_sha256=hashlib.sha256(html.encode('utf8')).hexdigest(), source_html=html, result=capture)
        if not valid:
            results.append({'requested_url': item.get('requested_url'), 'status': 'detail_stage_invalid',
                            'errors': capture.errors, 'proof': _facts(capture.proof)})
            continue
        # The effective profile selectors are also present in normalized stage
        # params; obtain them from the actual, validated capture proof.
        profile = capture.proof or {}
        _, selected, issue = scope_profile_html(html, profile.get('profile_content_selector'), url,
            start_selector=profile.get('profile_content_start_selector'))
        if issue or not selected or selected['selected_html_sha256'] != profile.get('selected_html_sha256'):
            results.append({'requested_url': item.get('requested_url'), 'status': 'detail_scope_invalid'})
            continue
        semantic_proof = {key: value for key, value in proof.items() if key != 'source_html_sha256'}
        semantic_proof['payload'] = {key: value for key, value in proof['payload'].items()
                                     if key not in {'source_sha256', 'html_sha256'}}
        results.append({'requested_url': item.get('requested_url'), 'final_url': url,
            'selected_scope_sha256': selected['selected_html_sha256'], 'stage_evidence': semantic_proof,
            'profile_proof': _facts(profile), 'errors': capture.errors, 'meta': capture.meta})
    return {'role': packet['role'], 'results': results}
