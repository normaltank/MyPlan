"""
Page-level email attribution as weighted bipartite matching.

W[i, j] = logit P(email mention j belongs to person i), built as a sum of
log-likelihood-ratio features (naive-Bayes / logistic form):

    x_excl_p  = log 1/(1 + #other persons inside LCA(p, e))   uniform-share prior
    x_excl_e  = log 1/(1 + #other addresses inside LCA(p, e))
    x_dist    = -log(1 + tree distance(p, e))
    x_conv    = log LR_conv(e | p, domain scheme)             naming convention
    x_df_site = -log df_site(e)                                uniform-share over pages
    x_df_glob = -log(1 + df_global(e) - df_site(e))            cross-site reuse
    x_label   = 1[e is preceded by an e-mail label]
    x_record  = 1[e lies inside p's own record container from L2]

    logit = b + w . x      (defaults below; fit on audited pairs in production)

Hard constraints (edge removed):
    e inside template region                     -> office/footer mailbox
    role label right before e ('办公室邮箱' ...)  -> functional mailbox
    df_site(e) > 1 and LCA(p, e) holds others    -> shared, non-exclusive

Solvers
    match_1to1  : scipy linear_sum_assignment with 'unassigned' dummy columns,
                  maximises sum of logits over positive-evidence pairs.
    match_flow  : explicit min-cost flow (networkx) with capacities:
                  person may own up to k emails (slot k costs log k),
                  a single mention inside a multi-person closure may be
                  shared (slot k costs log k); each (e, p) pair used once.
Decision boundary everywhere is logit > 0 (more likely than not).
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from scipy.optimize import linear_sum_assignment

from iso_mining import ancestors_inclusive, in_template

# --------------------------------------------------------------------------
# Data structures
# --------------------------------------------------------------------------
@dataclass
class PersonMention:
    pid: str
    name: str
    node: Tag                                   # node holding the name text
    record_nodes: list[Tag] = field(default_factory=list)   # from L2, optional
    vnode: Optional[object] = None              # VirtualNode for text-list persons


@dataclass
class EmailMention:
    mid: int
    address: str
    node: Tag                                   # element containing the address
    label_before: bool = False
    role_label: bool = False
    in_template: bool = False
    note: str = ""
    host: Optional[Tag] = None                  # set when bound to a text stream span
    start: int = -1
    end: int = -1


@dataclass
class Match:
    pid: str
    name: str
    address: str
    mid: int
    confidence: float
    logit: float
    margin: float
    flags: list[str] = field(default_factory=list)


@dataclass
class MatchResult:
    matches: list[Match]
    unassigned: list[tuple[EmailMention, str]]  # (mention, reason)
    W: np.ndarray                               # persons x emails logits (-inf = forbidden)


# --------------------------------------------------------------------------
# Email mention extraction (DOM-boundary aware, de-obfuscation)
# --------------------------------------------------------------------------
_EMAIL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%+\-]*@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
_OBFUS = [
    (re.compile(r"\s*[\[\(（【]\s*(?:at|AT|艾特)\s*[\]\)）】]\s*"), "@"),
    (re.compile(r"＠"), "@"),
    (re.compile(r"\s*[\[\(（【]\s*(?:dot|DOT|点)\s*[\]\)）】]\s*"), "."),
]
_LABEL = re.compile(r"(e-?mail|email|邮箱|信箱|电子邮件|电邮|mail)\s*[:：]?\s*$", re.I)
_ROLE = re.compile(r"(办公室|院办|招生|咨询|投稿|编辑部|值班|教务|学工)[\u4e00-\u9fff]{0,4}"
                   r"(e-?mail|邮箱|信箱|电邮)\s*[:：]?\s*$", re.I)


def _deobfuscate(s: str) -> str:
    for pat, rep in _OBFUS:
        s = pat.sub(rep, s)
    return s


def decode_cfemail(hexstr: str) -> str:
    key = int(hexstr[:2], 16)
    return "".join(chr(int(hexstr[i:i + 2], 16) ^ key) for i in range(2, len(hexstr), 2))


def extract_email_mentions(soup: BeautifulSoup, template_ids: Optional[set[int]] = None
                           ) -> list[EmailMention]:
    template_ids = template_ids or set()
    out: list[EmailMention] = []
    seen: set[tuple[int, str]] = set()

    def add(addr: str, node: Tag, before: str):
        addr = addr.strip().strip(".").lower()
        key = (id(node), addr)
        if key in seen:
            return
        seen.add(key)
        tail = before[-14:]
        out.append(EmailMention(len(out), addr, node,
                                label_before=bool(_LABEL.search(tail)),
                                role_label=bool(_ROLE.search(tail)),
                                in_template=in_template(node, template_ids)))

    def preceding_text(ts: NavigableString, idx: int) -> str:
        txt = str(ts)[:idx]
        if len(txt.strip()) < 3:                  # label may sit in a previous node
            prev = ts.find_previous(string=True)
            hops = 0
            while prev is not None and hops < 3 and not str(prev).strip():
                prev = prev.find_previous(string=True)
                hops += 1
            if prev is not None:
                txt = str(prev) + txt
        return txt

    for ts in soup.find_all(string=True):         # each text node separately
        if isinstance(ts, Comment) or ts.parent is None or ts.parent.name in ("script", "style"):
            continue
        raw = _deobfuscate(str(ts))
        for m in _EMAIL.finditer(raw):
            add(m.group(0), ts.parent, preceding_text(ts, m.start()))
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.lower().startswith("mailto:"):
            add(h[7:].split("?")[0], a, a.find_previous(string=True) or "")
    for el in soup.find_all(attrs={"data-cfemail": True}):
        try:
            add(decode_cfemail(el["data-cfemail"]), el, el.find_previous(string=True) or "")
        except ValueError:
            pass
    return out


# --------------------------------------------------------------------------
# Naming-convention model per domain
# --------------------------------------------------------------------------
FREE_MAIL = {"163.com", "126.com", "yeah.net", "qq.com", "foxmail.com", "gmail.com",
             "sina.com", "sina.cn", "sohu.com", "hotmail.com", "outlook.com",
             "live.com", "yahoo.com", "aliyun.com", "139.com", "189.cn", "icloud.com"}


def _default_to_pinyin(name: str) -> Optional[list[str]]:
    try:
        from pypinyin import lazy_pinyin
        return [s.lower() for s in lazy_pinyin(name)]
    except ImportError:
        return None


def pinyin_variants(syl: list[str]) -> set[str]:
    if not syl:
        return set()
    sur, given = syl[0], syl[1:]
    g = "".join(given)
    gi = "".join(x[0] for x in given)
    # The trailing <first-given-initial><surname> variant (e.g. 王婉婷 -> 'wwang')
    # is a widespread university convention; without it genuine mailboxes such as
    # 'wwang2026@hunnu.edu.cn' were rejected by name-affinity checks.
    return {sur, sur + g, sur + gi, "".join(x[0] for x in syl), g + sur, gi + sur,
            sur + (g[:1] if g else ""), sur[:1] + g, (gi[:1] + sur) if gi else sur}



class DomainConventionModel:
    """Learns per-domain local-part scheme: P(pinyin | D), numeric length dist."""

    def __init__(self, to_pinyin: Callable[[str], Optional[list[str]]] = _default_to_pinyin,
                 K: float = 20.0, eps: float = 0.1):
        self.to_pinyin, self.K, self.eps = to_pinyin, K, eps
        self.pinyin_hits: Counter = Counter()
        self.pairs: Counter = Counter()
        self.numeric_len: dict[str, Counter] = defaultdict(Counter)
        self.total: Counter = Counter()

    @staticmethod
    def split(addr: str) -> tuple[str, str]:
        local, _, dom = addr.lower().partition("@")
        return local, dom

    def observe(self, addresses: list[str]) -> None:
        """Unsupervised: numeric-ID length distribution per domain."""
        for a in set(addresses):
            local, dom = self.split(a)
            self.total[dom] += 1
            if local.isdigit():
                self.numeric_len[dom][len(local)] += 1

    def fit(self, pairs: list[tuple[str, str]]) -> None:
        """Supervised/self-training: (address, name) from high-confidence matches."""
        for addr, name in pairs:
            local, dom = self.split(addr)
            self.pairs[dom] += 1
            if self._pinyin_match(local, name):
                self.pinyin_hits[dom] += 1

    def p_pinyin(self, dom: str) -> float:
        if dom in FREE_MAIL:
            return 0.2
        return (self.pinyin_hits[dom] + 1) / (self.pairs[dom] + 2)   # Beta(1,1)

    def numeric_share(self, dom: str) -> float:
        return sum(self.numeric_len[dom].values()) / self.total[dom] if self.total[dom] else 0.0

    def p_len(self, dom: str, L: int) -> float:
        c = self.numeric_len[dom]
        n = sum(c.values())
        return (c[L] + 0.1) / (n + 0.1 * 12) if n else 0.1

    def _pinyin_match(self, local: str, name: str) -> Optional[bool]:
        if re.search(r"[\u4e00-\u9fff]", name):
            syl = self.to_pinyin(name)
        else:                                      # 'John A. Smith' -> ['smith', 'john', 'a']
            words = [w.lower() for w in re.split(r"[\s.\-]+", name) if w]
            syl = words[-1:] + words[:-1]
        if not syl:
            return None
        clean = re.sub(r"[\d._\-]+", "", local)
        if not clean:
            return False
        v = pinyin_variants(syl)
        return clean in v or any(len(x) >= 4 and clean.startswith(x) for x in v)

    def log_lr(self, addr: str, name: str) -> float:
        local, dom = self.split(addr)
        if local.isdigit():
            if self.numeric_share(dom) > 0.5 and self.p_len(dom, len(local)) < 0.05:
                return math.log(0.5)
            return 0.0                               # numeric IDs carry no name info
        match = self._pinyin_match(local, name)
        if match is None:
            return 0.0
        s = self.p_pinyin(dom)
        lr = s * self.K + (1 - s) if match else s * self.eps + (1 - s)
        return math.log(lr)

    # ---- glued digits: '0510-85911234' + '8843120002@...' ---------------
    def split_glued_numeric(self, addr: str) -> tuple[str, Optional[str], bool]:
        """Return (clean_address, phone_prefix_or_None, ambiguous)."""
        local, dom = self.split(addr)
        digits = local.split("-")[-1]
        if not digits.isdigit() or self.numeric_share(dom) <= 0.5:
            return addr, None, False

        def p_phone(s: str) -> float:
            if not s:
                return 0.5
            if len(s) in (7, 8):
                return 0.9
            if len(s) == 11 and re.match(r"1[3-9]", s):
                return 0.9
            if len(s) in (10, 11, 12) and s.startswith("0"):
                return 0.8
            return 0.01

        scored = sorted(((p_phone(digits[:i]) * self.p_len(dom, len(digits) - i), i)
                         for i in range(0, len(digits) - 3)), reverse=True)
        (b, i), second = scored[0], (scored[1][0] if len(scored) > 1 else 0.0)
        phone = digits[:i] or None
        return f"{digits[i:]}@{dom}", phone, (second > 0 and b / second < 2.0)


# --------------------------------------------------------------------------
# Container-key chains: DOM ancestors, extended by text topology keys
# --------------------------------------------------------------------------
class ChainIndex:
    """chain(x) = root->leaf list of hashable container keys.
    DOM node      : [id(a) for a in DOM ancestors]
    text span     : DOM chain of host + [('sec',..), ('hard',..), ('soft',..)]
    LCA(p, e) = longest common prefix; 'x inside C' = C's key in chain(x).
    Nothing else in the matcher needs to know whether a mention is DOM or text."""

    def __init__(self):
        self._topo: dict[int, object] = {}

    def topology(self, host: Tag):
        from text_lists import HostTopology
        t = self._topo.get(id(host))
        if t is None:
            t = self._topo[id(host)] = HostTopology(host)
        return t

    @staticmethod
    def dom(node: Tag) -> list:
        return [id(a) for a in ancestors_inclusive(node)]

    def person(self, p: "PersonMention") -> list:
        if p.vnode is not None:
            v = p.vnode
            return self.dom(v.host) + self.topology(v.host).path(v.start, v.end)
        return self.dom(p.node)

    def email(self, e: "EmailMention") -> list:
        if e.host is not None:
            return self.dom(e.host) + self.topology(e.host).path(e.start, e.end)
        return self.dom(e.node)


def bind_text_spans(emails: list[EmailMention], persons: list[PersonMention],
                    index: ChainIndex) -> None:
    """Attach stream offsets to e-mail mentions that live inside the host of a
    virtual person. Repeated addresses in one host are bound in reading order."""
    hosts = {id(p.vnode.host): p.vnode.host for p in persons if p.vnode is not None}
    for hid, host in hosts.items():
        topo = index.topology(host)
        pending: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for a, b, addr in topo.emails:
            pending[addr].append((a, b))
        for e in emails:
            if e.host is not None or e.in_template:
                continue
            if hid not in {id(x) for x in ancestors_inclusive(e.node)} and e.node is not host:
                continue
            if pending.get(e.address):
                e.host = host
                e.start, e.end = pending[e.address].pop(0)


def _adjacent_owner(p_chain: list, e_chain: list, p: PersonMention, e: EmailMention,
                    persons: list[PersonMention]) -> float:
    """Text analogue of DOM distance: 1 if p is the nearest person preceding e
    inside the same section (convention '姓名 邮箱'), else 0."""
    if p.vnode is None or e.host is None or p.vnode.host is not e.host:
        return 0.0
    sec_p = next((k for k in p_chain if isinstance(k, tuple) and k[0] == "sec"), None)
    sec_e = next((k for k in e_chain if isinstance(k, tuple) and k[0] == "sec"), None)
    if sec_p is None or sec_p != sec_e or p.vnode.start > e.start:
        return 0.0
    for q in persons:
        if q is p or q.vnode is None or q.vnode.host is not e.host:
            continue
        if p.vnode.start < q.vnode.start < e.start:
            return 0.0
    return 1.0


# --------------------------------------------------------------------------
# Weight matrix
# --------------------------------------------------------------------------
FEATURES = ["excl_p", "excl_e", "dist", "conv", "df_site", "df_glob", "label", "record", "adj"]
DEFAULT_W = np.array([2.0, 1.0, 0.5, 1.0, 1.5, 0.5, 1.0, 1.5, 1.0])
DEFAULT_B = 0.5


def _depth(t: Tag) -> int:
    return len(ancestors_inclusive(t))


def _lcp(x: list, y: list) -> int:
    n = 0
    for u, v in zip(x, y):
        if u != v:
            break
        n += 1
    return n


def build_weight_matrix(persons: list[PersonMention], emails: list[EmailMention],
                        conv: DomainConventionModel,
                        df_site: dict[str, int], df_global: dict[str, int],
                        w: np.ndarray = DEFAULT_W, b: float = DEFAULT_B,
                        index: Optional[ChainIndex] = None
                        ) -> tuple[np.ndarray, np.ndarray, dict[tuple[int, int], dict]]:
    index = index or ChainIndex()
    bind_text_spans(emails, persons, index)
    P, E = len(persons), len(emails)
    W = np.full((P, E), -np.inf)
    reason = np.empty((P, E), dtype=object)
    feats: dict[tuple[int, int], dict] = {}
    ch_p = [index.person(p) for p in persons]
    ch_e = [index.email(e) for e in emails]
    set_p = [set(c) for c in ch_p]
    set_e = [set(c) for c in ch_e]
    # inverted index container -> #persons / addresses inside it; replaces the
    # O(P+E) scan per (i, j) pair (the matrix build was O(P*E*(P+E)))
    n_p_in: Counter = Counter(k for c in set_p for k in c)
    addr_in: dict = defaultdict(set)
    for e_, c in zip(emails, set_e):
        for k in c:
            addr_in[k].add(e_.address)
    # record container keys: DOM record nodes, or the person's own soft cell
    rec_keys = []
    for p, c in zip(persons, ch_p):
        if p.vnode is not None:
            rec_keys.append({k for k in c if isinstance(k, tuple) and k[0] == "item"})
        else:
            rec_keys.append({id(n) for n in p.record_nodes})

    for j, e in enumerate(emails):
        dfs = max(1, df_site.get(e.address, 1))
        dfg = max(dfs, df_global.get(e.address, dfs))
        for i, p in enumerate(persons):
            if e.in_template:
                reason[i, j] = "template"
                continue
            if e.role_label:
                reason[i, j] = "role_label"
                continue
            L = _lcp(ch_p[i], ch_e[j])
            if L == 0:
                reason[i, j] = "no_common_container"
                continue
            C = ch_p[i][L - 1]
            others_p = n_p_in[C] - 1                     # C is on p's own chain
            others_e = len(addr_in[C]) - (e.address in addr_in[C])
            if dfs > 1 and others_p > 0:
                reason[i, j] = "shared_df_nonexclusive"
                continue
            dist = (len(ch_p[i]) - L) + (len(ch_e[j]) - L)
            x = np.array([
                -math.log1p(others_p),
                -math.log1p(others_e),
                -math.log1p(dist),
                conv.log_lr(e.address, p.name),
                -math.log(dfs),
                -math.log1p(dfg - dfs),
                float(e.label_before),
                float(bool(rec_keys[i] & set_e[j])),
                _adjacent_owner(ch_p[i], ch_e[j], p, e, persons),
            ])
            W[i, j] = b + float(w @ x)
            feats[(i, j)] = dict(zip(FEATURES, x.round(3))) | {"others_p": others_p}
            reason[i, j] = "ok"
    return W, reason, feats


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _margins(W: np.ndarray) -> np.ndarray:
    """Per email: best logit minus runner-up (inf if a single candidate)."""
    m = np.full(W.shape[1], np.inf)
    for j in range(W.shape[1]):
        col = np.sort(W[:, j][np.isfinite(W[:, j])])[::-1]
        if len(col) >= 2:
            m[j] = col[0] - col[1]
    return m


def _unassigned_reason(reason: np.ndarray, j: int) -> str:
    rs = {r for r in reason[:, j] if r}
    for r in ("template", "role_label", "shared_df_nonexclusive"):
        if rs == {r}:
            return r
    return "insufficient_evidence" if "ok" in rs else (next(iter(rs)) if rs else "no_person")


def _finalise(pairs: list[tuple[int, int]], persons, emails, W, reason) -> MatchResult:
    margins = _margins(W)
    per_person = Counter(i for i, _ in pairs)
    per_addr: dict[str, set] = defaultdict(set)
    for i, j in pairs:
        per_addr[emails[j].address].add(persons[i].pid)
    matches = []
    for i, j in pairs:
        flags = []
        if per_person[i] > 1:
            flags.append("multi_email_person")
        if len(per_addr[emails[j].address]) > 1:
            flags.append("shared_email")
        if margins[j] < math.log(2):
            flags.append("ambiguous")
        matches.append(Match(persons[i].pid, persons[i].name, emails[j].address, emails[j].mid,
                             round(_sigmoid(W[i, j]), 4), round(float(W[i, j]), 3),
                             float(margins[j]), flags))
    used = {j for _, j in pairs}
    unassigned = [(emails[j], _unassigned_reason(reason, j)) for j in range(len(emails)) if j not in used]
    return MatchResult(matches, unassigned, W)


# --------------------------------------------------------------------------
# Solver 1: strict 1:1 with linear_sum_assignment
# --------------------------------------------------------------------------
BIG = 1e9


def match_1to1(persons, emails, W: np.ndarray, reason: np.ndarray) -> MatchResult:
    P, E = W.shape
    if P == 0 or E == 0:
        return _finalise([], persons, emails, W, reason)
    # rows = emails; cols = persons + E dummy 'unassigned' columns (cost 0)
    cost = np.full((E, P + E), BIG)
    benefit = W.T                                          # E x P
    mask = np.isfinite(benefit) & (benefit > 0)            # only positive evidence
    cost[:, :P] = np.where(mask, -benefit, BIG)
    cost[np.arange(E), P + np.arange(E)] = 0.0
    r, c = linear_sum_assignment(cost)
    pairs = [(cj, rj) for rj, cj in zip(r, c) if cj < P and cost[rj, cj] < BIG / 2]
    return _finalise(pairs, persons, emails, W, reason)


# --------------------------------------------------------------------------
# Solver 2: relaxed capacities via explicit min-cost flow
# --------------------------------------------------------------------------
def match_flow(persons, emails, W: np.ndarray, reason: np.ndarray,
               max_emails_per_person: int = 3, max_share: int = 3,
               scale: int = 1000) -> MatchResult:
    """person slot k and email slot k cost log(k): the k-th owner/address must
    carry k-fold stronger evidence; each (e, p) edge has capacity 1."""
    import networkx as nx

    P, E = W.shape
    G = nx.DiGraph()
    supply = 0
    for j in range(E):
        finite = np.isfinite(W[:, j]) & (W[:, j] > 0)
        # a single mention may be shared only if its closure holds >1 candidates
        cap = min(max_share, int(finite.sum())) if finite.sum() > 1 else 1
        for k in range(1, cap + 1):
            G.add_edge("S", ("es", j, k), capacity=1, weight=int(scale * math.log(k)))
            G.add_edge(("es", j, k), ("e", j), capacity=1, weight=0)
        supply += cap
        for i in np.where(finite)[0]:
            G.add_edge(("e", j), ("p", int(i)), capacity=1, weight=-int(scale * W[i, j]))
    for i in range(P):
        for k in range(1, max_emails_per_person + 1):
            G.add_edge(("p", i), ("ps", i, k), capacity=1, weight=int(scale * math.log(k)))
            G.add_edge(("ps", i, k), "T", capacity=1, weight=0)
    G.add_edge("S", "T", capacity=supply, weight=0)             # 'leave unassigned' bypass
    G.nodes["S"]["demand"] = -supply
    G.nodes["T"]["demand"] = supply
    flow = nx.min_cost_flow(G)
    pairs = [(v[1], u[1]) for u, outs in flow.items() if isinstance(u, tuple) and u[0] == "e"
             for v, f in outs.items() if f > 0 and isinstance(v, tuple) and v[0] == "p"]
    return _finalise(pairs, persons, emails, W, reason)


# --------------------------------------------------------------------------
# Convenience: persons from L2 records
# --------------------------------------------------------------------------
def persons_from_group(group, name_fn) -> list[PersonMention]:
    from iso_mining import VirtualNode, primary_tokens
    out = []
    for idx, rec in enumerate(group.records):
        if isinstance(rec[0], VirtualNode):
            v = rec[0]
            if idx in group.features.get("noise", []):
                continue                                  # not a person
            # exclusivity for text-list persons comes from ChainIndex text keys
            out.append(PersonMention(f"p{idx}", v.core, v.host, record_nodes=[], vnode=v))
            continue
        toks = primary_tokens(rec)
        if not toks:
            continue
        name = max(toks, key=name_fn)
        node = None
        for n in rec:
            for s in n.find_all(string=True) if n.find_all(string=True) else []:
                if name in s:
                    node = s.parent
                    break
            if node is not None:
                break
        out.append(PersonMention(f"p{idx}", name, node or rec[0], record_nodes=list(rec)))
    return out
