"""
L3 wrapper synthesizer: LLM as a compiler, L2 as the oracle.

Flow per template cluster
    pages  -> split: train (shown to LLM, <=2) / holdout (never shown)
    L2     -> reference items on every page (independent of the LLM)
    prompt -> compressed DOM skeletons of train pages + negative evidence
    LLM    -> JSON with N wrapper variants (different anchoring strategies)
    gates  -> G0 lint | G1 coverage/precision vs L2 on holdout | G3 closure
              uniqueness | G7 page disjointness | G8 negative control
    accept -> WrapperRegistry (L1 cache) with golden snapshots for regression
    reject -> structured gate feedback -> next round (bounded) -> L5

Independence: the LLM never sees L2's answer. If it did, G1 agreement would
be copying, not evidence (differential testing needs independent programs).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional
from urllib.parse import urljoin

import lxml.html
from bs4 import BeautifulSoup

from iso_mining import (VirtualNode, css_path, element_children, first_href,
                        mine_records, name_prob, primary_tokens, url_template,
                        utility_from_items, _TOKEN_SPLIT)
from template_infer import infer_template_nodes_robust

# --------------------------------------------------------------------------
# Wrapper spec
# --------------------------------------------------------------------------
@dataclass
class WrapperSpec:
    record_xpath: str                   # absolute-from-anchor XPath 1.0 selecting records
    name_xpath: str = "."               # relative; string() of first hit = name text
    url_xpath: str = ".//a/@href"       # relative; first hit
    mode: str = "dom"                   # 'dom' | 'text_list' (record_xpath selects text hosts)
    strategy: str = ""
    rationale: str = ""


@dataclass
class GateReport:
    gate: str
    passed: bool
    detail: dict = field(default_factory=dict)


@dataclass
class Verdict:
    spec: WrapperSpec
    passed: bool
    reports: list[GateReport]
    outputs: dict = field(default_factory=dict)   # page_id -> items

    def feedback(self) -> dict:
        return {"strategy": self.spec.strategy, "record_xpath": self.spec.record_xpath,
                "failed": [{"gate": r.gate, **r.detail} for r in self.reports if not r.passed]}


# --------------------------------------------------------------------------
# DOM skeleton for the prompt (token economy)
# --------------------------------------------------------------------------
VOLATILE_CLASS = re.compile(
    r"^(odd|even|on|active|cur|current|selected|hover|first|last|clearfix|fl|fr|"
    r"left|right|show|hide|hidden|open|in)$|\d{3,}|^(css|sc|jsx|ng|v|el)-|^[a-f0-9]{6,}$"
    r"|^[a-z]{1,2}\d{1,2}$", re.I)                        # VSB 'i1', 'i2' ordinal classes
SKIP = {"script", "style", "noscript", "head", "meta", "link", "svg", "iframe"}


def stable_classes(el) -> list[str]:
    return [c for c in (el.get("class") or "").split() if not VOLATILE_CLASS.search(c)]


def skeleton(html: str, page_url: str, max_lines: int = 220, text_len: int = 16) -> str:
    """Indented tag lines with stable classes/ids, href templates and short text;
    runs of >3 structurally identical siblings are elided."""
    doc = lxml.html.document_fromstring(html)
    signer_cache: dict = {}

    def sig(el, d=3):
        key = (el, d)
        if key not in signer_cache:
            kids = [sig(c, d - 1) for c in el if isinstance(c.tag, str) and c.tag not in SKIP] if d else []
            signer_cache[key] = f"{el.tag}({','.join(kids)})"
        return signer_cache[key]

    lines: list[str] = []

    def line(el, depth):
        attrs = []
        if el.get("id") and not re.search(r"\d{3,}", el.get("id")):
            attrs.append(f"#{el.get('id')}")
        attrs += [f".{c}" for c in stable_classes(el)]
        if el.tag == "a" and el.get("href"):
            attrs.append(f"href~{url_template(urljoin(page_url, el.get('href')))}")
        own = (el.text or "").strip()
        txt = f' "{own[:text_len]}"' if own else ""
        lines.append("  " * depth + f"<{el.tag}{''.join(' ' + a for a in attrs)}>{txt}")

    def walk(el, depth):
        if len(lines) >= max_lines:
            return
        line(el, depth)
        kids = [c for c in el if isinstance(c.tag, str) and c.tag not in SKIP]
        i = 0
        while i < len(kids):
            j = i
            while j + 1 < len(kids) and sig(kids[j + 1]) == sig(kids[i]):
                j += 1
            run = kids[i:j + 1]
            if len(run) > 3:
                walk(run[0], depth + 1)
                walk(run[1], depth + 1)
                lines.append("  " * (depth + 1) + f"... ({len(run) - 3} more siblings, same structure)")
                walk(run[-1], depth + 1)
            else:
                for k in run:
                    walk(k, depth + 1)
            i = j + 1

    body = doc.find("body")
    walk(body if body is not None else doc, 0)
    return "\n".join(lines[:max_lines])


# --------------------------------------------------------------------------
# Prompt template
# --------------------------------------------------------------------------
PROMPT_TEMPLATE = """You are compiling a *wrapper* (XPath 1.0 program) that extracts the list of
full-time teaching staff from a family of structurally identical university
department pages ({cms_hint}). The wrapper will run unattended on thousands of
pages from this template cluster, so robustness matters more than cleverness.

