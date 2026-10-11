"""Current, roster-bound evidence for controls that do not advance a roster."""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString

from server.pagination_identity import pager_ancestor

_FORWARD = re.compile(r'(?:^|[-_\s])(?:next|nextpage|pagination|pager|pages)(?:$|[-_\s])', re.I)
_FORWARD_LABEL = re.compile(r'下一页|下页|后一页|更多(?:人员|教师|师资)|加载更多(?:人员|教师|师资)?|'
                            r'next(?:\s+page)?|(?:load|show)\s+more(?:\s+(?:people|faculty|teachers))?', re.I)
_NAVIGATION = re.compile(r'(?:^|[-_\s])(?:nav|navigation|menu|breadcrumb|breadcrumbs)(?:$|[-_\s])', re.I)
_PUNCTUATION = re.compile(r'[\s>|/›»→·:：-]*')


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _words(node) -> str:
    text = ' '.join([str(node.get('id', '')), *node.get('class', [])])
    text = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', text)
    return re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', text)


def _action_attributes(node) -> bool:
    return any(str(key).lower().startswith(('on', 'data-')) for key in node.attrs)


def _touches_roster(node, cards) -> bool:
    return any(node is card or node in card.parents or card in node.parents for card in cards)


def _outside_link_text(frame, *, headings=False) -> str:
    return ''.join(str(text) for text in frame.descendants
        if isinstance(text, NavigableString) and not isinstance(text, Comment)
        and not any(parent.name == 'a' or (headings and parent.name == 'dt')
                    for parent in text.parents if parent is not frame))


def _breadcrumb_marker(frame, links) -> bool:
    images = frame.find_all('img')
    if not images:
        return True
    if len(images) != 1 or images[0].parent is not frame or links[0].parent is not frame:
        return False
    marker = images[0]
    return (not marker.get('alt') and not marker.get('title') and not marker.has_attr('srcset')
            and not _action_attributes(marker)
            and list(frame.children).index(marker) < list(frame.children).index(links[0]))


def _navigation_frame(control, cards, url):
    """Independent site navigation, active section menu, or link-only breadcrumb."""
    host = urlsplit(url).netloc
    for frame in control.parents:
        if frame.name in {'body', 'html', 'main', 'article', '[document]'}:
            break
        if _touches_roster(frame, cards) or pager_ancestor(frame) is not None:
            continue
        if _action_attributes(frame) or _FORWARD.search(_words(frame)):
            return None
        if frame.name == 'nav' or frame.get('role') == 'navigation' or _NAVIGATION.search(_words(frame)):
            return frame, 'site_navigation'
        links = frame.select('a[href]')
        if len(links) < 2 or frame.find(['picture', 'iframe', 'object', 'button', 'input', 'form']):
            continue
        if any(urlsplit(urljoin(url, link['href'])).netloc != host for link in links):
            continue
        active = [link for link in links if link.get('aria-current') == 'page'
                  or 'cur' in link.get('class', [])]
        if (frame.name in {'dl', 'ul', 'ol'} and not frame.find('img') and len(active) == 1
                and urljoin(url, active[0]['href']) == url
                and _PUNCTUATION.fullmatch(_outside_link_text(frame, headings=True))):
            return frame, 'active_section_menu'
        if (links[0].get_text(' ', strip=True).lower() in {'首页', 'home'}
                and _PUNCTUATION.fullmatch(_outside_link_text(frame)) and _breadcrumb_marker(frame, links)):
            return frame, 'link_only_breadcrumb'
    return None


def _current_page_navigation(control, cards, url):
    if (control.name != 'a' or control.get('role') == 'button'
            or any(parent.name in {'article', 'main'} for parent in control.parents)
            or _touches_roster(control, cards) or pager_ancestor(control) is not None
            or _action_attributes(control)):
        return None
    href = str(control.get('href', '')).strip()
    if (not href or href.startswith(('#', 'javascript:')) or urljoin(url, href) != url
            or 'next' in control.get('rel', [])):
        return None
    label = control.get_text(' ', strip=True) or control.get('aria-label', '') or control.get('title', '')
    if _FORWARD.search(_words(control)) or _FORWARD_LABEL.fullmatch(label):
        return None
    return _navigation_frame(control, cards, url)


def nonpagination_evidence(html: str, url: str, card_selector: str, selector: str) -> dict | None:
    """Every selector match needs affirmative current non-pagination evidence."""
    from server.browser_batch_tool import without_banner_arrows

    soup = BeautifulSoup(html, 'html.parser')
    try:
        cards, controls = soup.select(card_selector), soup.select(selector)
    except Exception:
        return None
    if not cards or not controls:
        return None
    # Reuse the harvest's exact media-component rule; never broaden its href,
    # pager, roster-overlap or media-only checks for remembered controls.
    banner = without_banner_arrows(html, card_selector, {'items': [{'selector': selector}], 'total': 1})
    if banner['total'] == 0:
        facts = [{'kind': 'banner_arrow', 'control_sha256': _sha(str(controls[0]))}]
    else:
        facts = []
        for control in controls:
            frame = _current_page_navigation(control, cards, url)
            if frame is None:
                return None
            node, mode = frame
            facts.append({'kind': 'current_page_navigation', 'control_sha256': _sha(str(control)),
                          'navigation_sha256': _sha(str(node)), 'navigation_kind': mode})
    return {'basis': 'current_nonpagination_controls', 'url': url, 'unit_selector': card_selector,
            'selector': selector, 'source_sha256': _sha(html),
            'roster_sha256': _sha(''.join(str(card) for card in cards)), 'controls': facts}


def nonpagination_evidence_valid(item: dict, url: str, selector: str, source_sha256: str) -> bool:
    proof = item.get('nonpagination') or {}
    if not isinstance(proof, dict):
        return False
    controls = proof.get('controls') or []
    return bool(proof.get('basis') == 'current_nonpagination_controls'
        and proof.get('url') == url and proof.get('unit_selector') == selector
        and proof.get('selector') == item.get('selector') and proof.get('source_sha256') == source_sha256
        and re.fullmatch(r'[a-f0-9]{64}', str(proof.get('roster_sha256', '')))
        and isinstance(controls, list) and type(item.get('count')) is int
        and item['count'] == len(controls) and controls
        and all(isinstance(control, dict) and control.get('kind') in {'banner_arrow', 'current_page_navigation'}
                and re.fullmatch(r'[a-f0-9]{64}', str(control.get('control_sha256', '')))
                and (control['kind'] == 'banner_arrow' or (
                    control.get('navigation_kind') in {'site_navigation', 'active_section_menu', 'link_only_breadcrumb'}
                    and re.fullmatch(r'[a-f0-9]{64}', str(control.get('navigation_sha256', '')))))
                for control in controls))


def without_nonpagination_controls(html: str, url: str, card_selector: str, pagination: dict) -> dict:
    """Use the same current classifications for candidate and remembered controls."""
    from server.browser_batch_tool import without_banner_arrows

    result = without_banner_arrows(html, card_selector, pagination)
    if not isinstance(result.get('items'), list):
        return result
    kept, removed = [], 0
    for item in result.get('items', []):
        if nonpagination_evidence(html, url, card_selector, item.get('selector', '')):
            removed += 1
        else:
            kept.append(item)
    return {**result, 'items': kept, 'total': result['total'] - removed,
            **({'current_navigation_ignored': removed} if removed else {})}
