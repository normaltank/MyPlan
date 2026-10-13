"""Bound a faculty-group continuation using one native root and reciprocal pagers.

No plan field can assert this relationship. Browser tools and capture consumers
share the receipt; an unrelated faculty menu or a URL prefix cannot supply it.
"""
from __future__ import annotations

import re
from bs4 import BeautifulSoup, Tag
from urllib.parse import urljoin

from server.pagination_identity import pager_ancestor
from server.profile_content import scope_profile_html


def _path(node: Tag) -> str:
    parts = []
    for item in [node, *node.parents]:
        if not isinstance(item, Tag) or item.name == '[document]':
            break
        peers = item.parent.find_all(item.name, recursive=False) if item.parent else [item]
        position = next(index for index, peer in enumerate(peers, 1) if peer is item)
        parts.append(f'{item.name}:nth-of-type({position})')
    return ' > '.join(reversed(parts))


def _pager(html: str, url: str, branch: dict, destination: str) -> str | None:
    """A link in one pager within the source or its immediate following component."""
    soup = BeautifulSoup(html, 'html.parser')
    scopes = soup.select(branch['source_container_selector'])
    if len(scopes) != 1:
        return None
    scope = scopes[0]
    neighbor = next((node for node in scope.next_siblings if isinstance(node, Tag)
                     and node.name not in {'script', 'style'}), None)
    pagers = {}
    for link in soup.select('a[href]'):
        if urljoin(url, link['href']) != destination:
            continue
        pager = pager_ancestor(link)
        if pager is None:
            continue
        chrome = False
        for wrapper in [link, *link.parents]:
            if wrapper is scope.parent:
                break
            tokens = ' '.join([str(wrapper.get('id') or ''), *wrapper.get('class', [])])
            chrome = chrome or wrapper.name in {'aside', 'header', 'footer', 'menu'}
            chrome = chrome or wrapper.get('role') in {'menu', 'menubar'}
            chrome = chrome or bool(re.search(
                r'(?:^|[-_\s])(?:sidebar|menu)(?:$|[-_\s])', tokens, re.I))
            if wrapper is not pager:
                chrome = chrome or wrapper.name == 'nav' or bool(re.search(
                    r'(?:^|[-_\s])(?:sidebar|menu|navigation|navbar|nav)(?:$|[-_\s])', tokens, re.I))
        if chrome:
            continue
        allowed = (pager is scope or scope in pager.parents or pager is neighbor
                   or (neighbor is not None and neighbor in pager.parents))
        if allowed:
            # Prove the actual link, including its computed ancestor visibility.
            # A nav container is never relaxed into a valid profile boundary.
            pagers.setdefault(_path(pager), _path(link))
    return next(iter(pagers.values())) if len(pagers) == 1 else None


def _source_ready(proof: dict, branch: dict, *, faculty: bool) -> bool:
    from server.source_qualification import SOURCE_EVIDENCE_VERSION, SOURCE_FIELDS, source_unit_selector
    unit = source_unit_selector(branch)
    return (proof.get('source_evidence_version') == SOURCE_EVIDENCE_VERSION
        and all(proof.get(key) is True for key in (
        'college_bound', 'units_within_scope', 'witness_visible', 'dom_stable', 'network_idle'))
        and all(proof.get(key) == branch.get(key) for key in SOURCE_FIELDS)
        and (not unit or proof.get('unit_selector') == unit)
        and (not faculty or (proof.get('confirmed') is True and proof.get('role_supported') is True)))


def relationship_matches(html: str, url: str, branch: dict, path: dict, relation: dict) -> bool:
    """Rebind the current original/configuration to the browser-owned receipt."""
    from server.source_qualification import digest
    if not isinstance(relation, dict) or relation.get('version') != 1:
        return False
    root_url = path['root_url']
    root = relation.get('root_source_proof') or {}
    current_pager = relation.get('current_pager_proof') or {}
    root_pager = relation.get('root_pager_proof') or {}
    if not all(isinstance(item, dict) for item in (root, current_pager, root_pager)):
        return False
    requests = root.get('source_request_evidence') or {}
    if not isinstance(requests, dict):
        return False
    selector = _pager(html, url, branch, root_url)
    if not selector:
        return False
    _, scope, issue = scope_profile_html(html, selector, url)
    return bool(not issue and scope and relation.get('source_url') == url
        and relation.get('root_url') == root_url and root_url != url
        and relation.get('faculty_group_path') == path == root.get('faculty_group_path')
        and root.get('url') == root_url and _source_ready(root, branch, faculty=True)
        and requests.get('main_document_url') == root_url
        and requests.get('main_document_status') == 200
        and relation.get('source_html_sha256') == digest(html)
        and current_pager.get('profile_content_selector') == selector
        and current_pager.get('html_sha256') == digest(html)
        and current_pager.get('selected_html_sha256') == scope['selected_html_sha256']
        and root.get('extra_witness_selectors') == [root_pager.get('profile_content_selector')]
        and root_pager.get('html_sha256') == root.get('source_html_sha256')
        and relation.get('root_forward_url') == url and relation.get('current_back_url') == root_url
        and all(item.get('confirmed') is True for item in (current_pager, root_pager)))