## Target
One record per person listed as faculty. Each record yields
- name: the person's name text
- url:  the person's detail-page link if present

## Pages you may inspect (compressed DOM skeletons)
Notation: `<tag #id .class href~template> "text"`; volatile classes (odd/even,
active, hashed names) are already removed; `...(N more siblings, same structure)`
marks elided repetitions.

{train_blocks}

## Negative evidence - your wrapper MUST NOT select these
These texts are site template (navigation, leadership banners, footers,
pagers) identified by cross-page comparison:
{negative_texts}
A structurally similar NON-faculty page from the same site (the wrapper will
be run on it and must yield nothing person-like):
{negative_block}

## Robustness rules (violations are rejected automatically)
1. XPath 1.0 only (lxml). No absolute paths from /html or /body; start with `//`
   anchored on the nearest stable container.
2. Anchor on semantic, stable attributes: ids without long digit runs, class
   tokens via contains(concat(' ', normalize-space(@class), ' '), ' token ').
   Never use: odd, even, on, active, cur, first, last, hashed/numbered classes.
3. No positional predicates such as [3] or [position()=2] to pick containers;
   a header-row exclusion like [td] or [not(th)] is fine.
4. Never test for specific names, surnames, or Chinese content words in
   predicates; the program must not depend on who is listed.
5. One record = one person. If a table puts several people per row, select
   cells, not rows. If names sit in one text paragraph, set mode="text_list"
   and select the paragraph(s) as hosts.
6. Prefer the shortest selector that is still unambiguous; include pages whose
   rows are split across several sibling tables/lists with a union `|` only if
   needed.

## Previous attempts that failed validation
{feedback}

