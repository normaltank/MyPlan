"""Reviewed DOM stage mechanisms. Results retain originals; probes expose structure only."""
from __future__ import annotations

import contextlib
import hashlib
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


def digest(html):
    return hashlib.sha256(html.encode('utf-8')).hexdigest()


def _same_site(address, root):
    parsed, origin = urlparse(address), urlparse(root)
    return (parsed.scheme in {'http', 'https'} and parsed.netloc == origin.netloc
            and bool(parsed.hostname) and not parsed.username and not parsed.password)


async def scoped_links(_fallback, context, branch):
    """A selected container owns all descendant anchors in the current rendered state."""
    from server.page_types import EntranceCapture
    from server.browser_batch_tool import single_page_evidence
    url, html, urls, errors = branch['url'], None, [], []
    evidence = {'url': url, 'kind': 'navigation', 'mechanism': 'descendant_anchor_sources',
                'discovery_scope': 'rendered_navigation_state', 'selected_containers': 0,
                'children': [], 'discovered_children': 0}
    page = await context.new_page()
    try:
        response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        if response is not None and response.status >= 400:
            raise RuntimeError('entrance HTTP failure')
        with contextlib.suppress(Exception):
            await page.wait_for_load_state('networkidle', timeout=3000)
        html = await page.content()
        evidence.update(final_url=page.url, source_html_sha256=digest(html))
        containers = BeautifulSoup(html, 'html.parser').select(branch['child_link_selector'])
        evidence['selected_containers'] = len(containers)
        if not containers:
            errors.append({'url': url, 'status': 'entrance_container_missing'})
        if await page.locator(branch['child_link_selector']).count() != len(containers):
            errors.append({'url': url, 'status': 'entrance_state_changed'})
        for index, container in enumerate(containers):
            if not await page.locator(branch['child_link_selector']).nth(index).is_visible():
                errors.append({'url': url, 'status': 'entrance_container_hidden'})
            anchors = container.select('a[href]')
            if not anchors:
                errors.append({'url': url, 'status': 'entrance_links_missing'})
            live_anchors = page.locator(branch['child_link_selector']).nth(index).locator('a[href]')
            if await live_anchors.count() != len(anchors) or any([
                    not await live_anchors.nth(position).is_visible() for position in range(len(anchors))]):
                errors.append({'url': url, 'status': 'entrance_link_visibility_unverified'})
            for anchor in anchors:
                address = urljoin(page.url, anchor['href'])
                if not _same_site(address, url):
                    errors.append({'url': address, 'status': 'entrance_source_outside_scope'})
                elif address not in urls:
                    urls.append(address)
        if branch.get('max_children') is not None and len(urls) > branch['max_children']:
            errors.append({'url': url, 'status': 'child_limit_reached'})
        termination = single_page_evidence(html, page.url, branch['child_link_selector'])
        evidence.update(children=urls, discovered_children=len(urls), pagination_termination=termination,
                        navigation_termination_verified=termination['confirmed'] and not branch.get('next_selector'))
        if not evidence['navigation_termination_verified']:
            errors.append({'url': url, 'status': 'single_page_pagination_unverified'})
        if await page.content() != html:
            errors.append({'url': url, 'status': 'entrance_state_changed'})
    except Exception as exc:
        errors.append({'url': url, 'status': 'entrance_navigation_failed', 'error_type': type(exc).__name__})
    finally:
        await page.close()
    return EntranceCapture(html, urls, errors, evidence)


