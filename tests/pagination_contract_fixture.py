"""Explicit complete-page evidence for synthetic recon component observations.

These builders stipulate a rendered single page to exercise stage/profile/card
contracts. Actual controls and false absence claims stay in Chromium tests.
This does not patch or bypass the production gate.
"""
import hashlib


def termination(url, selector):
    return {'url': url, 'unit_selector': selector, 'confirmed': True,
            'source_sha256': hashlib.sha256(b'<main>complete component roster</main>').hexdigest(),
            'unresolved_forward_controls': 0, 'basis': 'rendered_forward_controls'}


def completed_result(value, args):
    if isinstance(value, dict) and (value.get('pagination') or {}).get('total') == 0:
        selector = args.get('card_selector') or args.get('container_selector')
        if selector and value.get('url') and ('cardCount' in value or 'segment_count' in value):
            return {**value, 'pagination_termination': termination(value['url'], selector)}
    return value