## Output
Return JSON only, no prose, no code fences:
{{"variants": [
  {{"strategy": "class-anchored" | "structure-anchored" | "link-pattern-anchored",
    "mode": "dom" | "text_list",
    "record_xpath": "...",
    "name_xpath": "relative XPath, default '.'",
    "url_xpath": "relative XPath to @href, default './/a/@href'",
    "rationale": "<= 30 words"}}
]}}
Give 3 variants using *different* anchoring strategies.
"""


def build_prompt(train: list[tuple[str, str]], negative_texts: list[str],
                 negative_page: Optional[tuple[str, str]], feedback: list[dict],
                 cms_hint: str = "unknown CMS") -> str:
    blocks = "\n\n".join(f"### Train page {i + 1}: {url}\n{skeleton(h, url)}"
                         for i, (url, h) in enumerate(train))
    neg_txt = "\n".join(f"- {t}" for t in negative_texts[:30]) or "- (none)"
    neg_blk = skeleton(negative_page[1], negative_page[0], max_lines=80) if negative_page else "(none)"
    fb = json.dumps(feedback, ensure_ascii=False, indent=1) if feedback else "(none)"
    return PROMPT_TEMPLATE.format(cms_hint=cms_hint, train_blocks=blocks,
                                  negative_texts=neg_txt, negative_block=neg_blk, feedback=fb)


def parse_llm_output(raw: str) -> list[WrapperSpec]:
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    data = json.loads(raw)
    out = []
    for v in data.get("variants", []):
        out.append(WrapperSpec(record_xpath=v["record_xpath"], name_xpath=v.get("name_xpath", "."),
                               url_xpath=v.get("url_xpath", ".//a/@href"), mode=v.get("mode", "dom"),
                               strategy=v.get("strategy", ""), rationale=v.get("rationale", "")))
    return out


# --------------------------------------------------------------------------
# Execution (lxml) and shared keys with L2 (bs4)
# --------------------------------------------------------------------------
def lxml_css_path(el) -> str:
    """Same format as iso_mining.css_path, so lxml and bs4 nodes are comparable."""
    chain = [el] + list(el.iterancestors())
    parts = []
    for node in reversed(chain):
        parent = node.getparent()
        idx = 1
        if parent is not None:
            same = [c for c in parent if c.tag == node.tag]
            idx = next(i + 1 for i, c in enumerate(same) if c is node)
        parts.append(f"{node.tag}:nth-of-type({idx})")
    return " > ".join(parts)


def best_name(text: str) -> str:
    toks = [t for t in _TOKEN_SPLIT.split(text or "") if t]
    return max(toks, key=name_prob) if toks else ""


def item_key(name_text: str, url: Optional[str]) -> tuple[str, str]:
    return (best_name(name_text), url or "")


@dataclass
class Page:
    pid: str
    url: str
    html: str
    soup: BeautifulSoup = None
    doc: object = None

    def __post_init__(self):
        self.soup = BeautifulSoup(self.html, "lxml")
        self.doc = lxml.html.document_fromstring(self.html)


def _first(res) -> Optional[str]:
    if isinstance(res, list):
        res = res[0] if res else None
    if res is None:
        return None
    if isinstance(res, str):
        return res.strip()
    return (res.text_content() if hasattr(res, "text_content") else str(res)).strip()


def execute(spec: WrapperSpec, page: Page) -> tuple[list[dict], list]:
    """Returns (items, record_elements). Raises on invalid XPath."""
    els = page.doc.xpath(spec.record_xpath)
    els = [e for e in els if hasattr(e, "tag")]
    items = []
    if spec.mode == "text_list":
        from text_lists import lex, text_stream
        for host_el in els:
            host = page.soup.select_one(lxml_css_path(host_el))
            if host is None:
                continue
            s, hrefs = text_stream(host)
            for v in lex(host, s, hrefs):
                if v.shape_ok:
                    items.append({"name": v.core, "url": urljoin(page.url, v.href) if v.href else None,
                                  "path": css_path(v)})
        return items, els
    for el in els:
        name = _first(el.xpath(spec.name_xpath)) if spec.name_xpath else _first(el)
        url = _first(el.xpath(spec.url_xpath)) if spec.url_xpath else None
        items.append({"name": name or "", "url": urljoin(page.url, url) if url else None,
                      "path": lxml_css_path(el)})
    return items, els


# --------------------------------------------------------------------------
# L2 reference
# --------------------------------------------------------------------------
@dataclass
class Reference:
    must: set                          # keys L2 accepts (p >= 0.5)
    optional: set                      # keys L2 keeps for review (p < 0.5)
    other_groups: list[list[str]]      # css paths of other accepted groups' records
    template_paths: list[str]


def l2_reference(page: Page, siblings: list[Page]) -> Reference:
    tpl = infer_template_nodes_robust(page.soup, [s.soup for s in siblings if s is not page])
    res = mine_records(page.soup, page_url=page.url, template_ids=tpl)
    must, optional, others = set(), set(), []
    if res.best is not None:
        g = res.best
        for r, p in zip(g.records, g.record_probs):
            v = r[0]
            if isinstance(v, VirtualNode):
                key = item_key(v.core, urljoin(page.url, v.href) if v.href else None)
                if not v.shape_ok:
                    continue
            else:
                key = item_key(" ".join(primary_tokens(r)), first_href(r, page.url))
            (must if p >= 0.5 else optional).add(key)
        for alt in res.alternatives:
            others.append([css_path(r[0]) for r in alt.records])
    tpaths = []
    for t in page.soup.find_all(True):
        if id(t) in tpl:
            tpaths.append(css_path(t))
    return Reference(must, optional, others, tpaths)


# --------------------------------------------------------------------------
# Gates
# --------------------------------------------------------------------------
_HAN = re.compile(r"[\u4e00-\u9fff]")


def gate_g0_lint(spec: WrapperSpec) -> GateReport:
    xp_all = " ".join([spec.record_xpath, spec.name_xpath or "", spec.url_xpath or ""])
    hard, soft = [], []
    if re.match(r"\s*/(html|body)\b", spec.record_xpath) or re.search(r"\|\s*/(html|body)\b", spec.record_xpath):
        hard.append("absolute path from /html or /body")
    if re.search(r"\[\s*\d+\s*\]|position\(\)\s*=\s*\d", spec.record_xpath):
        hard.append("positional predicate on record/container path")
    preds = re.findall(r"\[[^\]]*\]", xp_all)
    if any(_HAN.search(p) for p in preds):
        hard.append("content-dependent predicate (CJK literal)")
    classes = re.findall(r"'\s*([A-Za-z0-9_\-]+)\s*'", xp_all) + re.findall(r'@class\s*=\s*"([^"]+)"', xp_all)
    bad = [c for c in classes if VOLATILE_CLASS.search(c)]
    if bad:
        hard.append(f"volatile class tokens {bad}")
    if re.search(r"@class\s*=", xp_all):
        soft.append("exact @class equality; prefer token match")
    if re.search(r"@id\s*=\s*['\"][^'\"]*\d{3,}", xp_all):
        hard.append("id with long digit run")
    steps = len(re.findall(r"/+", spec.record_xpath))
    if steps > 8:
        soft.append(f"long path ({steps} steps)")
    return GateReport("G0_lint", not hard, {"hard": hard, "soft": soft})


def gate_g1(outputs: dict, refs: dict, holdout: list[str],
            tau_recall: float = 0.98, tau_precision: float = 0.98) -> GateReport:
    tp = fn = fp = 0
    missed, extra = [], []
    for pid in holdout:
        got = {item_key(i["name"], i["url"]) for i in outputs[pid]}
        ref = refs[pid]
        tp += len(got & ref.must)
        fn_set = ref.must - got
        fp_set = got - ref.must - ref.optional
        fn += len(fn_set)
        fp += len(fp_set)
        missed += [f"{pid}:{k[0]}" for k in list(fn_set)[:5]]
        extra += [f"{pid}:{k[0] or '<empty>'}" for k in list(fp_set)[:5]]
    recall = tp / (tp + fn) if tp + fn else 1.0
    precision = tp / (tp + fp) if tp + fp else 1.0
    ok = recall >= tau_recall and precision >= tau_precision and tp > 0
    return GateReport("G1_coverage", ok, {"recall": round(recall, 4), "precision": round(precision, 4),
                                          "missed_examples": missed[:8], "extra_examples": extra[:8]})


def _lca_path(paths: list[str]) -> str:
    if not paths:
        return ""
    split = [p.split(" > ") for p in paths]
    common = []
    for level in zip(*split):
        if all(x == level[0] for x in level):
            common.append(level[0])
        else:
            break
    return " > ".join(common)


def _inside(container: str, path: str) -> bool:
    return path == container or path.startswith(container + " > ")


def gate_g3(record_paths: dict, refs: dict, holdout: list[str]) -> GateReport:
    """Minimal closure container must be non-degenerate and must not swallow
    another accepted record group (e.g. a leadership list)."""
    problems = []
    for pid in holdout:
        paths = record_paths[pid]
        if not paths:
            continue
        C = _lca_path(paths)
        depth = C.count(">") + 1 if C else 0
        if depth <= 2:
            problems.append(f"{pid}: closure is the whole document ({C or 'root'})")
            continue
        own = set(paths)
        for grp in refs[pid].other_groups:
            inside = [p for p in grp if _inside(C, p) and p not in own]
            if inside:
                problems.append(f"{pid}: closure {C.split(' > ')[-1]} contains another "
                                f"accepted group ({len(inside)} records)")
    return GateReport("G3_closure", not problems, {"problems": problems[:6]})


def gate_g7(outputs: dict, holdout: list[str]) -> GateReport:
    """Paginated pages of one list must be disjoint (wrong paging params or a
    template block selected on every page both show up as repeats)."""
    seen, dup = {}, []
    for pid in holdout:
        for i in outputs[pid]:
            k = item_key(i["name"], i["url"])
            if k in seen and seen[k] != pid:
                dup.append(f"{k[0]} ({seen[k]} & {pid})")
            seen.setdefault(k, pid)
    return GateReport("G7_disjoint", not dup, {"duplicates": dup[:8]})


def gate_g8(spec: WrapperSpec, negatives: list[Page]) -> GateReport:
    """Wrapper + scorer must reject every negative page: either nothing is
    extracted or the extracted items have U <= 0."""
    bad = []
    for page in negatives:
        items, _ = execute(spec, page)
        if not items:
            continue
        U, _ = utility_from_items([i["name"] for i in items], [i["url"] for i in items], page.url)
        if U > 0:
            bad.append({"page": page.pid, "U": round(U, 2), "sample": [i["name"][:12] for i in items[:5]]})
    return GateReport("G8_negative", not bad, {"accepted_negatives": bad})


# --------------------------------------------------------------------------
# Validator
# --------------------------------------------------------------------------
class Validator:
    def __init__(self, pages: list[Page], negatives: list[Page], n_train: int = 2,
                 tau_recall: float = 0.98, tau_precision: float = 0.98):
        self.pages, self.negatives = pages, negatives
        self.train = pages[:n_train]
        self.holdout = [p.pid for p in pages[n_train:]] or [p.pid for p in pages]
        self.refs = {p.pid: l2_reference(p, pages) for p in pages}
        self.tau_r, self.tau_p = tau_recall, tau_precision

    def negative_texts(self) -> list[str]:
        p = self.train[0]
        tpl = infer_template_nodes_robust(p.soup, [q.soup for q in self.pages if q is not p])
        out = []
        for t in p.soup.find_all(True):
            if id(t) in tpl and not element_children(t):
                s = t.get_text(" ", strip=True)
                if s and s not in out:
                    out.append(s[:40])
        return out

    def validate(self, spec: WrapperSpec) -> Verdict:
        reports = [gate_g0_lint(spec)]
        outputs, paths = {}, {}
        try:
            for p in self.pages:
                items, els = execute(spec, p)
                outputs[p.pid] = items
                paths[p.pid] = [lxml_css_path(e) for e in els]
        except Exception as ex:                      # invalid XPath etc.
            reports.append(GateReport("execute", False, {"error": str(ex)[:200]}))
            return Verdict(spec, False, reports)
        reports.append(gate_g1(outputs, self.refs, self.holdout, self.tau_r, self.tau_p))
        reports.append(gate_g3(paths, self.refs, self.holdout))
        reports.append(gate_g7(outputs, self.holdout))
        reports.append(gate_g8(spec, self.negatives))
        return Verdict(spec, all(r.passed for r in reports), reports, outputs)


# --------------------------------------------------------------------------
# Synthesis loop and L1 registry
# --------------------------------------------------------------------------
@dataclass
class SynthResult:
    accepted: Optional[Verdict]
    rounds: list[list[Verdict]]
    escalate: bool
    n_version_agreement: Optional[bool] = None


def _lint_cost(v: Verdict) -> int:
    return len(v.reports[0].detail.get("soft", [])) * 10 + len(v.spec.record_xpath)


def synthesize(validator: Validator, llm: Callable[[str], str], cms_hint: str = "unknown CMS",
               max_rounds: int = 3, negative_page: Optional[Page] = None) -> SynthResult:
    feedback: list[dict] = []
    rounds = []
    neg = negative_page or (validator.negatives[0] if validator.negatives else None)
    for _ in range(max_rounds):
        prompt = build_prompt([(p.url, p.html) for p in validator.train], validator.negative_texts(),
                              (neg.url, neg.html) if neg else None, feedback, cms_hint)
        try:
            specs = parse_llm_output(llm(prompt))
        except (json.JSONDecodeError, KeyError) as ex:
            feedback = [{"parse_error": str(ex)[:200]}]
            rounds.append([])
            continue
        verdicts = [validator.validate(s) for s in specs]
        rounds.append(verdicts)
        passed = [v for v in verdicts if v.passed]
        if passed:
            agree = None
            if len(passed) >= 2:             # N-version: independent programs must agree
                sigs = [{pid: sorted(item_key(i["name"], i["url"]) for i in v.outputs[pid])
                         for pid in validator.holdout} for v in passed]
                agree = all(s == sigs[0] for s in sigs[1:])
            best = min(passed, key=_lint_cost)
            return SynthResult(best, rounds, escalate=(agree is False), n_version_agreement=agree)
        feedback = [v.feedback() for v in verdicts]
    return SynthResult(None, rounds, escalate=True)


class WrapperRegistry:
    """L1 cache: cluster_key -> accepted spec + golden snapshot for regression."""

    def __init__(self):
        self.entries: dict[str, dict] = {}

    def put(self, cluster_key: str, verdict: Verdict) -> None:
        prev = self.entries.get(cluster_key, {})
        self.entries[cluster_key] = {
            "version": prev.get("version", 0) + 1,
            "spec": asdict(verdict.spec),
            "golden": {pid: sorted(item_key(i["name"], i["url"]) for i in items)
                       for pid, items in verdict.outputs.items()},
        }

    def get(self, cluster_key: str) -> Optional[WrapperSpec]:
        e = self.entries.get(cluster_key)
        return WrapperSpec(**e["spec"]) if e else None

    def regress(self, cluster_key: str, candidate: WrapperSpec, pages: dict[str, Page]) -> list[str]:
        """A recompiled wrapper must reproduce every golden page exactly."""
        golden = self.entries[cluster_key]["golden"]
        diffs = []
        for pid, keys in golden.items():
            if pid not in pages:
                continue
            items, _ = execute(candidate, pages[pid])
            got = sorted(item_key(i["name"], i["url"]) for i in items)
            if [list(k) for k in got] != [list(k) for k in keys]:
                diffs.append(pid)
        return diffs

    def dumps(self) -> str:
        return json.dumps(self.entries, ensure_ascii=False, indent=1)