async def verified_next(_fallback, context, branch, *, source_target=None, on_page=None, resume_pages=None):
    """Independently capture a bounded, unique next-click chain with source proof per page."""
    from server.browser_batch_tool import (_unit_selector, _wait_for_change, single_page_evidence,
                                           _frontier_signature)
    from server.browser_readiness import install_request_tracking
    from server.pagination_identity import next_identity, same_identity, displaced_controls
    from server.source_qualification import source_claim_valid, verify_source
    url, selector, unit = branch['url'], branch['next_selector'], _unit_selector(branch)
    pages, errors, seen = [], [], set()
    evidence = {'url': url, 'end': 'unresolved', 'visited_pages': 0, 'next_clicks': 0,
                'restored_pages': 0, 'mechanism': 'verified_next_click_chain', 'source_roles': {}}
    # A new invocation proves its chain again; saved pages remain audit originals.
    if resume_pages:
        evidence['resume_rejected'] = 'stage_requires_current_chain_proof'
    page = await context.new_page()
    install_request_tracking(page)
    identity = None
    try:
        response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        if response is not None and response.status >= 400:
            raise RuntimeError('pagination HTTP failure')
        while True:
            with contextlib.suppress(Exception):
                await page.wait_for_load_state('networkidle', timeout=3000)
            html = await page.content()
            if source_claim_valid(branch):
                local = {}
                try:
                    proof = await verify_source(page, branch, source_target or {}, local_capture=local)
                except Exception as exc:
                    proof = {'url': page.url, 'confirmed': False, 'reason': 'source_verification_failed',
                             'error_type': type(exc).__name__}
                html = local.get('html') or html
                evidence['source_roles'][page.url] = proof
                if not proof.get('confirmed'):
                    errors.append({'url': page.url, 'status': 'source_role_evidence_unverified'})
            frontier = (page.url, _frontier_signature(html, branch))
            if frontier in seen:
                errors.append({'url': page.url, 'status': 'pagination_cycle'})
                break
            seen.add(frontier)
            pages.append((page.url, html))
            if on_page:
                on_page(page.url, html, len(pages) - 1)
            evidence['visited_pages'] = len(pages)
            control = page.locator(selector)
            count = await control.count()
            if count == 0:
                terminal = single_page_evidence(html, page.url, unit)
                displaced = (displaced_controls(html, unit, identity, page.url,
                             visited_urls={item[0] for item in pages}) if identity is not None else [])
                if identity is None or not terminal['confirmed'] or displaced:
                    errors.append({'url': page.url, 'status': 'pagination_not_verified'})
                else:
                    evidence.update(end='next_control_absent', pagination_termination=terminal,
                                    forward_identity=identity)
                break
            if count != 1 or not await control.is_visible():
                errors.append({'url': page.url, 'status': 'next_control_not_unique'})
                break
            current = next_identity(html, unit, selector)
            if (current is None or current.get('binding_count') != 1
                    or (identity is not None and not same_identity(identity, current))):
                errors.append({'url': page.url, 'status': 'pagination_not_verified'})
                break
            identity = identity or current
            if not single_page_evidence(html, page.url, unit, [selector])['confirmed']:
                errors.append({'url': page.url, 'status': 'pagination_unknown_forward_control'})
                break
            if not await control.is_enabled():
                errors.append({'url': page.url, 'status': 'pagination_terminal_control_unverified'})
                break
            href = await control.get_attribute('href')
            if href and not href.startswith(('#', 'javascript:')) and not _same_site(urljoin(page.url, href), url):
                errors.append({'url': page.url, 'status': 'pagination_source_outside_scope'})
                break
            if len(pages) >= branch.get('max_pages', 20):
                errors.append({'url': page.url, 'status': 'page_limit_reached'})
                break
            before_url = page.url
            await control.click()
            evidence['next_clicks'] += 1
            if await _wait_for_change(page, html, before_url, unit_selector=unit) is None:
                errors.append({'url': page.url, 'status': 'pagination_unchanged'})
                break
            if not _same_site(page.url, url):
                errors.append({'url': page.url, 'status': 'pagination_source_outside_scope'})
                break
    except Exception as exc:
        errors.append({'url': page.url, 'status': 'pagination_navigation_failed', 'error_type': type(exc).__name__})
    finally:
        await page.close()
    return pages, errors, evidence


async def article_region(_fallback, context, url, branch, *, scope_pairs=None, page_ready_selector=None,
                         saved_record=None):
    """Capture one semantic article region, independently returning its page and proof."""
    from server.page_types import ProfileCapture
    from server.browser_readiness import install_request_tracking
    from server.profile_structure import resolve_equivalent_profile_selectors, verify_profile_container
    page, html, proof, errors = await context.new_page(), None, None, []
    install_request_tracking(page)
    try:
        response = await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        if response is not None and response.status >= 400:
            raise RuntimeError('detail HTTP failure')
        effective_ready = page_ready_selector or branch.get('profile_ready_selector')
        if effective_ready:
            await page.locator(effective_ready).wait_for(state='visible', timeout=10000)
        with contextlib.suppress(Exception):
            await page.wait_for_load_state('networkidle', timeout=4000)
        html = await page.content()
        pairs = scope_pairs or {(branch['profile_content_selector'], branch.get('profile_content_start_selector'))}
        pair = next(iter(pairs)) if len(pairs) == 1 else await resolve_equivalent_profile_selectors(page, pairs)
        if pair is None:
            errors.append({'url': url, 'status': 'profile_content_selector_conflict'})
        else:
            selector, start = pair
            nodes = BeautifulSoup(html, 'html.parser').select(selector)
            if len(nodes) != 1 or nodes[0].name != 'article' or nodes[0].select('article'):
                errors.append({'url': url, 'status': 'profile_article_region_unverified'})
            else:
                local = {}
                proof = await verify_profile_container(page, selector, start, local_capture=local, allow_empty=True)
                html = local.get('html') or html
                if not proof.get('confirmed'):
                    errors.append({'url': url, 'status': 'profile_content_browser_unverified'})
    except Exception as exc:
        errors.append({'url': url, 'status': 'profile_navigation_failed', 'error_type': type(exc).__name__})
    finally:
        final_url = page.url
        await page.close()
    return ProfileCapture(html, proof, errors, {'final_url': final_url, 'mechanism': 'semantic_article_region'})


