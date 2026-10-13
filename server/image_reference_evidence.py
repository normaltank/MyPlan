"""Reconstruct the supported literal-reference subset from saved originals."""
from __future__ import annotations

import re
from urllib.parse import quote, urljoin


def absolute(value, base):
    return quote(urljoin(base, value), safe=":/?#[]@!$&'()*+,;=%")


def structural_selector(value):
    if not re.fullmatch(r'[A-Za-z0-9_#.*\s>:,\-]+', value):
        raise ValueError('unsupported selector')
    value = re.sub(r'::?(?:before|after)\b', '', value)
    value = re.sub(r':(?:focus-visible|focus-within|hover|active|focus|link|visited)\b', '', value)
    if ':' in value:
        raise ValueError('unsupported selector state')
    return ','.join(re.sub(r'\s*>\s*', '>', re.sub(r'\s+', ' ', part.strip())) for part in value.split(','))


def path(node):
    parts = []
    while node is not None and node.name != '[document]':
        children = node.parent.find_all(recursive=False) if node.parent else [node]
        parts.insert(0, f'{node.name}:nth-child({next(i for i, child in enumerate(children, 1) if child is node)})')
        node = node.parent
    return '>'.join(parts)


def _comments(text):
    result, index, quoted, url_depth = [], 0, None, 0
    while index < len(text):
        char = text[index]
        if quoted:
            result.append(char)
            if char == '\\':
                index += 1
                if index >= len(text):
                    raise ValueError('unfinished CSS escape')
                result.append(text[index])
            elif char == quoted:
                quoted = None
        elif char in "\"'":
            quoted = char
            result.append(char)
        elif text[index:index+4].lower() == 'url(':
            result.append(text[index:index+4])
            index += 3
            url_depth = 1
        elif url_depth:
            if char == ')':
                url_depth = 0
            result.append(char)
        elif text[index:index+2] == '/*':
            end = text.find('*/', index+2)
            if end < 0:
                raise ValueError('unfinished CSS comment')
            index = end+1
        else:
            result.append(char)
        index += 1
    if quoted:
        raise ValueError('unfinished CSS string')
    return ''.join(result)


def _top_positions(text, characters):
    quoted, depth = None, 0
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
            continue
        if char == '\\':
            escaped = True
        elif quoted:
            if char == quoted:
                quoted = None
        elif char in "\"'":
            quoted = char
        elif char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif depth == 0 and char in characters:
            yield index, char
        if depth < 0:
            raise ValueError('invalid CSS parentheses')
    if quoted or depth or escaped:
        raise ValueError('unbalanced CSS')


def _declarations(body):
    start = 0
    for end, _ in [*_top_positions(body, ';'), (len(body), ';')]:
        value = body[start:end].strip()
        start = end+1
        if not value:
            continue
        if ':' not in value:
            raise ValueError('invalid declaration')
        key, text = value.split(':', 1)
        yield key.strip().lower(), re.sub(r'\s+!important\s*$', '', text.strip(), flags=re.I)


def _urls(value, base):
    if re.search(r'(?:image-set|cross-fade|paint|element|attr)\s*\(', value, re.I):
        raise ValueError('opaque image function')
    if re.search(r'url\s*\(', value, re.I) and '\\' in value:
        raise ValueError('escaped CSS URL')
    return [absolute(match[2], base) for match in re.finditer(r'url\(\s*(["\']?)(.*?)\1\s*\)', value, re.I)]


def _styles(text, base, failed, soup):
    text = _comments(text)
    positions = list(_top_positions(text, '{};'))
    start, level, opened, selector = 0, 0, None, None
    for index, char in positions:
        if char == ';' and level == 0:
            if any(url in failed for url in _urls(text[start:index], base)):
                raise ValueError('unsupported CSS directive')
            start = index+1
        elif char == '{':
            if level == 0:
                selector, opened = text[start:index].strip(), index
            level += 1
        elif char == '}':
            level -= 1
            if level < 0:
                raise ValueError('unbalanced CSS blocks')
            if level:
                continue
            body = text[opened+1:index]
            if selector.startswith('@') or any(ch in body for _, ch in _top_positions(body, '{}')):
                if any(url in failed for url in _urls(body, base)):
                    raise ValueError('unsupported image rule')
            else:
                for property_name, value in _declarations(body):
                    for url in _urls(value, base):
                        if url not in failed:
                            continue
                        if property_name != 'background-image' or not re.fullmatch(r'url\(\s*(["\']?)(.*?)\1\s*\)', value, re.I):
                            raise ValueError('unsupported image declaration')
                        selected = structural_selector(selector)
                        yield (url, 'css', selected, tuple(sorted(path(node) for node in soup.select(selected))))
            start = index+1
    if level:
        raise ValueError('unbalanced CSS blocks')


def srcset(value, base):
    candidates = value.split(',')
    result = []
    for candidate in candidates:
        fields = candidate.strip().split()
        if (not fields or len(fields) > 2 or (len(candidates) > 1 and len(fields) != 2)
                or (len(fields) == 2 and not re.fullmatch(r'(?:\d+w|\d+(?:\.\d+)?x)', fields[1]))
                or fields[0].startswith('data:')):
            raise ValueError('unsupported srcset')
        result.append(absolute(fields[0], base))
    return result


def dom_graph(soup, failed, base):
    if soup.select('base,svg,object,embed,iframe'):
        raise ValueError('opaque document reference')
    for node in soup.find_all(True):
        if node.get('background') is not None and absolute(node['background'], base) in failed:
            raise ValueError('unsupported native image reference')
        attrs = []
        if node.name in {'img', 'source'}:
            if node.name == 'img' and node.get('src'):
                attrs.append(('src', absolute(node['src'], base)))
            if node.get('srcset'):
                attrs.extend(('srcset', url) for url in srcset(node['srcset'], base))
        elif node.name == 'input' and str(node.get('type', '')).lower() == 'image' and node.get('src'):
            attrs.append(('src', absolute(node['src'], base)))
        elif node.name == 'video' and node.get('poster'):
            attrs.append(('poster', absolute(node['poster'], base)))
        elif node.name == 'link' and absolute(node.get('href', ''), base) in failed:
            raise ValueError('unbound resource link')
        for attribute, url in attrs:
            if url in failed:
                yield (url, 'dom', node.name, attribute, path(node))
        if node.get('style'):
            for property_name, value in _declarations(_comments(node['style'])):
                for url in _urls(value, base):
                    if url in failed:
                        if property_name != 'background-image' or not re.fullmatch(r'url\(\s*(["\']?)(.*?)\1\s*\)', value, re.I):
                            raise ValueError('unsupported inline image')
                        yield (url, 'css', 'inline:'+path(node), (path(node),))


def css_graph(sources, failed, soup, base):
    for url, text in sources:
        yield from _styles(text, url or base, failed, soup)
