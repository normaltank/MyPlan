"""Single rendered WebPlus roster with scoped, current-browser terminal proof."""
from __future__ import annotations

from bs4 import BeautifulSoup

from server.pager_evidence import proof_valid, scope_digest, scoped_terminal_pagers


async def capture(fallback, context, branch, *, source_target=None, on_page=None, resume_pages=None):
    # New invocations prove the actual widgets; saved originals never replace a click.
    pages, errors, evidence = await fallback(context, branch, source_target=source_target,
        on_page=on_page, resume_pages=[], terminal_verifier=scoped_terminal_pagers)
    evidence['mechanism'] = 'scoped_webplus_single_page'
    if resume_pages:
        evidence['resume_rejected'] = 'stage_requires_current_widget_proof'
    if len(evidence.get('terminal_pagers', [])) != 1 or not evidence['terminal_pagers'][0].get('confirmed'):
        errors.append({'url': branch['url'], 'status': 'scoped_terminal_pager_unverified'})
    return pages, errors, evidence


def probe(context, result):
    pages, errors, evidence = result
    return {'mechanism': evidence.get('mechanism'), 'error_count': len(errors),
            'page_count': len(pages), 'end': evidence.get('end'),
            'pagination_termination': evidence.get('pagination_termination'),
            'terminal_pagers': evidence.get('terminal_pagers')}


def valid(context, result, payload):
    from server.browser_batch_tool import single_page_evidence
    try:
        [terminal] = payload['terminal_pagers']
        samples = terminal['request_scope_evidence']['samples']
        [first, clicked, stable] = samples
        html, selector = context.source_html, context.branch['card_selector']
        widget = first['source_widget_selector']
        soup = BeautifulSoup(html, 'html.parser')
        roots, cards = soup.select(widget), soup.select(selector)
        pagers = soup.select(terminal['pager_selector'])
        host = pagers[0].find_parent(id=lambda value: value and value.startswith('wp_paging_')) if len(pagers) == 1 else None
        accepted = (payload['mechanism'] == 'scoped_webplus_single_page'
            and type(payload['error_count']) is int and payload['error_count'] == 0
            and type(payload['page_count']) is int and payload['page_count'] == 1
            and payload['end'] == 'single_page_no_pagination_control_seen'
            and terminal['url'] == context.final_url
            and proof_valid(terminal, html, selector)
            and len(roots) == 1 and roots[0].get('id', '').startswith('wp_news_') and bool(cards)
            and host is not None and roots[0]['id'].removeprefix('wp_news_') == host['id'].removeprefix('wp_paging_')
            and all(roots[0] in card.parents for card in cards)
            and terminal['source_widget_sha256'] == scope_digest(html, widget)
            and all(sample['source_widget_selector'] == widget and sample['roster_selector'] == selector
                and sample['pager_selector'] == terminal['pager_selector']
                and type(sample['blocking_pending_request_count']) is int
                and sample['blocking_pending_request_count'] == 0
                and isinstance(sample['ignored_pending_images'], list)
                and all(isinstance(url, str) and url for url in sample['ignored_pending_images'])
                for sample in (first, clicked, stable))
            and payload['pagination_termination'] == single_page_evidence(html, context.final_url,
                selector, [terminal['control_selector']]))
        if result is not None:
            accepted = accepted and probe(context, result) == payload
        return bool(accepted)
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def register():
    from server.page_types import PaginationParams, TypeDefinition, register_type
    register_type(TypeDefinition('pagination.single_page', 2, 'pagination', PaginationParams,
        lambda b: (b.get('kind') or 'roster') == 'roster' and b.get('single_page') is True
            and bool(b.get('card_selector')) and not any(b.get(key) for key in
                ('next_selector', 'initial_click_selector', 'empty_state_selector', 'roster_container_selector')),
        capture, 'current browser + ordinary roster branch + card selector',
        'one captured HTML page, errors and bound WebPlus terminal proof',
        ('scoped_terminal_pager_unverified', 'single_page_pagination_unverified', 'source_role_evidence_unverified'),
        'Visible WebPlus roster and native pager share a widget ID; counts prove one page. '
        'Actual forward click, stable widgets and request evidence are required. Only pending img requests '
        'whose every DOM occurrence lies outside both widgets can be ignored. Other structures use @1.',
        probe, valid, 'actual terminal click + source/pager/widget hashes + three request-scope samples',
        source_dependencies=('server/pager_evidence.py', 'server/browser_readiness.py',
                             'server/browser_batch_tool.py')))