def entrance_probe(context, result):
    return {'mechanism': 'descendant_anchor_sources', 'source_sha256': digest(result.html or ''),
        'source_urls': result.source_urls, 'selected_containers': result.evidence.get('selected_containers', 0),
        'error_count': len(result.errors), 'discovery_scope': result.evidence.get('discovery_scope'),
        'pagination_termination': result.evidence.get('pagination_termination', {})}


def entrance_valid(context, result, payload):
    try:
        urls = payload['source_urls']
        proof = payload['pagination_termination']
        valid = (payload['mechanism'] == 'descendant_anchor_sources'
            and payload['source_sha256'] == context.source_html_sha256
            and type(payload['error_count']) is int and payload['error_count'] == 0 and type(payload['selected_containers']) is int
            and payload['selected_containers'] > 0 and payload['discovery_scope'] == 'rendered_navigation_state'
            and isinstance(urls, list) and bool(urls) and len(urls) == len(set(urls))
            and all(_same_site(url, context.requested_url) for url in urls)
            and proof.get('confirmed') is True and proof.get('unresolved_forward_controls') == 0
            and proof.get('source_sha256') == context.source_html_sha256
            and proof.get('unit_selector') == context.branch['child_link_selector'])
        if result is not None:
            valid = valid and entrance_probe(context, result) == payload
        if context.source_html is not None:
            from server.browser_batch_tool import single_page_evidence
            nodes = BeautifulSoup(context.source_html, 'html.parser').select(context.branch['child_link_selector'])
            actual = list(dict.fromkeys(urljoin(context.final_url, anchor['href']) for node in nodes
                                       for anchor in node.select('a[href]')))
            valid = (valid and actual == urls and len(nodes) == payload['selected_containers']
                     and single_page_evidence(context.source_html, context.final_url,
                                              context.branch['child_link_selector']) == proof)
        return bool(valid)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def pagination_probe(context, result):
    pages, errors, evidence = result
    unit, selector = context.branch['card_selector'], context.branch['next_selector']
    return {'mechanism': 'verified_next_click_chain', 'error_count': len(errors),
        'pages': [{'url': url, 'sha256': digest(html), 'unit_count': len(BeautifulSoup(html, 'html.parser').select(unit)),
                   'forward_control_count': len(BeautifulSoup(html, 'html.parser').select(selector))}
                  for url, html in pages],
        'end': evidence.get('end'), 'next_clicks': evidence.get('next_clicks'),
        'pagination_termination': evidence.get('pagination_termination', {})}


def pagination_valid(context, result, payload):
    try:
        pages, terminal = payload['pages'], payload['pagination_termination']
        valid = (payload['mechanism'] == 'verified_next_click_chain'
            and type(payload['error_count']) is int and payload['error_count'] == 0
            and isinstance(pages, list) and 2 <= len(pages) <= context.branch.get('max_pages', 20)
            and pages[0]['sha256'] == context.source_html_sha256 and pages[0]['url'] == context.final_url
            and payload['end'] == 'next_control_absent' and type(payload['next_clicks']) is int
            and payload['next_clicks'] == len(pages) - 1
            and all(_same_site(page['url'], context.requested_url) and type(page['unit_count']) is int
                    and page['unit_count'] > 0 and type(page['forward_control_count']) is int
                    and re.fullmatch('[0-9a-f]{64}', page['sha256']) for page in pages)
            and all(page['forward_control_count'] == 1 for page in pages[:-1])
            and pages[-1]['forward_control_count'] == 0
            and terminal.get('confirmed') is True and terminal.get('unresolved_forward_controls') == 0
            and terminal.get('source_sha256') == pages[-1]['sha256']
            and terminal.get('url') == pages[-1]['url']
            and terminal.get('unit_selector') == context.branch['card_selector'])
        if result is not None:
            valid = valid and pagination_probe(context, result) == payload
            from server.browser_batch_tool import single_page_evidence
            originals = result[0]
            valid = valid and all(single_page_evidence(html, url, context.branch['card_selector'],
                [context.branch['next_selector']] if index < len(originals) - 1 else [])['confirmed']
                for index, (url, html) in enumerate(originals))
            if originals:
                final_url, final_html = originals[-1]
                valid = valid and single_page_evidence(final_html, final_url,
                    context.branch['card_selector']) == terminal
        return bool(valid)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def detail_probe(context, result):
    proof = result.proof or {}
    # No profile text, names, image labels or URL-derived person fields leave this probe.
    return {'mechanism': 'semantic_article_region', 'source_sha256': digest(result.html or ''),
        'error_count': len(result.errors), 'profile_content_selector': proof.get('profile_content_selector'),
        'profile_content_start_selector': proof.get('profile_content_start_selector'),
        'container_count': proof.get('container_count'), 'container_visible': proof.get('container_visible'),
        'dom_stable': proof.get('dom_stable'), 'network_idle': proof.get('network_idle'),
        'confirmed': proof.get('confirmed'), 'html_sha256': proof.get('html_sha256')}


