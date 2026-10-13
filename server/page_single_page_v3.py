"""Single-page WebPlus capture with bounded failed-image and controller evidence."""
from __future__ import annotations

import base64
import hashlib
import re

from bs4 import BeautifulSoup
from soupsieve import SelectorSyntaxError

from server import page_single_page_v2 as v2
from server.failed_image_pager import _digest, install_stylesheet_capture, terminal_pagers

MECHANISM = 'scoped_webplus_single_page_failed_images'


async def capture(fallback, context, branch, *, source_target=None, on_page=None, resume_pages=None):
    materials = {}
    async def verify(page, selector):
        proofs, observed = await terminal_pagers(page, selector)
        materials.update(observed)
        return proofs
    pages, errors, evidence = await fallback(context, branch, source_target=source_target,
        on_page=on_page, resume_pages=[], page_setup=install_stylesheet_capture, terminal_verifier=verify)
    evidence.update(mechanism=MECHANISM, failed_image_materials=materials)
    if resume_pages:
        evidence['resume_rejected'] = 'stage_requires_current_widget_proof'
    if len(evidence.get('terminal_pagers', [])) != 1 or not evidence['terminal_pagers'][0].get('confirmed'):
        errors.append({'url': branch['url'], 'status': 'scoped_terminal_pager_unverified'})
    return pages, errors, evidence


def probe(context, result):
    return {**v2.probe(context, result), 'failed_image_materials_sha256': _digest(result[2]['failed_image_materials'])}


def _path(node):
    parts = []
    while node is not None and node.name != '[document]':
        children = node.parent.find_all(recursive=False) if node.parent else [node]
        parts.insert(0, f'{node.name}:nth-child({next(i for i, child in enumerate(children, 1) if child is node)})')
        node = node.parent
    return '>'.join(parts)


def _valid_sample(scope, soup, root, pager, materials, url):
    if (scope['confirmed'] is not True or type(scope['document_status']) is not int or scope['document_status'] != 200
            or type(scope['script_errors']) is not int or scope['script_errors'] != 0 or scope['document_url'] != url):
        return False
    failed = scope['failed_requests']
    if not isinstance(failed, list):
        return False
    if not failed:
        return scope['references'] == [] and scope['material_refs'] == []
    if any(len(row) != 3 or row[1] != 'image' or row[2] not in (404, 410) for row in failed):
        return False
    refs = scope['material_refs']
    if not refs or not isinstance(refs, list) or len(refs) > 33:
        return False
    cssom = materials[refs[0]]
    if cssom['kind'] != 'cssom' or _digest(cssom['value']) != refs[0]:
        return False
    originals = {}
    total = 0
    for key in refs[1:]:
        item = materials[key]
        raw = base64.b64decode(item['body_base64'], validate=True)
        total += len(raw)
        if item['kind'] != 'stylesheet' or _digest(item) != key or hashlib.sha256(raw).hexdigest() != item['sha256']:
            return False
        originals[item['url']] = item['sha256']
    if total > 256 * 1024 or any(sheet['href'] and sheet['href'] not in originals for sheet in cssom['value']['sheets']):
        return False
    if {row['url'] for row in scope['references']} != {row[0] for row in failed}:
        return False
    for row in scope['references']:
        if not row['items'] or not any(item['places'] for item in row['items']):
            return False
        for item in row['items']:
            if item['kind'] == 'css':
                if item['property'] != 'background-image':
                    return False
                selector = item['structural_selector']
                if selector is None:
                    nodes = soup.select(item['location']['inline_path'])
                else:
                    if not re.fullmatch(r'[A-Za-z0-9_#.*\s>,\-]+', selector):
                        return False
                    nodes = soup.select(selector)
                expected = [{'path': _path(node), 'within_roster': root is node or root in node.parents,
                             'within_pager': pager is node or pager in node.parents,
                             'contains_scope': node is root or node in root.parents or node is pager or node in pager.parents}
                            for node in nodes]
                if expected != item['places']:
                    return False
            elif item['kind'] != 'img':
                return False
            for place in item['places']:
                [node] = soup.select(place['path'])
                if (any(place[field] is not False for field in ('within_roster', 'within_pager', 'contains_scope'))
                        or root is node or pager is node or root in node.parents or pager in node.parents
                        or node in root.parents or node in pager.parents):
                    return False
    return True


def valid(context, result, payload):
    try:
        if payload['mechanism'] != MECHANISM or probe(context, result) != payload:
            return False
        evidence = result[2]
        materials = evidence['failed_image_materials']
        [terminal] = payload['terminal_pagers']
        [first, clicked, stable] = terminal['request_scope_evidence']['samples']
        scopes = [sample['failed_resource_scope'] for sample in (first, clicked, stable)]
        if scopes[0] != scopes[1] or scopes[1] != scopes[2]:
            return False
        soup = BeautifulSoup(context.source_html, 'html.parser')
        [root], [pager] = soup.select(first['source_widget_selector']), soup.select(terminal['pager_selector'])
        if not all(_valid_sample(scope, soup, root, pager, materials, context.final_url) for scope in scopes):
            return False
        if scopes[0]['failed_requests'] and terminal.get('controller_evidence', {}).get('probes') != 2:
            return False
        adapted = {**payload, 'mechanism': 'scoped_webplus_single_page'}
        adapted.pop('failed_image_materials_sha256')
        return v2.valid(context, (result[0], result[1], {**evidence, 'mechanism': adapted['mechanism']}), adapted)
    except (KeyError, TypeError, ValueError, AttributeError, StopIteration, SelectorSyntaxError):
        return False


def register():
    from server.page_types import PaginationParams, TypeDefinition, register_type
    register_type(TypeDefinition('pagination.single_page', 3, 'pagination', PaginationParams,
        lambda b: (b.get('kind') or 'roster') == 'roster' and b.get('single_page') is True
            and bool(b.get('card_selector')) and not any(b.get(key) for key in
                ('next_selector', 'initial_click_selector', 'empty_state_selector', 'roster_container_selector')),
        capture, 'current browser + ordinary roster + bound WebPlus widget',
        'one HTML page, current terminal proof and hashed CSS response/CSSOM originals',
        ('scoped_terminal_pager_unverified', 'single_page_pagination_unverified', 'source_role_evidence_unverified'),
        'Same widget/count/actual-click contract as @2. Permanent failed images may be scoped outside '
        'both widgets only with complete readable DOM/CSS references and responsive server-bound controller. '
        'Literal background-image URLs and simple tag/class/id selectors, child/descendant relations, '
        'hover/focus/active/link states and before/after pseudo-elements are supported; ambiguous references, '
        'inaccessible CSS, transient/data failures and script errors remain unverified.',
        probe, valid, 'three stable image-reference samples + CSS originals + responsive controller + @2 source/widget proof',
        source_dependencies=('server/page_single_page_v2.py', 'server/failed_image_pager.py',
                             'server/pager_evidence.py', 'server/browser_readiness.py',
                             'server/browser_batch_tool.py', 'server/agent_recon.py', 'server/source_qualification.py')))
