"""Apply an explicitly browser-verified profile boundary to captured HTML.

Original captures are never modified. A failed boundary preserves the original
observation and produces a blocking issue; it never silently authorizes fallback.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from bs4 import BeautifulSoup, Tag


def _inline_hidden(node: Tag) -> bool:
    if node.has_attr('hidden') or str(node.get('aria-hidden', '')).strip().lower() == 'true':
        return True
    declarations = {}
    for declaration in str(node.get('style', '')).split(';'):
        if ':' in declaration:
            key, value = declaration.split(':', 1)
            declarations[key.strip().lower()] = re.sub(r'\s*!important\s*$', '', value.strip().lower())
    opacity_hidden = False
    opacity = declarations.get('opacity')
    if opacity is not None:
        try:
            opacity_hidden = float(opacity.rstrip('%')) <= 0
        except ValueError:
            pass
    return (declarations.get('display') == 'none'
            or declarations.get('visibility') in {'hidden', 'collapse'}
            or declarations.get('content-visibility') == 'hidden'
            or opacity_hidden)


# Elements that never render text; an inline display:none on them is not hidden content.
_NON_RENDERING = {'script', 'style', 'noscript', 'template'}
_MEDIA_TAGS = ['img', 'picture', 'svg', 'canvas', 'iframe', 'object', 'embed', 'video', 'audio']
_MEANINGFUL = re.compile(r'[\u4e00-\u9fff@\d]')
TRIVIAL_HIDDEN_CHARS = 16


def trivial_hidden(node: Tag) -> bool:
    """A hidden editor placeholder (e.g. 'invisible'): no media, a few Latin letters only.

    Anything that could be a name, e-mail, number or Chinese text is not trivial
    and still blocks, because hidden profile content may be a collapsed section.
    """
    text = node.get_text(' ', strip=True)
    if node.name in _MEDIA_TAGS or node.find(_MEDIA_TAGS):
        return False
    from server.word_toc import decoration_kind, toc_anchor
    if toc_anchor(node) is not None:
        return decoration_kind(node, _inline_hidden) is not None
    if len(text) > TRIVIAL_HIDDEN_CHARS:
        return False
    # Word marks its own never-displayed text (the '窗体顶端' / '窗体底端' form
    # boundaries of a pasted document) with mso-hide:all; no reader ever sees it.
    return not _MEANINGFUL.search(text) or _word_hidden(node)


def _word_hidden(node: Tag) -> bool:
    return (node.get_text('', strip=True) in {'窗体顶端', '窗体底端'}
            and 'mso-hide:all' in re.sub(r'\s+', '', str(node.get('style', ''))).lower())


def _node_path(node: Tag, stop: Tag | None = None) -> str:
    """A short structural path (tag, id, first classes) for scout feedback; no text."""
    parts = []
    for item in [node, *node.parents]:
        if not isinstance(item, Tag) or item is stop or item.name in {'[document]', 'html'}:
            break
        part = item.name
        if item.get('id'):
            part += '#' + str(item['id'])
        classes = item.get('class') or []
        if classes:
            part += ''.join('.' + value for value in classes[:2])
        if item.parent is not None and not item.get('id'):
            same = [sibling for sibling in item.parent.find_all(item.name, recursive=False)]
            if len(same) > 1:
                part += f':nth-of-type({same.index(item) + 1})'
        parts.append(part)
        if item.get('id') or len(parts) >= 4:
            break
    return ' > '.join(reversed(parts))


def hidden_blockers(html: str, selector: str, *, start_selector: str | None = None,
                    limit: int = 3) -> list[dict[str, Any]]:
    """Where a profile_content_selector_hidden verdict comes from: paths and sizes only."""
    soup = BeautifulSoup(html, 'html.parser')
    try:
        matches = soup.select(selector)
    except Exception:
        return []
    if len(matches) != 1:
        return []
    node = matches[0]
    found = []
    for item in [node, *[ancestor for ancestor in node.parents if isinstance(ancestor, Tag)]]:
        if _inline_hidden(item):
            found.append({'where': 'container' if item is node else 'ancestor', 'path': _node_path(item)})
    kept = list(node.children)
    if start_selector:
        try:
            markers = soup.select(start_selector)
        except Exception:
            markers = []
        if len(markers) == 1 and markers[0] in kept:
            kept = kept[kept.index(markers[0]) + 1:]
    seen: list[Tag] = []
    for child in kept:
        if not isinstance(child, Tag):
            continue
        for item in [child, *child.find_all(True)]:
            if item.name in _NON_RENDERING or any(item in found_node.descendants for found_node in seen):
                continue
            if _inline_hidden(item) and not trivial_hidden(item):
                seen.append(item)
                found.append({'where': 'descendant', 'path': _node_path(item, stop=node),
                              'text_chars': len(item.get_text('', strip=True)),
                              'media': len(item.find_all(_MEDIA_TAGS)) + int(item.name in _MEDIA_TAGS)})
    return found[:limit]


def scope_profile_html(html: str, selector: str, url: str, *, start_selector: str | None = None
                       ) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """Return a complete selected subtree copy, evidence, or a blocking issue."""
    def fail(status: str):
        issue = {'url': url, 'status': status,
                 'selector': start_selector if status.startswith('profile_content_start_') else selector,
                 'profile_content_selector': selector}
        if start_selector is not None:
            issue['profile_content_start_selector'] = start_selector
        return html, None, issue

    if not isinstance(selector, str) or not selector.strip():
        return fail('profile_content_selector_invalid')
    soup = BeautifulSoup(html, 'html.parser')
    try:
        matches = soup.select(selector)
    except Exception:
        return fail('profile_content_selector_invalid')
    if not matches:
        return fail('profile_content_selector_missing')
    if len(matches) != 1:
        return fail('profile_content_selector_multiple')
    node = matches[0]
    if node.name in {'html', 'body', 'header', 'footer', 'nav', 'script', 'style', 'noscript', 'template'}:
        return fail('profile_content_selector_unsafe')
    ancestors = [ancestor for ancestor in node.parents if isinstance(ancestor, Tag)]
    if any(_inline_hidden(item) for item in [node, *ancestors]):
        return fail('profile_content_selector_hidden')
    if start_selector is not None:
        if not isinstance(start_selector, str) or not start_selector.strip():
            return fail('profile_content_start_selector_invalid')
        try:
            markers = soup.select(start_selector)
        except Exception:
            return fail('profile_content_start_selector_invalid')
        if not markers:
            return fail('profile_content_start_selector_missing')
        if len(markers) != 1:
            return fail('profile_content_start_selector_multiple')
        marker = markers[0]
        if marker.parent is not node:
            return fail('profile_content_start_selector_not_direct_child')
        # A sibling boundary removes complete DOM nodes, never words or paragraphs.
        for child in list(node.contents):
            is_marker = child is marker
            child.extract()
            if is_marker:
                break
    placeholders = []
    toc_decorations = []
    for item in node.find_all(True):
        if item.name in _NON_RENDERING or any(item in found.descendants for found in placeholders):
            continue
        if _inline_hidden(item):
            if not trivial_hidden(item):
                return fail('profile_content_selector_hidden')
            placeholders.append(item)
            from server.word_toc import decoration_kind
            if kind := decoration_kind(item, _inline_hidden):
                from server.word_toc import node_selector
                toc_decorations.append({'path': node_selector(item), 'kind': kind,
                    'node_sha256': hashlib.sha256(str(item).encode('utf-8')).hexdigest(),
                    'text_chars': len(item.get_text('', strip=True))})
    for item in placeholders:  # never captured: a reader does not see them
        item.decompose()
    text_copy = BeautifulSoup(str(node), 'html.parser')
    for non_content in text_copy(['script', 'style', 'noscript', 'template']):
        non_content.decompose()
    has_text = bool(text_copy.get_text(' ', strip=True))
    from server.image_biography import media_references
    has_media = bool(media_references(str(text_copy), url))
    if not has_text and not has_media:
        return fail('profile_content_start_selector_empty' if start_selector is not None
                    else 'profile_content_selector_empty')
    selected = str(node)
    evidence = {'profile_content_selector': selector,
                'profile_content_start_selector': start_selector,
                'original_html_sha256': hashlib.sha256(html.encode('utf-8')).hexdigest(),
                'selected_html_sha256': hashlib.sha256(selected.encode('utf-8')).hexdigest(),
                'attribution_scope': 'verified_profile_container',
                'hidden_placeholders_removed': len(placeholders),
                'word_toc_decorations_removed': len(toc_decorations),
                'word_toc_decorations': toc_decorations,
                'image_only_candidate': not has_text and has_media}
    return '<html><body>' + selected + '</body></html>', evidence, None