def detail_valid(context, result, payload):
    try:
        valid = (payload['mechanism'] == 'semantic_article_region'
            and type(payload['error_count']) is int and payload['error_count'] == 0
            and payload['confirmed'] is True and type(payload['container_count']) is int and payload['container_count'] == 1
            and payload['container_visible'] is True and payload['dom_stable'] is True
            and payload['network_idle'] is True
            and payload['source_sha256'] == payload['html_sha256'] == context.source_html_sha256
            and payload['profile_content_selector'] == context.branch['profile_content_selector']
            and payload['profile_content_start_selector'] == context.branch.get('profile_content_start_selector'))
        if result is not None:
            valid = valid and detail_probe(context, result) == payload
        if context.source_html is not None:
            nodes = BeautifulSoup(context.source_html, 'html.parser').select(context.branch['profile_content_selector'])
            valid = valid and len(nodes) == 1 and nodes[0].name == 'article' and not nodes[0].select('article')
        return bool(valid)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def register():
    from server.page_types import (TypeDefinition, EntranceParams, PaginationParams, DetailParams, register_type)
    definitions = (
        ('entrance.scoped_links', 'entrance', EntranceParams, scoped_links, entrance_probe, entrance_valid,
         lambda branch: branch.get('kind') == 'navigation' and bool(branch.get('child_link_selector'))),
        ('pagination.verified_next', 'pagination', PaginationParams, verified_next, pagination_probe, pagination_valid,
         lambda branch: (branch.get('kind') or 'roster') == 'roster' and bool(branch.get('next_selector'))
                        and bool(branch.get('card_selector')) and not branch.get('initial_click_selector')
                        and not branch.get('single_page')),
        ('detail.article_region', 'detail', DetailParams, article_region, detail_probe, detail_valid,
         lambda branch: bool(branch.get('profile_content_selector'))),
    )
    for type_id, role, params, implementation, probe, validator, applicability in definitions:
        register_type(TypeDefinition(type_id, 1, role, params, applicability, implementation,
            'bounded browser context + canonical structural branch; originals remain local',
            'explicit stage capture containing originals, errors and structure proof',
            {'entrance': ('entrance_container_missing', 'entrance_state_changed', 'entrance_container_hidden',
                'entrance_links_missing', 'entrance_link_visibility_unverified', 'entrance_source_outside_scope',
                'child_limit_reached', 'single_page_pagination_unverified', 'entrance_navigation_failed'),
             'pagination': ('source_role_evidence_unverified', 'pagination_cycle', 'pagination_not_verified',
                'next_control_not_unique', 'pagination_unknown_forward_control', 'pagination_terminal_control_unverified',
                'pagination_source_outside_scope', 'page_limit_reached', 'pagination_unchanged', 'pagination_navigation_failed'),
             'detail': ('profile_content_selector_conflict', 'profile_article_region_unverified',
                'profile_content_browser_unverified', 'profile_navigation_failed')}[role],
            applicability_contract={
                'entrance': 'Navigation containers with visible descendant anchors on the same site; current DOM only, no parent paging.',
                'pagination': 'Ordinary roster with unique next control and card selector, no initial click or single-page claim; absent terminal control, at most max_pages (default 20).',
                'detail': 'Exactly one non-nested semantic article selected by profile_content_selector; selector conflicts remain unresolved.',
            }[role],
            stage_probe=probe, evidence_validator=validator, evidence_contract='current-dom-stage-v1'))
