"""
L2 isomorphic record mining (practical MDR/DEPTA variant).

Input : any BeautifulSoup DOM (+ optional page_url, optional template node ids)
Output: MiningResult with the best record group (teacher cards), its minimal
        closure container (LCA), per-record person probabilities, and
        non-overlapping alternative groups.

Design
------
1. Structural fingerprint  sig_d(n)
   - inline/presentational wrappers (span, b, i, font, ...) are TRANSPARENT:
     their children are lifted into the parent, so <td><span><a>X</a></span></td>
     and <td><a>X</a></td> have the same skeleton.
   - all attributes (class/id/style) are dropped -> zebra-stripe classes,
     'active', random css hashes cannot split a group.
   - <br> is dropped and adjacent text nodes merge into one '#t' token.
   - consecutive identical child signatures collapse to one token with '+'
     (Kleene closure), so a row with 5 cells and a last row with 3 cells,
     or a card with 1 vs 2 phone lines, share a signature.
   - depth-capped Merkle hash: sig_d(n) = H(label(n), collapse([sig_{d-1}(c)]))

2. Candidates
   - k=1 : children of a parent grouped by signature (non-contiguous allowed,
           so '<h3>教授</h3> a a a <h3>副教授</h3> a a' is one group)
   - k=2 : contiguous repeated sibling pairs (record spans two siblings,
           e.g. photo-row + name-row tables)
   - positive groups with equal record signature are merged across parents
     (grid tables: every <tr> yields a <td> group -> one group, LCA=<table>)

3. Scoring without per-site thresholds
   Each record r gets a posterior-like p_r = P(person | r):
       p_r = name_prob(primary tokens of r) * m
       m   = (1 - H_url_norm) * (1 - self_ref)        (only if records link)
   Candidate utility  U = sum_r (2 p_r - 1)
       = E[#correct records] - E[#wrong records]
   Decision: accept iff U > 0 (Bayes boundary, not a tuned threshold);
             pick argmax U among accepted, non-overlapping groups.
   Navigation: names unlikely + heterogeneous URL templates -> U < 0
   Pager    : numeric tokens + links back to the list page -> U < 0
   Row-level vs cell-level grid: the cell group has more records with p~1,
             the row group has fewer -> argmax picks cells.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional
from urllib.parse import unquote, urljoin, urlparse

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from name_labels import without_degree_annotation

# --------------------------------------------------------------------------
# 1. Structural fingerprint
# --------------------------------------------------------------------------
TRANSPARENT = {
    "span", "b", "i", "em", "strong", "font", "u", "small", "big", "label",
    "sup", "sub", "s", "strike", "center", "nobr", "abbr", "cite", "mark", "q",
}
IGNORE = {
    "script", "style", "noscript", "template", "iframe", "svg", "head",
    "meta", "link", "input", "select", "option", "button", "form", "br",
}
TEXT = "#t"


@dataclass(eq=False)
class VirtualNode:
    """A record carved out of a text stream (see text_lists.py). Identity-hashed;
    (host, start, end) is a pointer into the host's text stream, so extraction
    stays verbatim/pointer-based like the rest of the pipeline."""
    host: Tag
    start: int
    end: int
    text: str                  # raw span as it appears in the stream
    core: str                  # candidate name (annotations stripped)
    section: Optional[str] = None   # title anchor, e.g. '副教授'
    annot: Optional[str] = None     # '(博导)', '(兼)'
    href: Optional[str] = None
    via: str = "lex"           # 'lex' | 'viterbi'
    shape_ok: bool = True
    person_anchor: bool = False     # section label announces persons
    name: str = "#vtext"


def _is_text(x) -> bool:
    return isinstance(x, NavigableString) and not isinstance(x, Comment)


def norm_children(tag: Tag) -> list:
    """Children after lifting transparent wrappers; returns Tags and TEXT markers.
    Adjacent text (including text across removed <br>) merges into one TEXT."""
    out: list = []
    for c in tag.children:
        if isinstance(c, Comment):
            continue
        if _is_text(c):
            if c.strip():
                if not out or out[-1] is not TEXT:
                    out.append(TEXT)
            continue
        if not isinstance(c, Tag) or c.name in IGNORE:
            continue
        if c.name in TRANSPARENT:
            for g in norm_children(c):
                if g is TEXT and out and out[-1] is TEXT:
                    continue
                out.append(g)
        else:
            out.append(c)
    return out


def element_children(tag: Tag) -> list[Tag]:
    return [c for c in norm_children(tag) if c is not TEXT]


def _label(tag: Tag) -> str:
    return tag.name


def _collapse(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for t in tokens:
        if out and (out[-1] == t or out[-1] == t + "+"):
            out[-1] = t + "+"
        else:
            out.append(t)
    return out


class Signer:
    """Memoised depth-capped Merkle signature."""

    def __init__(self, depth: int = 4, digest_size: int = 8):
        self.depth = depth
        self.digest_size = digest_size
        self._memo: dict[tuple[int, int], str] = {}

    def sig(self, tag: Tag, depth: Optional[int] = None) -> str:
        d = self.depth if depth is None else depth
        key = (id(tag), d)
        if key in self._memo:
            return self._memo[key]
        if d == 0:
            s = _label(tag)
        else:
            kids = [TEXT if c is TEXT else self.sig(c, d - 1) for c in norm_children(tag)]
            body = ",".join(_collapse(kids))
            raw = f"{_label(tag)}({body})".encode()
            s = hashlib.blake2b(raw, digest_size=self.digest_size).hexdigest()
        self._memo[key] = s
        return s


# --------------------------------------------------------------------------
# 2. Name likelihood (statistical prior, not a noise blacklist)
# --------------------------------------------------------------------------
# Top Chinese surnames in approximate frequency order (rank-based Zipf prior).
_SURNAMES = (
    "王李张刘陈杨黄赵吴周徐孙马朱胡郭何高林罗郑梁谢宋唐许韩冯邓曹彭曾肖田董袁潘"
    "于蒋蔡余杜叶程苏魏吕丁任沈姚卢姜崔钟谭陆汪范金石廖贾夏韦付方白邹孟熊秦邱江"
    "尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤常温康施文牛樊葛邢安齐"
    "易乔伍庞颜倪庄聂章鲁岳翟殷詹申欧耿关兰焦俞左柳甘祝包宁尚符舒阮柯纪梅童凌毕"
    "单季裴霍涂成苗谷盛曲翁冉骆蓝路游辛靳管柴蒙鲍华喻祁蒲房滕屈饶解牟艾尤阳时穆"
    "农司卓古吉缪简车项连芦麦褚娄窦戚岑景党宫费卜冷晏席卫米柏宗瞿桂全佟应臧闵苟"
    "邬边卞姬师和仇栾隋商刁沙荣巫寇桑郎甄丛仲虞敖巩明佘池查麻苑迟邝瓦荀舍南栗门修慕颉郁妥喜"
    # Field recall repair (湖南师范大学 batch, 2026-09-24): the first nine were absent,
    # so real in-post faculty were discarded at the p < 0.5 gate (历史文化学院
    # 傅新球/粟壬波/邸宏霆, 生命科学学院 银海强/容明强/穰杰/禹琪芳, plus 蔺/邴 seen in the
    # hospital fixture).  The rest are genuine surnames whose leading character
    # does not begin common words, because the prior admits any listed surname:
    # a recall miss is caught by roster_card_mismatch, but a word admitted as a
    # person is silent. Words like 宿舍/索引/薄膜/韶山 must stay below the gate.
    "傅粟邸银容穰禹蔺邴萧阎赖屠匡茹狄奚庾晁扈邰郗竺芮郜宓璩逄隗郦"
)
_COMPOUND = {"欧阳", "司马", "上官", "诸葛", "东方", "皇甫", "尉迟", "公孙", "慕容",
             "夏侯", "令狐", "宇文", "长孙", "司徒", "端木", "独孤", "南宫", "申屠", "完颜"}
_SURNAME_RANK = {ch: i for i, ch in enumerate(_SURNAMES)}
_HAN = re.compile(r"^[\u4e00-\u9fff]{2,4}$")
_MINORITY = re.compile(r"^[\u4e00-\u9fff]{1,6}[·•][\u4e00-\u9fff]{1,8}$")
_LATIN = re.compile(r"^[A-Z][a-zA-Z'\-]+(?:[ .][A-Z][a-zA-Z'\-.]*){1,3}$")
_TOKEN_SPLIT = re.compile(r"[\s\u3000,，、;；:：|/()（）\[\]【】<>《》\"'“”]+")


def latin_name_shape(s: str) -> bool:
    """Unicode Latin words, retaining accents, particles and joined surnames.

    This is a field shape prior, just like the Han surname prior; it does not
    remove content or establish faculty eligibility outside a roster.
    """
    words = s.split()
    if not 2 <= len(words) <= 8:
        return False
    for word in words:
        if not word or not word[0].isalpha() or not word[-1].isalpha():
            return False
        if not all((ch.isalpha() and 'LATIN' in unicodedata.name(ch, ''))
                   or ch in "-'’" for ch in word):
            return False
    return words[0][0].isupper() and words[-1][0].isupper()


_LEADING_LABEL = re.compile(r"^[\s\u3000]*(?:(?:姓\s*名|name)\s*[:：]\s*)?", re.I)


def _latin_tokens(text: str) -> list[str]:
    # Only a run that opens the text (optionally after a 姓名 label) is a
    # name slot; Latin words later in a Chinese biography are institutions.
    text = unicodedata.normalize('NFC', text)
    text = text[_LEADING_LABEL.match(text).end():]
    if not text or not (text[0].isalpha() and 'LATIN' in unicodedata.name(text[0], '')):
        return []
    return _latin_runs(text)[:1]


def _latin_runs(text: str) -> list[str]:
    # The maximal Latin run ends at a different script (degree/title metadata
    # on the Malaga cards), punctuation or a number; spaces remain in the name.
    runs, current = [], []
    for ch in unicodedata.normalize('NFC', text):
        if (ch.isalpha() and 'LATIN' in unicodedata.name(ch, '')) or ch.isspace() or ch in "-'’.":
            current.append(ch)
        else:
            run = ''.join(current).strip()
            if _LATIN.fullmatch(run) or latin_name_shape(run):
                runs.append(run)
            current = []
    run = ''.join(current).strip()
    if _LATIN.fullmatch(run) or latin_name_shape(run):
        runs.append(run)
    return runs


_INVISIBLE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")


def strip_invisible(t: str) -> str:
    """Drop CMS-injected zero-width padding before any name shape test or store."""
    return _INVISIBLE.sub("", t or "")


def name_prob(s: str) -> float:
    """P(string is a person name). Rank-based surname prior; replace with a
    trained char-level model in production (same interface)."""
    # Zero-width padding injected by some CMS templates (e.g. 生命科学学院's
    # '张\u200b国进') previously fell through every name shape and scored 0.03.
    # _collapse_han owns both normalisations so the two call sites cannot drift.
    s = _collapse_han(s or "").strip()
    if not s:
        return 0.0
    if any(ch.isdigit() for ch in s):
        return 0.01
    if _HAN.match(s):
        if s[:2] in _COMPOUND and len(s) >= 3:
            return 0.9
        r = _SURNAME_RANK.get(s[0])
        if r is None:
            return 0.08                      # rare surname: possible but unlikely
        # rank 0 -> 0.97, tail -> ~0.6 ; 4-char strings slightly less name-like
        p = 0.6 + 0.37 * math.exp(-r / 120)
        return p * (0.85 if len(s) == 4 else 1.0)
    if _MINORITY.match(s):
        return 0.8
    if _LATIN.match(s) or latin_name_shape(unicodedata.normalize('NFC', s)):
        return 0.6
    return 0.03


def _load_rare_surnames() -> tuple[frozenset[str], frozenset[str]]:
    text = Path(__file__).with_name("rare_surnames.txt").read_text(encoding="utf-8")
    entries = [line.strip() for line in text.splitlines()
               if line.strip() and not line.startswith("#")]
    return (frozenset(e for e in entries if len(e) == 1),
            frozenset(e for e in entries if len(e) == 2))


_RARE_SINGLE, _RARE_COMPOUND = _load_rare_surnames()


def rare_surname_name(s: str) -> bool:
    """A 2-4 Han string below the name gate whose surname is a rare one.

    name_prob deliberately keeps these below 0.5: many rare surnames are also
    everyday characters (宿舍, 学院, 天气), so admitting them would put words
    into the roster silently. They go to human review instead of being dropped."""
    s = _collapse_han(s or "").strip()
    if not _HAN.match(s) or name_prob(s) >= 0.5:
        return False
    if len(s) >= 3 and s[:2] in _RARE_COMPOUND:
        return True
    return s[0] in _RARE_SINGLE


def record_text(nodes: Iterable[Tag]) -> str:
    parts: list[str] = []
    for n in nodes:
        if isinstance(n, VirtualNode):
            parts.append(n.core)
        else:
            parts.extend(n.stripped_strings)
    return " ".join(parts)


_HAN_WS = re.compile(r"[\u4e00-\u9fff\s\u3000]+")

def _collapse_han(t: str) -> str:
    """'孙  洁' -> '孙洁': CMS pad 2-char names to 3-char width. Only when the
    whole text is 2-4 Han chars plus whitespace, so '教授 张三' is untouched."""
    t = strip_invisible(t)
    if _HAN_WS.fullmatch(t):
        c = re.sub(r"[\s\u3000]+", "", t)
        if 2 <= len(c) <= 4:
            return c
        # A padded name may precede a separate, longer annotation token:
        # '王 奔 副教授'. Joining the whole label is too long, while splitting
        # it first exposes '王' as the supposed name. Join just the leading
        # run of single-character padding, preserving the following field.
        words = t.split()
        prefix = 0
        for word in words:
            if len(word) != 1:
                break
            prefix += 1
        if 2 <= prefix <= 3 and prefix < len(words):
            return "".join(words[:prefix]) + " " + " ".join(words[prefix:])
    return t

_LEADING_LIST_INDEX = re.compile(r"^[\s\u3000]*\d{1,4}[\s\u3000]*[.、,，)）:：]?[\s\u3000]*")

def _name_token(t: str) -> str:
    """Normalise one candidate token from a roster row.

    WebPlus-style rosters put the row ordinal inside the link text
    ('4 丁保扬', '2王颖', '1.宫春博（…）'), and the row date ('2026-05-22') reaches
    the token stream as well. A person's name never contains a digit, so the
    index is stripped first and any digit-bearing remainder is discarded —
    otherwise the extractor takes the serial as the name or drops the row.
    """
    s = _LEADING_LIST_INDEX.sub("", t or "").strip()
    # CMSes append decorative Unicode symbols to a link label. They are
    # outside the written name, so strip them by Unicode category rather
    # than maintaining a site-specific list of symbols or surnames.
    while s and unicodedata.category(s[-1]).startswith("S"):
        s = s[:-1].rstrip()
    while s and unicodedata.category(s[0]).startswith("S"):
        s = s[1:].lstrip()
    if not s or any(ch.isdigit() for ch in s):
        return ""
    return without_degree_annotation(s)


def primary_tokens(nodes: list[Tag], k: int = 3) -> list[str]:
    if nodes and isinstance(nodes[0], VirtualNode):
        return [nodes[0].core] if nodes[0].core else []
    toks: list[str] = []
    for n in nodes:                           # link texts first
        for a in n.find_all("a") if n.name != "a" else [n]:
            t = _collapse_han(a.get_text(" ", strip=True))
            if t:
                toks.extend(_latin_tokens(t))
                toks.extend(c for c in (_name_token(x) for x in _TOKEN_SPLIT.split(t)) if c)
    for n in nodes:                           # then every text leaf, han-collapsed
        leaves = [n.core] if isinstance(n, VirtualNode) else list(n.stripped_strings)
        for t in leaves:
            toks.extend(_latin_tokens(t))
            toks.extend(c for c in (_name_token(x) for x in _TOKEN_SPLIT.split(_collapse_han(t))) if c)
    seen, out = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            out.append(t)
        if len(out) >= k:
            break
    return out


# --------------------------------------------------------------------------
# 3. URL templating  (segment-wise, group-aware)
# --------------------------------------------------------------------------
# Each path segment is classified as a whole instead of regex-substituting
# digits inside it. VSB/博达 '/5d/4e/c13687a154958/page.htm' previously became
# '/{n}d/{n}e/...' (hex hash shredded into many templates -> H_url explosion).
_NUM = re.compile(r"\d+")
_HEX2 = re.compile(r"^[0-9a-f]{2}$", re.I)
_HEXN = re.compile(r"^(?=[^\d]*\d)[0-9a-f]{6,}$", re.I)              # >=6 hex incl. a digit
_UUID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$", re.I)
_B64ID = re.compile(r"^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_\-]{16,}$")  # opaque tokens


def _split_ext(seg: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)(\.[A-Za-z0-9]{1,5})$", seg)
    return (m.group(1), m.group(2)) if m and m.group(1) else (seg, "")


def _seg_class(seg: str, hash_position: bool) -> str:
    stem, ext = _split_ext(seg)
    if not stem:
        return seg
    if _HEX2.match(stem) and hash_position:
        c = "{h2}"                 # '5d', '03', 'ab' at a proven hash position
    elif stem.isdigit():
        c = "{n}"
    elif _UUID.match(stem):
        c = "{uuid}"
    elif _HEXN.match(stem):
        c = "{hex}"
    elif _B64ID.match(stem):
        c = "{id}"
    else:
        c = _NUM.sub("{n}", stem)  # mixed tokens: 'c13687a154958' -> 'c{n}a{n}'
    return c + ext


def url_templates(urls: list[str]) -> list[str]:
    """Group-aware normalisation. A position (netloc, #segments, index) is a
    'hash position' if any URL of the group has a digit-bearing 2-hex segment
    there; only then are letters-only pairs such as 'ab' generalised, so a
    real slug like '/de/' (German site) stays literal elsewhere."""
    parsed = [urlparse(u) for u in urls]
    segs = [[x for x in p.path.split("/") if x] for p in parsed]
    hashpos = set()
    for p, sg in zip(parsed, segs):
        for i, x in enumerate(sg):
            if _HEX2.match(x) and any(ch.isdigit() for ch in x):
                hashpos.add((p.netloc, len(sg), i))
    out = []
    for p, sg in zip(parsed, segs):
        path = "/".join(_seg_class(x, (p.netloc, len(sg), i) in hashpos) for i, x in enumerate(sg))
        q = "&".join(sorted(
            f"{k}={_seg_class(v, False)}" if v else k
            for k, _, v in (kv.partition("=") for kv in p.query.split("&") if kv)))
        out.append(f"{p.netloc}/{path}?{q}" if q else f"{p.netloc}/{path}")
    return out


def url_template(url: str) -> str:
    return url_templates([url])[0]


def _profile_href(href: str) -> bool:
    # Some CMSs put rendered error markup in href instead of a URL.
    if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
        return False
    if '<' in href or '>' in href:
        return False
    path = unquote(urlparse(href).path)
    return not re.search(r'<([A-Za-z][\w:-]*)(?:\s[^<>]*)?>[^<>]*</\1\s*>', path, re.I)


def first_href(nodes: list[Tag], base: Optional[str]) -> Optional[str]:
    for n in nodes:
        if isinstance(n, VirtualNode):
            if _profile_href(n.href or ''):
                return urljoin(base, n.href) if base else n.href
            continue
        for a in ([n] if n.name == "a" else []) + n.find_all("a"):
            h = (a.get("href") or "").strip()
            if _profile_href(h):
                return urljoin(base, h) if base else h
    return None


def norm_entropy(items: list[str]) -> float:
    n = len(items)
    if n < 2:
        return 0.0
    c = Counter(items)
    h = -sum(v / n * math.log(v / n) for v in c.values())
    return max(0.0, h / math.log(n))


# --------------------------------------------------------------------------
# 4. Tree utilities
# --------------------------------------------------------------------------
def ancestors_inclusive(tag: Tag) -> list[Tag]:
    if isinstance(tag, VirtualNode):
        return ancestors_inclusive(tag.host) + [tag]
    chain = [tag] + [p for p in tag.parents if isinstance(p, Tag)]
    return chain[::-1]                         # root ... tag


def lca(nodes: list[Tag]) -> Optional[Tag]:
    if not nodes:
        return None
    paths = [ancestors_inclusive(n) for n in nodes]
    common = None
    for level in zip(*paths):
        if all(x is level[0] for x in level):
            common = level[0]
        else:
            break
    return common


def css_path(tag: Tag) -> str:
    if isinstance(tag, VirtualNode):
        return f"{css_path(tag.host)}::text[{tag.start}:{tag.end}]"
    parts = []
    for t in ancestors_inclusive(tag):
        if t.name == "[document]":
            continue
        idx = 1
        if t.parent is not None:
            same = [s for s in t.parent.find_all(t.name, recursive=False)]
            idx = next((i + 1 for i, s in enumerate(same) if s is t), 1)
        parts.append(f"{t.name}:nth-of-type({idx})")
    return " > ".join(parts)


def infer_template_nodes(soup: BeautifulSoup, others: list[BeautifulSoup]) -> set[int]:
    """Cheap cross-page template detection: a node whose (tag path, text) also
    appears in every sibling page of the same site is template. Returns id()s of
    template nodes in `soup` (their descendants are implied)."""
    def keys(s: BeautifulSoup) -> dict:
        out = {}
        for t in s.find_all(True):
            if t.name in IGNORE:
                continue
            path = "/".join(a.name for a in ancestors_inclusive(t))
            txt = re.sub(r"\s+", " ", t.get_text(" ", strip=True))[:400]
            if txt:
                out.setdefault((path, txt), []).append(t)
        return out

    mine = keys(soup)
    other_keys = [set(keys(o).keys()) for o in others]
    ids: set[int] = set()
    for k, nodes in mine.items():
        if other_keys and all(k in ok for ok in other_keys):
            for n in nodes:
                ids.add(id(n))
    return ids


def in_template(tag: Tag, template_ids: set[int]) -> bool:
    return any(id(a) in template_ids for a in ancestors_inclusive(tag))


# --------------------------------------------------------------------------
# 5. Candidates, scoring, selection
# --------------------------------------------------------------------------
@dataclass
class RecordGroup:
    records: list[list[Tag]]                   # each record = 1..k sibling nodes
    record_sig: str
    parents: list[Tag]
    container: Optional[Tag] = None            # LCA of all record nodes
    record_probs: list[float] = field(default_factory=list)
    utility: float = 0.0
    features: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.records)

    def texts(self) -> list[str]:
        return [record_text(r) for r in self.records]


@dataclass
class MiningResult:
    best: Optional[RecordGroup]
    alternatives: list[RecordGroup]
    candidates: list[RecordGroup]


def _is_nav_or_chrome(tag: Tag) -> bool:
    """Checks whether the tag or any of its ancestors belongs to website chrome/navigation."""
    if not isinstance(tag, Tag):
        return False
    for t in ancestors_inclusive(tag):
        if not isinstance(t, Tag):
            continue
        if t.name in ("nav", "header", "footer", "aside"):
            return True
        role = (t.get("role") or "").lower()
        if role in ("navigation", "menubar", "menu"):
            return True
        cls_tokens = set(t.get("class", []))
        id_str = (t.get("id") or "").lower()
        chrome_names = {"menu", "nav", "navbar", "footer", "sidebar", "breadcrumb", "breadcrumbs"}
        if any(c.lower() in chrome_names for c in cls_tokens) or any(c in id_str for c in chrome_names):
            return True
    return False


def _generate(soup: BeautifulSoup, signer: Signer) -> list[RecordGroup]:
    cands: list[RecordGroup] = []
    for parent in soup.find_all(True):
        if parent.name in IGNORE or parent.name in TRANSPARENT or parent.name in ("html", "body"):
            continue
        if _is_nav_or_chrome(parent):
            continue
        kids = element_children(parent)
        if len(kids) < 1:
            continue
        sigs = [signer.sig(k) for k in kids]
        # k = 1 : group by signature (non-contiguous allowed)
        groups: dict[str, list[Tag]] = defaultdict(list)
        for k, s in zip(kids, sigs):
            groups[s].append(k)
        for s, members in groups.items():
            if len(members) >= 2:
                cands.append(RecordGroup([[m] for m in members], s, [parent]))
            elif len(members) == 1:
                toks = primary_tokens([members[0]])
                if toks and name_prob(toks[0]) >= 0.85:
                    cands.append(RecordGroup([[members[0]]], s, [parent]))
        # k = 2 : contiguous repeated heterogeneous pairs
        i = 0
        while i + 3 < len(kids):
            if sigs[i] != sigs[i + 1] and sigs[i] == sigs[i + 2] and sigs[i + 1] == sigs[i + 3]:
                j = i
                recs = []
                while j + 1 < len(kids) and sigs[j] == sigs[i] and sigs[j + 1] == sigs[i + 1]:
                    recs.append([kids[j], kids[j + 1]])
                    j += 2
                cands.append(RecordGroup(recs, f"{sigs[i]}|{sigs[i+1]}", [parent]))
                i = j
            else:
                i += 1
    return cands


# --------------------------------------------------------------------------
# Evidence fusion
# --------------------------------------------------------------------------
@dataclass
class FusionConfig:
    """Log-odds additive fusion.

        logit p_r = clip(logit P_name(r), +-name_clip)
                  + w_n * url_c * (1 - 2 H)          H = normalised URL-template entropy
                  - self_c * rho                     rho = share of links back to list page
                  + log(anchor_lr) * [person anchor]
                  - tpl_c * [record in template region]
        then clip to +-total_clip.

    Every term is bounded, so no single feature can veto a group: a record
    with a clipped-max name score (+2.94) survives the worst URL term (-1.39).
    w_n = 1 - 1/n_links is the reliability of an entropy estimated from n links.
    mode='product' reproduces the legacy multiplicative rule for A/B tests."""
    mode: str = "logit"
    name_clip: float = math.log(19)      # P_name confined to [0.05, 0.95]
    url_c: float = math.log(4)           # URL term in [-1.39, +1.39]
    self_c: float = math.log(20)         # pager evidence, [-3.0, 0]
    anchor_lr: float = 3.0               # '教授：' etc.
    tpl_c: float = math.log(1000)        # template region: near-hard evidence
    total_clip: float = math.log(999)    # p_r in [0.001, 0.999]


FUSION = FusionConfig()
ANCHOR_LR = FUSION.anchor_lr             # kept for backward compatibility


def _logit(p: float) -> float:
    p = min(max(p, 1e-9), 1 - 1e-9)
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _clip(x: float, c: float) -> float:
    return max(-c, min(c, x))


def url_evidence(hrefs: list[Optional[str]], page_url: Optional[str]) -> dict:
    linked = [h for h in hrefs if h]
    n = max(1, len(hrefs))
    out = dict(link_ratio=len(linked) / n, url_entropy=0.0, self_ref=0.0, n_links=len(linked))
    if out["link_ratio"] >= 0.5 and linked:
        all_t = url_templates(linked + ([page_url] if page_url else []))
        tmpls = all_t[:len(linked)]
        out["url_entropy"] = norm_entropy(tmpls)
        if page_url:
            # A CMS may use /info/<section>/<id>.htm for both roster and
            # profile pages. Template equality is not a self-reference;
            # compare actual destinations while ignoring in-page fragments.
            current = urlparse(page_url)._replace(fragment="").geturl()
            out["self_ref"] = sum(urlparse(h)._replace(fragment="").geturl() == current
                                  for h in linked) / len(linked)
    return out


def fuse(p_names: list[float], anchors: list[bool], in_tpl: list[bool], ev: dict,
         cfg: FusionConfig = FUSION) -> tuple[list[float], list[float]]:
    """Return (p_r, logit_r) per record."""
    H, rho, nl = ev["url_entropy"], ev["self_ref"], ev["n_links"]
    if cfg.mode == "product":
        m = (1 - H) * (1 - rho)
        ps = []
        for p, anc, t in zip(p_names, anchors, in_tpl):
            if anc and 0 < p < 1:
                o = p / (1 - p) * cfg.anchor_lr
                p = o / (1 + o)
            ps.append(0.0 if t else p * m)
        return ps, [_logit(p) for p in ps]
    w_n = (1 - 1 / nl) if (ev["link_ratio"] >= 0.5 and nl >= 2) else 0.0
    group_term = w_n * cfg.url_c * (1 - 2 * H) - cfg.self_c * rho
    zs = []
    for p, anc, t in zip(p_names, anchors, in_tpl):
        z = _clip(_logit(p), cfg.name_clip) + group_term
        z += math.log(cfg.anchor_lr) if anc else 0.0
        z -= cfg.tpl_c if t else 0.0
        zs.append(_clip(z, cfg.total_clip))
    return [_sigmoid(z) for z in zs], zs


def utility(ps: list[float]) -> float:
    return sum(2 * p - 1 for p in ps)


def utility_from_items(names: list[str], hrefs: list[Optional[str]], page_url: Optional[str],
                       name_fn: Callable[[str], float] = name_prob,
                       cfg: FusionConfig = FUSION) -> tuple[float, list[float]]:
    """Score arbitrary extracted items (used by the L3 validator, gate G8)."""
    pn = [max((name_fn(t) for t in _TOKEN_SPLIT.split(n) if t), default=0.0) for n in names]
    ps, _ = fuse(pn, [False] * len(names), [False] * len(names), url_evidence(hrefs, page_url), cfg)
    return utility(ps), ps


def _score(g: RecordGroup, page_url: Optional[str], template_ids: set[int],
           name_fn: Callable[[str], float], cfg: FusionConfig = FUSION) -> None:
    p_names = [max((name_fn(t) for t in primary_tokens(r)), default=0.0) for r in g.records]
    anchors = [isinstance(r[0], VirtualNode) and r[0].person_anchor and r[0].shape_ok
               for r in g.records]
    in_tpl = [bool(template_ids) and in_template(r[0], template_ids) for r in g.records]
    ev = url_evidence([first_href(r, page_url) for r in g.records], page_url)
    probs, logits = fuse(p_names, anchors, in_tpl, ev, cfg)
    g.record_probs = probs
    g.utility = utility(probs)
    g.features = dict(n=g.size, mean_name_p=sum(p_names) / g.size, link_ratio=ev["link_ratio"],
                      url_entropy=ev["url_entropy"], self_ref=ev["self_ref"],
                      template_frac=sum(in_tpl) / g.size, fusion=cfg.mode)


def _merge_same_sig(pos: list[RecordGroup], signer: Signer) -> list[RecordGroup]:
    """Merge accepted groups with identical record signature (grid tables,
    per-title sub-lists). Merge is restricted to groups whose parents share a
    signature, so a nav <ul><li><a> never fuses with a faculty <ul><li><a>
    (the nav group is already rejected by U<0 anyway)."""
    buckets: dict[tuple, list[RecordGroup]] = defaultdict(list)
    for g in pos:
        if isinstance(g.records[0][0], VirtualNode):   # text lists: same host parent
            key = (g.record_sig, "V", id(g.parents[0].parent))
        else:
            key = (g.record_sig, signer.sig(g.parents[0]))
        buckets[key].append(g)
    out = []
    for key, gs in buckets.items():
        sig = key[0]
        if len(gs) == 1:
            out.append(gs[0])
            continue
        recs, parents = [], []
        for g in gs:
            recs.extend(g.records)
            parents.extend(g.parents)
        out.append(RecordGroup(recs, sig, parents))
    return out


def _overlaps(a: RecordGroup, b: RecordGroup) -> bool:
    """True if any record node of a is an ancestor/descendant/equal of b's."""
    def _actual(n):
        return n.host if isinstance(n, VirtualNode) else n

    a_ids = {id(_actual(n)) for r in a.records for n in r}
    for r in b.records:
        for n in r:
            actual_n = _actual(n)
            if any(id(x) in a_ids for x in ancestors_inclusive(actual_n)):
                return True
    b_ids = {id(_actual(n)) for r in b.records for n in r}
    for r in a.records:
        for n in r:
            actual_n = _actual(n)
            if any(id(x) in b_ids for x in ancestors_inclusive(actual_n)):
                return True
    return False


def _triage_virtual(g: RecordGroup) -> None:
    """Text lists carry no structural proof that each span is a record, so
    records are triaged individually: accept p>=0.5 (Bayes boundary), review
    = name-shaped but p<0.5 (rare surnames go to L5, never silently dropped),
    noise = fails the name-shape grammar (digits, sentences, >4 Han chars)."""
    acc, rev, noise = [], [], []
    for i, (r, p) in enumerate(zip(g.records, g.record_probs)):
        (acc if p >= 0.5 else rev if r[0].shape_ok else noise).append(i)
    g.features.update(accept=acc, review=rev, noise=noise)


def mine_records(soup: BeautifulSoup, page_url: Optional[str] = None,
                 template_ids: Optional[set[int]] = None, depth: int = 4,
                 name_fn: Callable[[str], float] = name_prob,
                 virtual: bool = True, cfg: FusionConfig = FUSION) -> MiningResult:
    template_ids = template_ids or set()
    signer = Signer(depth=depth)
    cands = _generate(soup, signer)
    if virtual:
        from text_lists import generate_virtual_groups
        cands += generate_virtual_groups(soup)
    for g in cands:
        _score(g, page_url, template_ids, name_fn, cfg)
    positive = [g for g in cands if g.utility > 0]
    merged = _merge_same_sig(positive, signer)
    for g in merged:
        _score(g, page_url, template_ids, name_fn, cfg)
        g.container = lca([n for r in g.records for n in r])
    accepted = sorted((g for g in merged if g.utility > 0), key=lambda g: -g.utility)
    chosen: list[RecordGroup] = []
    for g in accepted:                          # greedy non-overlapping selection
        if all(not _overlaps(g, c) for c in chosen):
            chosen.append(g)
    for g in cands:
        if g.container is None:
            g.container = lca([n for r in g.records for n in r])
    for g in chosen:
        if isinstance(g.records[0][0], VirtualNode):
            _triage_virtual(g)
    return MiningResult(chosen[0] if chosen else None, chosen[1:], cands)
