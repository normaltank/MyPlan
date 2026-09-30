"""
Robust cross-page template inference, tolerant to counters, timestamps,
weekday strings and rotating widgets. Near-linear, no tree edit distance.

For every text leaf of page A:
  1. bucket key   = tag-name path from root (transparent tags removed)
  2. alignment    = k-th leaf of a bucket in A vs leaves k-w..k+w of the same
                    bucket in page B (occurrence-order alignment inside the
                    bucket). This tolerates inserted/removed siblings while
                    preventing a teacher name that happens to appear somewhere
                    in another view from matching by coincidence.
  3. similarity   = Jaccard of char bigrams after masking volatile content
                    (digit runs -> '#', whitespace removed, lower-cased)
  4. score s(l)   = min over sibling pages of the best windowed similarity
                    (template = present in EVERY page)
  5. volatility   = optional refetch of the SAME URL: a leaf whose masked
                    text changes between two fetches is page noise (rotating
                    ticker, random recommendation) and is marked template.
Threshold theta: Otsu split of the text-length-weighted score histogram
(self-calibrating per site), clamped to [0.5, 0.95].
Containers: bottom-up closure - an element is template iff it has text
leaves and all of them are template. in_template() then covers attributes
(mailto:, images) inside those containers.

Cost: O(N * (2w+1) * P) bigram-set comparisons; N leaves, P sibling pages.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from bs4 import BeautifulSoup, Tag

from iso_mining import IGNORE, TRANSPARENT, element_children

_DIGITS = re.compile(r"\d+")
_SPACE = re.compile(r"\s+")


def mask(text: str) -> str:
    return _DIGITS.sub("#", _SPACE.sub("", text.lower()))


def bigrams(s: str) -> frozenset:
    return frozenset(s[i:i + 2] for i in range(len(s) - 1)) if len(s) > 1 else frozenset([s])


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b)


def _path(t: Tag) -> str:
    return "/".join(p.name for p in reversed(list(t.parents))
                    if isinstance(p, Tag) and p.name not in TRANSPARENT) + "/" + t.name


def text_leaves(soup: BeautifulSoup) -> list[Tag]:
    out = []
    for t in soup.find_all(True):
        if t.name in IGNORE or t.name in TRANSPARENT:
            continue
        if not element_children(t) and t.get_text(strip=True):
            out.append(t)
    return out


class _Page:
    def __init__(self, soup: BeautifulSoup):
        self.leaves = text_leaves(soup)
        self.buckets: dict[str, list[tuple[Tag, frozenset]]] = defaultdict(list)
        self.pos: dict[int, tuple[str, int]] = {}
        for leaf in self.leaves:
            key = _path(leaf)
            self.pos[id(leaf)] = (key, len(self.buckets[key]))
            self.buckets[key].append((leaf, bigrams(mask(leaf.get_text(" ", strip=True)))))


def _best_sim(a: _Page, leaf: Tag, b: _Page, w: int) -> float:
    key, k = a.pos[id(leaf)]
    mine = a.buckets[key][k][1]
    other = b.buckets.get(key)
    if not other:
        return 0.0
    lo, hi = max(0, k - w), min(len(other), k + w + 1)
    return max((jaccard(mine, other[i][1]) for i in range(lo, hi)), default=0.0)


def otsu(scores: list[float], weights: list[float], bins: int = 40,
         lo: float = 0.5, hi: float = 0.95, default: float = 0.8) -> float:
    if len(scores) < 8:
        return default
    hist = [0.0] * bins
    for s, w in zip(scores, weights):
        hist[min(bins - 1, int(s * bins))] += w
    total = sum(hist)
    mean_all = sum(i * h for i, h in enumerate(hist)) / total
    best, best_t, w0, sum0 = -1.0, default, 0.0, 0.0
    for t in range(bins - 1):
        w0 += hist[t]
        sum0 += t * hist[t]
        w1 = total - w0
        if w0 == 0 or w1 == 0:
            continue
        m0, m1 = sum0 / w0, (mean_all * total - sum0) / w1
        between = w0 * w1 * (m0 - m1) ** 2
        if between > best:
            best, best_t = between, (t + 1) / bins
    return min(hi, max(lo, best_t))


def infer_template_nodes_robust(soup: BeautifulSoup, others: list[BeautifulSoup],
                                refetches: Optional[list[BeautifulSoup]] = None,
                                window: int = 2, return_debug: bool = False,
                                *, pages: Optional[dict[int, _Page]] = None):
    # The caller owns the immutable soups and the cache for one cluster only.
    # Do not use setdefault: its eager default would rebuild every index.
    def indexed(source: BeautifulSoup) -> _Page:
        if pages is None:
            return _Page(source)
        key = id(source)
        if key not in pages:
            pages[key] = _Page(source)
        return pages[key]

    A = indexed(soup)
    Bs = [indexed(o) for o in others]
    Rs = [indexed(r) for r in (refetches or [])]
    scores, weights, volatile = [], [], set()
    for leaf in A.leaves:
        s = min((_best_sim(A, leaf, B, window) for B in Bs), default=0.0)
        if Rs and min(_best_sim(A, leaf, R, 0) for R in Rs) < 1.0:
            volatile.add(id(leaf))
            s = 1.0
        scores.append(s)
        weights.append(len(leaf.get_text(strip=True)))
    theta = otsu(scores, weights)
    tmpl_leaf = {id(l) for l, s in zip(A.leaves, scores) if s >= theta}

    # bottom-up closure over containers
    total, hit = defaultdict(int), defaultdict(int)
    for leaf in A.leaves:
        is_t = id(leaf) in tmpl_leaf
        for node in [leaf] + [p for p in leaf.parents if isinstance(p, Tag)]:
            total[id(node)] += 1
            hit[id(node)] += is_t
    ids = {nid for nid, n in total.items() if n and hit[nid] == n}
    if return_debug:
        return ids, dict(theta=theta, volatile=len(volatile),
                         scores={l.get_text(" ", strip=True)[:30]: round(s, 2)
                                 for l, s in zip(A.leaves, scores)})
    return ids
