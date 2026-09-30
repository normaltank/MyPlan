"""
Virtual sibling records for plain-text name lists.

    <p>教授：张三、李四、王五<br>副教授：刘某（兼）、陈某</p>

has no sibling elements, so tree mining cannot see it. We treat the host's
text as a token stream and parse it with a small regular grammar:

    STREAM  := SECTION*
    SECTION := [ANCHOR] ITEM (DELIM ITEM)*
    ANCHOR  := TITLE [ '(' count ')' ] ':'        |  TITLE alone on a line
    ITEM    := CORE [ '(' annotation ')' ]
    DELIM   := 、 ， , ； ; ／ / | ｜ NEWLINE      (hard)
             | whitespace                         (soft, Han segments only)

Each ITEM becomes a VirtualNode with (host, start, end) offsets into the host
stream (pointer semantics, verbatim). All items are produced by the same ITEM
production, so they share one signature 'v|list' - delimiter choice is lexical
noise exactly like zebra classes are in DOM. Title anchors play the role
<h3> separators play in DOM grouping: they label, they do not split groups.

Scoring is the unchanged U = sum(2 p_r - 1); anchors add an odds prior
(ANCHOR_LR in iso_mining), and chosen text groups are triaged per record.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from iso_mining import IGNORE, TRANSPARENT, RecordGroup, VirtualNode, name_prob

TITLE = (r"(?:博士生导师|硕士生导师|博导|硕导|特聘教授|讲席教授|荣誉教授|客座教授|兼职教授|"
         r"副教授|助理教授|教授|副研究员|助理研究员|研究员|高级讲师|讲师|助教|"
         r"高级实验师|实验师|高级工程师|工程师|院士|博士后)")
_ANCHOR_COLON = re.compile(TITLE + r"\s*(?:[（(][^）)\n]{0,10}[)）])?\s*[:：]")
_ANCHOR_LINE = re.compile(r"(?m)^[ \t\u3000]*(" + TITLE + r")[ \t\u3000]*$")
_TITLE_ONLY = re.compile(r"^" + TITLE + r"$")
# generic section label at line start: '外籍教师：', '研究方向：', '兼职教授（3人）：'
_LABEL_COLON = re.compile(r"(?m)(?:^|(?<=[\n。；;]))[ \t\u3000]*([\u4e00-\u9fff]{2,8})"
                          r"\s*(?:[（(][^）)\n]{0,10}[)）])?\s*[:：]")
# labels that announce persons -> odds prior applies (ANCHOR_LR)
_PERSON_LABEL = re.compile(TITLE + r"|教师|老师|导师|师资|人员|专家|学者|成员|名单")
_HARD = re.compile(r"[、，,；;／/|｜\n]+")
_WS = re.compile(r"[ \t\u3000\xa0]+")
_WS2 = re.compile(r"[ \t\u3000\xa0]{2,}")
_ANNOT = re.compile(r"^(.*?)\s*[（(]([^）)]*)[)）]\s*$")
_TRIM = "。．.：:！!？?\"'“”‘’ \t\u3000\xa0"
_HAN_ONLY = re.compile(r"^[\u4e00-\u9fff]+$")
_HAN_DOMINANT = re.compile(r"[\u4e00-\u9fff]")
_SHAPE = [re.compile(r"^[\u4e00-\u9fff]{2,4}$"),
          re.compile(r"^[\u4e00-\u9fff]{1,6}[·•][\u4e00-\u9fff]{1,8}$"),
          re.compile(r"^[A-Z][a-zA-Z'\-]+(?:[ .][A-Z][a-zA-Z'\-.]*){1,3}$")]
_BLOCKISH = {"p", "div", "li", "tr", "td", "th", "dd", "dt", "h1", "h2", "h3", "h4", "h5", "h6"}
_INLINE_OK = {"a", "img"}


def name_shape(s: str) -> bool:
    return any(p.match(s) for p in _SHAPE)


# --------------------------------------------------------------------------
# Host selection and text stream
# --------------------------------------------------------------------------
def is_text_host(tag: Tag) -> bool:
    """Element whose descendants are only inline (transparent, <a>, <img>, <br>)."""
    if tag.name in IGNORE or tag.name in TRANSPARENT or tag.name in _INLINE_OK:
        return False
    for d in tag.find_all(True):
        if d.name in TRANSPARENT or d.name in _INLINE_OK or d.name == "br" or d.name in IGNORE:
            continue
        return False
    return True


def text_stream(host: Tag) -> tuple[str, list[Optional[str]]]:
    """Host text with <br>/block boundaries as '\\n' and a per-char href map.
    Source newlines are plain whitespace in HTML and are mapped to spaces."""
    chars: list[str] = []
    hrefs: list[Optional[str]] = []

    def emit(s: str, href: Optional[str]):
        chars.append(s)
        hrefs.extend([href] * len(s))

    def walk(node: Tag, href: Optional[str]):
        for c in node.children:
            if isinstance(c, Comment):
                continue
            if isinstance(c, NavigableString):
                emit(re.sub(r"[\r\n\t]", " ", str(c)), href)
            elif isinstance(c, Tag):
                if c.name == "br":
                    emit("\n", href)
                    continue
                if c.name in IGNORE:
                    continue
                h = c.get("href") if c.name == "a" else None
                walk(c, h or href)
                if c.name in _BLOCKISH:
                    emit("\n", href)

    walk(host, None)
    return "".join(chars), hrefs


def _listy(s: str) -> bool:
    body = s.strip()
    if len(body) < 5:
        return False
    if len(_HARD.findall(body)) >= 2 or _ANCHOR_COLON.search(body) or _ANCHOR_LINE.search(body):
        return True
    han_tokens = [t for t in _WS.split(body) if _HAN_ONLY.match(t)]
    return len(han_tokens) >= 3


# --------------------------------------------------------------------------
# Lexer
# --------------------------------------------------------------------------
def _sections(s: str) -> list[tuple[int, int, Optional[str]]]:
    marks = [(m.start(), m.end(), re.match(TITLE, m.group(0)).group(0))
             for m in _ANCHOR_COLON.finditer(s)]
    marks += [(m.start(), m.end(), m.group(1)) for m in _ANCHOR_LINE.finditer(s)]
    marks += [(m.start(), m.end(), m.group(1)) for m in _LABEL_COLON.finditer(s)]
    marks.sort(key=lambda x: (x[0], -x[1]))
    out, pos, cur = [], 0, None
    for a, b, title in marks:
        if a < pos:
            continue
        out.append((pos, a, cur))
        pos, cur = b, title
    out.append((pos, len(s), cur))
    return [(a, b, t) for a, b, t in out if s[a:b].strip()]


def _soft_split(s: str, a: int, b: int) -> list[tuple[int, int]]:
    """Whitespace splitting inside a Han segment. If double-width gaps exist
    they are the separators and single spaces are name padding ('王　伟');
    otherwise split on single spaces and re-join 1-char runs pairwise."""
    seg = s[a:b]
    if not _HAN_DOMINANT.search(seg) or re.search(r"[A-Za-z]{2,}", seg):
        return [(a, b)]                           # Latin names keep inner spaces
    sep = _WS2 if _WS2.search(seg.strip()) else _WS
    depth, inside = 0, [False] * len(seg)          # never split inside brackets
    for k, ch in enumerate(seg):
        if ch in "（(【[":
            depth += 1
        elif ch in "）)】]":
            depth = max(0, depth - 1)
        inside[k] = depth > 0
    spans, last = [], 0
    for m in sep.finditer(seg):
        if inside[m.start()]:
            continue
        if m.start() > last:
            spans.append((a + last, a + m.start()))
        last = m.end()
    if last < len(seg):
        spans.append((a + last, b))
    if sep is _WS2:                               # strip padding inside each span
        return spans
    merged, i = [], 0
    while i < len(spans):
        x0, x1 = spans[i]
        if x1 - x0 == 1 and i + 1 < len(spans) and spans[i + 1][1] - spans[i + 1][0] == 1:
            merged.append((x0, spans[i + 1][1]))
            i += 2
        else:
            merged.append((x0, x1))
            i += 1
    return merged


def viterbi_names(s: str, name_fn=name_prob, lo: int = 2, hi: int = 4) -> list[tuple[int, int]]:
    """Segment an undelimited Han run into 2-4 char pieces maximising
    sum log P(name). Used only under a title anchor."""
    n = len(s)
    best = [-math.inf] * (n + 1)
    back = [0] * (n + 1)
    best[0] = 0.0
    for j in range(1, n + 1):
        for L in range(lo, hi + 1):
            i = j - L
            if i >= 0 and best[i] > -math.inf:
                v = best[i] + math.log(max(name_fn(s[i:j]), 1e-6))
                if v > best[j]:
                    best[j], back[j] = v, i
    if best[n] == -math.inf:
        return [(0, n)]
    out, j = [], n
    while j > 0:
        out.append((back[j], j))
        j = back[j]
    return out[::-1]


# --------------------------------------------------------------------------
# E-mail spans inside a stream (masked before lexing, bound afterwards)
# --------------------------------------------------------------------------
_AT = r"(?:\s*(?:@|＠)\s*|\s*[\[\(（【]\s*(?:at|AT|艾特)\s*[\]\)）】]\s*)"
# Bounded quantifiers (RFC 5321 limits) + a left guard: the unbounded form
# rescanned every start position of a long alnum run, O(n^2) (20k chars: 11 s).
_EMAIL_SPAN = re.compile(r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9][A-Za-z0-9._%+\-]{0,63}" + _AT +
                         r"[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){0,8}\.[A-Za-z]{2,24}")
MASK = "\ue000"


def _glued_label_cut(raw: str) -> int:
    """Length of ``raw`` without an English word glued onto its top-level domain.

    Inline fields ('Email: a@b.edu.cn' then 'Tel: ...' in the next <span>) reach
    the text stream as 'a@b.edu.cnTel'. A top-level domain is one case; a
    capital that starts a new word ('cnTel', 'CNTel') is where the address ends.
    """
    dot = raw.rfind(".")
    label = raw[dot + 1:]
    for i in range(2, len(label)):
        if label[i].isupper() and (label[i - 1].islower()
                                   or (i + 1 < len(label) and label[i + 1].islower())):
            return dot + 1 + i
    return len(raw)


def email_spans(s: str) -> list[tuple[int, int, str]]:
    out = []
    for m in _EMAIL_SPAN.finditer(s):
        end = m.start() + _glued_label_cut(m.group(0))
        addr = re.sub(r"\s+", "", re.sub(_AT, "@", s[m.start():end])).lower().strip(".")
        out.append((m.start(), end, addr))
    return out


def mask_spans(s: str, spans) -> str:
    chars = list(s)
    for a, b, *_ in spans:
        for k in range(a, b):
            if chars[k] != "\n":
                chars[k] = MASK
    return "".join(chars)


# --------------------------------------------------------------------------
# Segmentation = the text topology (section > hard cell > soft cell)
# --------------------------------------------------------------------------
def segment(s: str) -> list[tuple[int, int, Optional[str], list]]:
    """[(sa, sb, title, [(pa, pb, [(a, b), ...]), ...]), ...] on a masked stream."""
    out = []
    for sa, sb, title in _sections(s):
        pieces, pos = [], sa
        for m in list(_HARD.finditer(s, sa, sb)) + [None]:
            end = m.start() if m else sb
            if end > pos:
                pieces.append((pos, end, _soft_split(s, pos, end)))
            if m:
                pos = m.end()
        out.append((sa, sb, title, pieces))
    return out


class HostTopology:
    """Maps a character interval of the host stream to virtual container keys:
        ('sec', host, i) > ('hard', host, i, j) > ('item', host, i, j, t) > ('soft', host, i, j, k)
    'item' = a name cell plus the attribute cells that follow it, so in
    '周八 zb@qq.com　吴九 wj@x.cn' each address is exclusive to its own item.
    so text positions behave like nested DOM nodes for LCA / exclusivity."""

    def __init__(self, host: Tag):
        self.host = host
        self.raw, self.hrefs = text_stream(host)
        self.emails = email_spans(self.raw)
        self.masked = mask_spans(self.raw, self.emails)
        self.segs = segment(self.masked)

    def _is_attr(self, xa: int, xb: int) -> bool:
        """Attribute cell = e-mail only, a bare title, or a bracketed note."""
        t = self.masked[xa:xb].strip(_TRIM)
        return (not t.replace(MASK, "").strip(_TRIM + "()（）【】[]")
                or bool(_TITLE_ONLY.match(t)) or t[:1] in "（(【[")

    def _items(self, softs: list) -> list[int]:
        """item index per soft cell: a non-attribute cell opens an item, the
        attribute cells that follow it (e-mail, title, note) join that item."""
        out, cur = [], -1
        for xa, xb in softs:
            if not self._is_attr(xa, xb) or cur < 0:
                cur += 1
            out.append(cur)
        return out

    def path(self, a: int, b: int) -> list[tuple]:
        h = id(self.host)
        for i, (sa, sb, _, pieces) in enumerate(self.segs):
            if not (sa <= a < sb):
                continue
            keys = [("sec", h, i)]
            for j, (pa, pb, softs) in enumerate(pieces):
                if not (pa <= a < pb):
                    continue
                keys.append(("hard", h, i, j))
                items = self._items(softs)
                for k, (xa, xb) in enumerate(softs):
                    if xa <= a < xb:
                        keys.append(("item", h, i, j, items[k]))
                        keys.append(("soft", h, i, j, k))
                        break
                break
            return keys
        return []


def lex(host: Tag, s: str, hrefs: list[Optional[str]]) -> list[VirtualNode]:
    """s is the raw stream; e-mails are masked so that '张三（zs@x.cn）' yields
    core '张三' with the address as annotation, and a space-separated address
    never becomes a person record."""
    masked = mask_spans(s, email_spans(s))
    nodes: list[VirtualNode] = []
    for sa, sb, title, pieces in segment(masked):
        person_label = bool(title and _PERSON_LABEL.search(title))
        for pa, pb, softs in pieces:
            for a, b in softs:
                raw = masked[a:b]
                lstrip = len(raw) - len(raw.lstrip(_TRIM))
                a2, b2 = a + lstrip, a + len(raw.rstrip(_TRIM))
                if b2 <= a2:
                    continue
                span_m = masked[a2:b2]
                annot = None
                m = _ANNOT.match(span_m)
                if m and m.group(1):
                    core = m.group(1).strip()
                    annot = s[a2 + m.start(2):a2 + m.end(2)].strip()
                else:
                    core = span_m
                core = core.replace(MASK, "").strip(_TRIM)
                core = _WS.sub("", core) if _HAN_ONLY.match(_WS.sub("", core) or "x") else core
                if not core or _TITLE_ONLY.match(core):
                    continue
                hs = {hrefs[k] for k in range(a2, b2)}
                href = hs.pop() if len(hs) == 1 else None
                if person_label and _HAN_ONLY.match(core) and len(core) >= 6:
                    base = a2 + span_m.find(core[:1])
                    for x, y in viterbi_names(core):
                        nodes.append(VirtualNode(host, base + x, base + y, core[x:y], core[x:y],
                                                 title, None, href, "viterbi",
                                                 name_shape(core[x:y]), person_label))
                    continue
                nodes.append(VirtualNode(host, a2, b2, s[a2:b2], core, title, annot, href,
                                         "lex", name_shape(core), person_label))
    return nodes


def generate_virtual_groups(soup: BeautifulSoup, min_items: int = 2) -> list[RecordGroup]:
    groups = []
    for host in soup.find_all(True):
        if not is_text_host(host):
            continue
        s, hrefs = text_stream(host)
        if not _listy(s):
            continue
        vnodes = lex(host, s, hrefs)
        if len(vnodes) >= min_items:
            groups.append(RecordGroup([[v] for v in vnodes], "v|list", [host]))
    return groups