def _final_link_proof(proof: dict, html: str, selector: str, source: dict) -> dict | None:
    """Bind a stable link to the source's atomic final HTML/visibility snapshot."""
    from server.source_qualification import digest
    _, selected, issue = scope_profile_html(html, selector, source['url'])
    if (issue or not selected or proof.get('confirmed') is not True
            or proof.get('selected_html_sha256') != selected['selected_html_sha256']
            or source.get('extra_witness_selectors') != [selector]
            or source.get('witness_visible') is not True):
        return None
    return {**proof, 'html_sha256': digest(html),
            'final_source_facts_sha256': source['final_witness_facts_sha256']}


async def read_pagination_parent(page, branch: dict, target: dict, path: dict, *, local_capture=None) -> dict:
    from server.browser_readiness import install_request_tracking
    from server.profile_structure import verify_profile_container
    from server.source_qualification import _verify_source_here, digest
    root_url, current_url = path['root_url'], page.url
    current_html = await page.content()
    current_selector = _pager(current_html, current_url, branch, root_url)
    if not current_selector:
        return {'related_sources': [], 'reason': 'current_roster_pager_link_missing',
                'root_observation': {'url': root_url, 'navigation_attempted': False}}
    context = page.context
    if hasattr(page, 'raw_page'):
        from server.browser_context import IsolatedBrowserContext
        context = IsolatedBrowserContext(page._context, polite=page.polite)
    root_page = await context.new_page()
    originals = []
    result = {'related_sources': originals, 'root_observation': {'url': root_url}}
    if local_capture is not None:
        local_capture.update(result)
    try:
        install_request_tracking(root_page)
        response = await root_page.goto(root_url, wait_until='domcontentloaded', timeout=12000)
        raw = await root_page.content()
        originals.append({'url': root_page.url, 'html': raw})
        result['root_observation'].update(status=getattr(response, 'status', None), final_url=root_page.url)
        if response is None or response.status != 200 or root_page.url != root_url:
            return {**result, 'reason': 'pagination_parent_unreadable_or_redirected'}
        root_capture = {}
        root_proof = await _verify_source_here(root_page, branch, target, local_capture=root_capture)
        root_html = root_capture['html']
        originals[0]['html'] = root_html
        result['root_observation']['proof'] = root_proof
        if not _source_ready(root_proof, branch, faculty=True) or root_proof.get('faculty_group_path') != path:
            return {**result, 'reason': 'pagination_parent_relationship_unverified'}
        root_selector = _pager(root_html, root_url, branch, current_url)
        if not root_selector:
            return {**result, 'reason': 'reciprocal_roster_pager_links_missing'}
        root_pager_capture, current_pager_capture = {}, {}
        root_pager = await verify_profile_container(root_page, root_selector, local_capture=root_pager_capture)
        current_pager = await verify_profile_container(page, current_selector, local_capture=current_pager_capture)
        if root_pager.get('confirmed') is not True or current_pager.get('confirmed') is not True:
            return {**result, 'reason': 'pagination_link_scope_unverified'}
        # Recheck both source scopes after reading the parent/pager. The same
        # complete original must bind each source and its actual pager proof.
        root_proof = await _verify_source_here(root_page, branch, target, local_capture=root_capture,
                                              extra_witness_selectors=(root_selector,))
        current_capture = {}
        current_proof = await _verify_source_here(page, branch, target, local_capture=current_capture,
                                                 extra_witness_selectors=(current_selector,))
        root_html, current_html = root_capture['html'], current_capture['html']
        originals[0]['html'] = root_html
        result['root_observation']['proof'] = root_proof
        if (not _source_ready(root_proof, branch, faculty=True)
                or not _source_ready(current_proof, branch, faculty=False)
                or page.url != current_url or root_page.url != root_url
                or current_proof.get('faculty_group_path') != path
                or current_proof.get('extra_witness_selectors') != [current_selector]
                or _pager(root_html, root_url, branch, current_url) != root_selector):
            return {**result, 'reason': 'pagination_relationship_changed'}
        root_pager = _final_link_proof(root_pager, root_html, root_selector, root_proof)
        current_pager = _final_link_proof(current_pager, current_html, current_selector, current_proof)
        if root_pager is None or current_pager is None:
            return {**result, 'reason': 'pagination_link_changed'}
        relation = {'version': 1, 'source_url': current_url, 'source_html_sha256': digest(current_html),
            'root_url': root_url, 'faculty_group_path': path, 'root_source_proof': root_proof,
            'root_pager_proof': root_pager, 'current_pager_proof': current_pager,
            'root_forward_url': current_url, 'current_back_url': root_url}
        if not relationship_matches(current_html, current_url, branch, path, relation):
            return {**result, 'reason': 'pagination_original_binding_changed'}
        return {**result, 'relationship': relation, 'current_proof': current_proof, 'html': current_html}
    except Exception as exc:
        return {**result, 'reason': 'pagination_parent_read_failed',
                'root_observation': {**result['root_observation'], 'error_kind': type(exc).__name__}}
    finally:
        await root_page.close()
