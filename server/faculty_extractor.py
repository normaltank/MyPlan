"""
Faculty Extractor: Deep Module for University Faculty Directory & Profile Harvesting.
Zero keyword blacklists. Based on:
1. DOM Isomorphic Subtree Clustering (Tree signature & structural clustering)
2. Layout-Aware Tokenization (Preserves CSS Block Formatting Context)
3. Minimal Enclosing Content Container (MECC Scoping)
"""

import functools
import re
from dataclasses import dataclass
from typing import List, Dict, Optional, Set, Tuple, Any
from collections import Counter
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag, NavigableString, Comment

import logging
import os
import sys

log = logging.getLogger("faculty_extractor")

# The algorithm layer uses flat imports ('from iso_mining import ...'), so it
# must be imported under exactly ONE module name. Importing it as
# 'Opus.iso_mining' AND as 'iso_mining' creates two VirtualNode classes;
# isinstance() then fails and mine_records crashes on every text-list page.
_OPUS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Opus")
if _OPUS_DIR not in sys.path:
    sys.path.insert(0, _OPUS_DIR)
try:
    from iso_mining import (VirtualNode, first_href, mine_records, name_prob, primary_tokens,
                            rare_surname_name, strip_invisible)
    from email_matching import DomainConventionModel
    from text_lists import email_spans, _AT as _AT_PATTERN
    _AT_RE = re.compile(_AT_PATTERN)
    L2_AVAILABLE = True
except ImportError as _ex:                          # never silently: log it
    log.error("L2 algorithm layer unavailable: %s", _ex)
    mine_records = DomainConventionModel = VirtualNode = first_href = primary_tokens = email_spans = None
    rare_surname_name = name_prob = None
    L2_AVAILABLE = False

    # _clean_name is called on every extraction path, including this degraded
    # one, so it must never depend on a name that only the happy path binds.
    _INVISIBLE_FALLBACK = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff\u00ad]")

    def strip_invisible(t: str) -> str:
        return _INVISIBLE_FALLBACK.sub("", t or "")

try:
    from server.runtime_l1 import L1RuntimeExecutor, SQLiteWrapperRegistry, cluster_key_for
    from server.agent_queue import AgentTaskQueue
    L1_AVAILABLE = True
except ImportError:
    try:
        from runtime_l1 import L1RuntimeExecutor, SQLiteWrapperRegistry, cluster_key_for
        from agent_queue import AgentTaskQueue
        L1_AVAILABLE = True
    except ImportError as _ex:
        log.warning("L1 runtime layer unavailable: %s", _ex)
        L1RuntimeExecutor = SQLiteWrapperRegistry = cluster_key_for = AgentTaskQueue = None
        L1_AVAILABLE = False

try:
    from server.titles import TITLE_KEYWORDS, resolve_title
except ImportError:
    from titles import TITLE_KEYWORDS, resolve_title

# '李星教授介绍': a 2-4 character name, a title keyword, then an introduction suffix.
# Below this prior, text repeated on most cards is template ('查看详情'), not a name.
TEMPLATE_NAME_PRIOR = 0.7
_NAME_SHAPED = re.compile(r"[\u4e00-\u9fff]{2,4}|[\u4e00-\u9fff]{1,8}(?:[·•][\u4e00-\u9fff]{1,8}){1,3}")
_DEGREE_AFTER_NAME = re.compile(r"^([\u4e00-\u9fff]{2,4}?)(?:博士|硕士)$")
_INTRODUCED_NAME = re.compile(
    r"^([\u4e00-\u9fff]{2,4}?)(" + "|".join(map(re.escape, TITLE_KEYWORDS))
    + r")(?:个人简介|简介|介绍|个人主页|主页)$")

try:
    from server.detail_mecc import DetailMECCExtractor, ProfileDetail, LayoutAwareTokenizer
    DETAIL_MECC_AVAILABLE = True
except ImportError:
    try:
        from detail_mecc import DetailMECCExtractor, ProfileDetail, LayoutAwareTokenizer
        DETAIL_MECC_AVAILABLE = True
    except ImportError as _ex:
        log.warning("Detail MECC extractor unavailable: %s", _ex)
        DetailMECCExtractor = ProfileDetail = LayoutAwareTokenizer = None
        DETAIL_MECC_AVAILABLE = False

_DEFAULT_L1_EXECUTOR = None

def get_default_l1_executor():
    global _DEFAULT_L1_EXECUTOR
    if _DEFAULT_L1_EXECUTOR is None and L1_AVAILABLE:
        try:
            reg = SQLiteWrapperRegistry()
            q = AgentTaskQueue()
            _DEFAULT_L1_EXECUTOR = L1RuntimeExecutor(registry=reg, queue=q)
        except Exception as ex:
            log.warning("Failed to create default L1 executor: %s", ex)
    return _DEFAULT_L1_EXECUTOR

# Common structural containers that are strictly external chrome/navigation
PRUNE_TAGS = ['script', 'style', 'noscript', 'svg', 'iframe', 'header', 'footer', 'nav']
PRUNE_CLASSES = [
    'header', 'footer', 'nav', 'menu', 'nav-menu', 'menu-item', 'pagination', 'page-nav', 
    'wp_paging', 'sidebar', 'sub-nav', 'bottom', 'copyright',
    'col_path', 'col_menu', 'breadcrumb', 'breadcrumbs', 'left_nav', 'nav_left'
]


FREE_MAIL_DOMAINS = {
    "163.com", "126.com", "yeah.net", "qq.com", "foxmail.com", "gmail.com",
    "sina.com", "sina.cn", "sohu.com", "hotmail.com", "outlook.com",
    "live.com", "yahoo.com", "aliyun.com", "139.com", "189.cn", "icloud.com"
}

def is_personal_email_for_scholar(email: str, scholar_name: str = "") -> bool:
    """
    Determines if an email belongs to a scholar using information-theoretic
    and naming-convention affinity (zero keyword blacklists).
    """
    if not email or '@' not in email:
        return False
    local_part, _, dom = email.lower().partition('@')
    clean_local = re.sub(r'[-_.]', '', local_part)
    if clean_local.isdigit():
        return True
    if scholar_name:
        if DomainConventionModel is not None:
            try:
                dcm = DomainConventionModel()
                if dcm._pinyin_match(clean_local, scholar_name) or dcm._pinyin_match(local_part, scholar_name):
                    return True
                for syl in dcm.readings(scholar_name):
                    initials = "".join(s[0] for s in syl if s).lower()
                    if len(initials) >= 2 and clean_local.startswith(initials):
                        return True
                    full_py = "".join(s for s in syl if s).lower()
                    if len(full_py) >= 3 and clean_local.startswith(full_py):
                        return True
                return False
            except Exception:
                pass
        return False
    if dom in FREE_MAIL_DOMAINS:
        return True
    return True

_AFFINITY_MODEL = None


@functools.lru_cache(maxsize=65536)
def email_name_affinity(email: str, scholar_name: str) -> bool:
    """The local part spells the person's name (full pinyin or initials).

    This is supporting evidence only. Many teachers use a personal address
    (163.com, qq.com, a nickname) that spells nothing; ownership comes from
    where the address appears, see ``own_email``.
    """
    global _AFFINITY_MODEL
    if not email or '@' not in email or not scholar_name or DomainConventionModel is None:
        return False
    local_part = email.lower().partition('@')[0]
    clean_local = re.sub(r'[-_.]', '', local_part)
    try:
        if _AFFINITY_MODEL is None:
            _AFFINITY_MODEL = DomainConventionModel()
        dcm = _AFFINITY_MODEL
        if dcm._pinyin_match(clean_local, scholar_name) or dcm._pinyin_match(local_part, scholar_name):
            return True
        for syl in dcm.readings(scholar_name):
            initials = "".join(s[0] for s in syl if s).lower()
            if len(initials) >= 2 and clean_local.startswith(initials):
                return True
            full_py = "".join(s for s in syl if s).lower()
            if len(full_py) >= 3 and clean_local.startswith(full_py):
                return True
    except Exception:
        return False
    return False


def own_email(name: str, own: List[str], holders: Dict[str, Set[Any]], key: Any,
              colleagues: Set[str], taken: Set[str]) -> str:
    """The address a teacher's own card or profile body gives, by location.

    ``own`` lists the addresses inside this teacher's own card and verified
    profile container, in page order; ``holders`` maps each address to every
    teacher whose own card or profile contains it. Domain and spelling do not
    decide ownership:

    * an address on several teachers' own pages is a shared contact (college
      office, lab), unless it spells exactly one of them;
    * an address that spells a colleague's name and not this teacher's is the
      colleague's;
    * among several own addresses, one that spells the name comes first, then
      page order.
    """
    ranked = []
    for index, email in enumerate(own):
        low = email.lower()
        if low in taken:
            continue
        mine = email_name_affinity(email, name)
        if not mine and any(email_name_affinity(email, other) for other in colleagues if other != name):
            continue
        sharers = holders.get(low, set()) - {key}
        if sharers and not (mine and not any(email_name_affinity(email, holder[0]) for holder in sharers
                                             if holder[0] != name)):
            continue
        ranked.append((not mine, index, email))
    return min(ranked)[2] if ranked else ""


def own_text_emails(text: str, name: str, colleagues=()) -> List[str]:
    """Addresses in a teacher's own biography text (e.g. an OCR'd resume image),
    in order, except one that spells a colleague's name and not this teacher's."""
    return [email for email in dict.fromkeys(find_emails(text))
            if email_name_affinity(email, name)
            or not any(email_name_affinity(email, other) for other in colleagues if other != name)]


def is_institutional_email(email: str, scholar_name: str = "") -> bool:
    if not email or '@' not in email:
        return False
    if scholar_name:
        return not is_personal_email_for_scholar(email, scholar_name)
    return False

def is_noise_element(tag: Tag) -> bool:
    """Detects universal website chrome (header, footer, nav, sidebar, copyright)."""
    if not isinstance(tag, Tag):
        return False
    if tag.name in PRUNE_TAGS:
        return True
    cls_str = " ".join(tag.get('class', [])).lower()
    id_str = (tag.get('id') or "").lower()
    combined = f"{cls_str} {id_str}"
    if any(k in combined for k in ('footer', 'nav', 'menu', 'sidebar', 'copyright', 'bottom')):
        return True
    if 'header' in combined:
        if not any(k in combined for k in ('profile', 'teacher', 'author', 'person', 'faculty')):
            return True
    return False


@dataclass(frozen=True)
class FacultyCandidate:
    name: str
    url: str
    title: str = "教师"
    card_context: str = ""

@dataclass(frozen=True)
class FacultyProfile:
    name: str
    title: str
    email: str
    full_bio: str
    emails: tuple = ()          # every address inside the content container, in order

class FacultyDirectoryExtractor:
    """
    Interface for extracting faculty members from directory/list pages
    using DOM Isomorphic Subtree Clustering.
    """

    @staticmethod
    def get_structure_fingerprint(node: Tag, depth: int = 2) -> str:
        """Calculates a structural signature of a DOM element, filtering out dynamic/zebra classes."""
        if depth == 0 or not isinstance(node, Tag):
            return ""
        child_tags = sorted([c.name for c in node.children if isinstance(c, Tag)])
        # Filter out alternating/dynamic zebra classes (e.g. odd, even, row-0, item-1, bg-*)
        filtered_classes = sorted([
            c for c in node.get('class', [])
            if not re.search(r'^(odd|even|\d+|[a-z]\d+|row\d*|item\d*|col\d*|news\d*|list\d*|bg-\w+|color-\w+)$', c, re.I)
        ])
        class_str = "." + ".".join(filtered_classes) if filtered_classes else ""
        tag_counts = Counter(child_tags)
        child_summary = ','.join(f'{k}+' if v > 2 else f'{k}*{v}' for k, v in sorted(tag_counts.items()))
        return f"{node.name}{class_str}[{child_summary}]"


    @staticmethod
    def _clean_name(raw: str) -> tuple:
        # Strip zero-width padding some templates inject into the name cell
        # (生命科学学院 '\u200b张国进'); it is invisible in the delivered workbook.
        raw = strip_invisible(raw)
        if not raw:
            return "", "教师"
        title_found = "教师"
        # Match Chinese or ASCII parentheses: （教授）, (副教授), （博导/教授）
        paren_match = re.search(r'[（\(]([^）\)]+)[）\)]', raw)
        if paren_match:
            paren_content = paren_match.group(1).strip()
            for kw in TITLE_KEYWORDS:
                if kw in paren_content:
                    title_found = kw
                    break
            raw = re.sub(r'[（\(][^）\)]+[）\)]', '', raw).strip()

        # Strip bracket prefixes like [校示范课教师], 【教授】
        raw = re.sub(r'^[\[【][^\]】]+[\]】]\s*', '', raw).strip()

        # Handle title - name or title: name separators like '校示范课教师-郑佳生', '院长：李旭明'
        sep_match = re.split(r'[-－:：]', raw)
        if len(sep_match) >= 2:
            potential_title, potential_name = sep_match[0].strip(), sep_match[-1].strip()
            if 2 <= len(potential_name) <= 6 and all('\u4e00' <= ch <= '\u9fff' for ch in potential_name):
                raw = potential_name
                for kw in TITLE_KEYWORDS:
                    if kw in potential_title:
                        title_found = kw
                        break

        if all('\u4e00' <= ch <= '\u9fff' or ch.isspace() for ch in raw):
            name = re.sub(r'\s+', '', raw)
        else:
            name = re.sub(r'\s+', ' ', raw).strip()

        # A degree after the name ('林海 博士') is not part of it and not a title.
        degree = _DEGREE_AFTER_NAME.match(name)
        if degree:
            name = degree.group(1)

        # A supervisor list links each person as '李星教授介绍' / '张慧副教授简介'.
        introduced = _INTRODUCED_NAME.match(name)
        if introduced:
            return introduced.group(1), (introduced.group(2) if title_found == "教师" else title_found)
        for kw in TITLE_KEYWORDS:
            if name.endswith(kw) and len(name) > len(kw):
                if title_found == "教师":
                    title_found = kw
                return name[:-len(kw)].strip(), title_found
            if name.startswith(kw) and len(name) > len(kw):
                if title_found == "教师":
                    title_found = kw
                return name[len(kw):].strip(), title_found
        return name, title_found

    @classmethod
    def _l2_candidates(cls, html: str, base_url: str,
                       review: Optional[list] = None) -> List[FacultyCandidate]:
        """Contract with iso_mining:
        - low-prior names need repeated linked-card evidence from trusted peers
        - without it, a name with a rare surname goes to ``review`` (when given),
          never to the result: a person must confirm it
        - VirtualNode records (text lists) are first-class
        - records without a link are kept: url = list page (profile-less faculty)
        - dedupe key preserves homonyms: if profile link is missing, identity of head node prevents collapse"""
        res = mine_records(BeautifulSoup(html, 'lxml'), page_url=base_url)
        g = res.best
        if g is None or g.utility <= 0:
            return []
        # A high-prior child-heading group may score above the enclosing card
        # group after rare-surname headings were omitted during same-signature
        # merging. Prefer the fuller linked-card group when it covers the same
        # DOM container and has several independently trusted peers.
        fuller = [candidate for candidate in res.candidates
                  if candidate.container is g.container
                  and candidate.size > g.size
                  and candidate.utility > 0
                  and candidate.features.get("link_ratio", 0) >= 0.8
                  and sum(p >= 0.5 for p in candidate.record_probs) >= 3]
        if fuller:
            g = max(fuller, key=lambda candidate: (candidate.size, candidate.utility))
        if isinstance(g.records[0][0], VirtualNode):
            # A text-list candidate can absorb nearby unlinked headings. If a
            # real linked-card group accounts for every linked item in that
            # same container, its DOM boundary is stronger evidence.
            linked_urls = {first_href(r, base_url) for r in g.records
                           if first_href(r, base_url) is not None}
            structural = [candidate for candidate in res.candidates
                          if candidate.container is g.container
                          and not isinstance(candidate.records[0][0], VirtualNode)
                          and candidate.utility > 0
                          and candidate.features.get("link_ratio", 0) == 1.0
                          and sum(p >= 0.5 for p in candidate.record_probs) >= 3
                          and len(candidate.records) == len(linked_urls)
                          and {first_href(r, base_url) for r in candidate.records} == linked_urls]
            if structural:
                g = max(structural, key=lambda candidate: candidate.utility)
        # One roster is often rendered as several accepted groups: linked vs
        # link-less cards, or one block per title. Merge accepted groups that
        # share best's container or are sibling blocks of it; a leadership bar
        # in a different region stays out (cross-page template inference is
        # the stronger fix once sibling list pages are available).
        def _region(x):
            c = x.container
            return (id(c), id(getattr(c, "parent", None)))
        bc, bp = _region(g)
        groups = [g] + [a for a in res.alternatives
                        if a is not res.best and a is not g
                        and (_region(a)[0] == bc or _region(a)[1] == bp)]
        pairs = [(grp, r, p) for grp in groups for r, p in zip(grp.records, grp.record_probs)]
        # Text every card repeats ('查看详情 >>', a field label) is template, not a
        # name; such a card is named by its first name-shaped text of its own.
        repeated: Dict[int, Counter] = {}
        for grp in groups:
            if not isinstance(grp.records[0][0], VirtualNode):
                repeated[id(grp)] = Counter(token for rec in grp.records
                                            for token in set(primary_tokens(rec, k=16)))
        out, seen = [], set()
        for idx, (grp, r, p) in enumerate(pairs):
            needs_review = False
            if p < 0.5:
                # A closed surname list cannot decide whether an otherwise
                # ordinary-looking string is a person. Repeated linked cards
                # with several already trusted peers provide independent DOM
                # evidence; unstructured text lists never receive this lift.
                peers = sum(prob >= 0.5 for prob in grp.record_probs)
                tokens = primary_tokens(r)
                proposed = tokens[0] if tokens else ""
                structural_proof = (
                    not isinstance(r[0], VirtualNode)
                    and grp.size >= 4 and peers >= 3
                    and grp.features.get("link_ratio", 0) >= 0.8
                    and grp.features.get("self_ref", 1) <= 0.1
                    and bool(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", proposed))
                    and first_href(r, base_url) is not None
                )
                if not structural_proof:
                    if review is None:
                        continue
                    needs_review = True
            head = r[0]
            if isinstance(head, VirtualNode):
                if not head.shape_ok:
                    continue
                raw, card = head.core, head.text
                href = urljoin(base_url, head.href) if head.href else None
                section = head.section or ""
            else:
                toks = primary_tokens(r)
                raw = toks[0] if toks else ""
                shared = repeated.get(id(grp), Counter())
                # Homonyms ('王伟' on two cards) repeat too, but they read as names.
                if (raw and grp.size >= 2 and shared[raw] >= max(2, (grp.size + 1) // 2)
                        and name_prob(raw) < TEMPLATE_NAME_PRIOR):
                    raw = next((token for token in primary_tokens(r, k=16)
                                if shared[token] == 1 and _NAME_SHAPED.fullmatch(token)), "")
                card = " ".join(n.get_text(separator=' ', strip=True) for n in r)
                href = first_href(r, base_url)
                section = ""
            name, title = cls._clean_name(raw)
            if not name:
                continue
            # The title vocabulary only identifies a title standing in the name slot.
            if needs_review and (name in TITLE_KEYWORDS or not rare_surname_name(name)):
                continue
            if title == "教师":
                ctx = f"{section} {card}"
                title = next((kw for kw in TITLE_KEYWORDS if kw in ctx), "教师")
            url = href or base_url
            node_key = href if href else id(head)
            if (name, node_key) in seen:
                continue
            seen.add((name, node_key))
            candidate = FacultyCandidate(name=name, url=url, title=title, card_context=card)
            (review if needs_review else out).append(candidate)
        return out

    @classmethod
    def extract_with_review(cls, html: str,
                            base_url: str) -> Tuple[List[FacultyCandidate], List[FacultyCandidate]]:
        """Admitted candidates, and rare-surname names a person must confirm first."""
        review: List[FacultyCandidate] = []
        return cls.extract(html, base_url, review=review), review

    @classmethod
    def extract(
        cls,
        html: str,
        base_url: str,
        executor: Optional[L1RuntimeExecutor] = None,
        emit_ticket: bool = False,
        *,
        review: Optional[list] = None,
    ) -> List[FacultyCandidate]:
        if not html:
            return []

        # Tier 0: L1 cached XPath wrapper (sub-millisecond deterministic path)
        exec_instance = executor or get_default_l1_executor()
        l1_miss = True
        if exec_instance is not None:
            try:
                records, status = exec_instance.execute(html, base_url)
                if status == "hit" and records:
                    log.info("L1 wrapper hit for %s (%d records)", base_url, len(records))
                    cands = []
                    for r in records:
                        clean_name, clean_title = cls._clean_name(r["name"])
                        cands.append(FacultyCandidate(
                            name=clean_name,
                            url=r["url"] or base_url,
                            title=clean_title,
                            card_context=""
                        ))
                    return cands
                elif status == "drift":
                    log.warning("L1 wrapper drift detected for %s; circuit breaker tripped, falling back to L2", base_url)
                    l1_miss = False
            except Exception as e:
                log.debug("L1 execution exception on %s: %s", base_url, e)

        # Some CMS templates leave the page-wide <header> open until </body>.
        # A header that is the sole body container is the page, not chrome.
        # Unwrap the container so ordinary structural extraction can inspect
        # its children; the inner nav/footer elements are still pruned below.
        parsed = BeautifulSoup(html, 'html.parser')
        body = parsed.body
        if body is not None:
            body_children = [child for child in body.children
                             if isinstance(child, Tag) and child.name not in ('script', 'style')]
            if len(body_children) == 1 and body_children[0].name == 'header':
                body_children[0].unwrap()
                html = str(parsed)

        # Tier 1: L2 isomorphic mining (the only path that honours the charter)
        if L2_AVAILABLE:
            try:
                l2 = cls._l2_candidates(html, base_url, review=review)
            except Exception:
                log.exception("L2 failed on %s", base_url)   # visible, then fall back
                l2 = None
            if l2:
                # If L1 was a miss and emit_ticket is requested, schedule L3 compiler ticket
                if l1_miss and emit_ticket and exec_instance and exec_instance.queue and cluster_key_for:
                    try:
                        key = cluster_key_for(html, base_url)
                        exec_instance.queue.create_compile_ticket(
                            key, f"New cluster seen at {base_url}; schedule L3 synthesis."
                        )
                    except Exception as ex:
                        log.debug("Failed to emit L3 compile ticket: %s", ex)
                return l2

        # Tier 2 (Heuristic Structural Clustering Fallback)
        soup = BeautifulSoup(html, 'html.parser')

        # Structural Pruning: prune standard chrome / navigation / pagination containers
        for tag_name in PRUNE_TAGS:
            for el in soup.find_all(tag_name):
                el.decompose()

        for class_name in PRUNE_CLASSES:
            for el in soup.find_all(class_=lambda c: c and class_name in c.split()):
                el.decompose()

        # Step 1: Collect candidate links
        links = soup.find_all('a', href=True)
        structural_items = []

        for a in links:
            raw_text = a.get_text(strip=True)
            href = a['href'].strip()

            if not href or href.startswith(('javascript:', '#', 'mailto:')):
                continue

            name_el = a.find(class_=re.compile(r'name|title', re.I))
            if name_el and 2 <= len(name_el.get_text(strip=True)) <= 10:
                raw_text = name_el.get_text(strip=True)

            clean_name, clean_title = cls._clean_name(raw_text)

            # A person's name in Chinese directory is typically 2-6 chars (or letters for foreign scholars)
            if len(clean_name) < 2 or len(clean_name) > 15:
                continue

            # Prior name probability via Opus, avoiding any hardcoded keyword blacklists
            if L2_AVAILABLE:
                from iso_mining import name_prob
                if name_prob(clean_name) < 0.35:
                    continue
            elif re.search(r'[\d_#=<>\[\]\(\)]', clean_name):
                continue

            # Resolve URL
            full_url = urljoin(base_url, href)
            p_path = urlparse(full_url).path
            if p_path.rstrip('/') == urlparse(base_url).path.rstrip('/'):
                continue
            if full_url.rstrip('/') == base_url.rstrip('/'):
                continue

            # Get parent/ancestor container for structural clustering
            parent = a.parent
            # Move up if parent is just an inline wrapper
            while parent and parent.name in ['span', 'strong', 'b', 'em', 'font']:
                parent = parent.parent

            if parent and is_noise_element(parent):
                continue

            if parent:
                # Find card container (e.g. li, tr, div with card semantics)
                card = parent
                if card.name in ['div', 'td', 'p'] and card.parent and card.parent.name in ['li', 'tr', 'td']:
                    card = card.parent

                fp = cls.get_structure_fingerprint(card)
                structural_items.append({
                    'name': clean_name,
                    'url': full_url,
                    'card': card,
                    'fingerprint': fp,
                    'raw_link': a
                })

        if not structural_items:
            return []

        # Step 2: Cluster by structural fingerprint
        fp_counts = Counter(item['fingerprint'] for item in structural_items)
        if not fp_counts:
            return []

        # Dominant cluster selection: the highest frequency isomorphic subtree
        dominant_fp, dominant_freq = fp_counts.most_common(1)[0]
        dominant_prefix = dominant_fp.split('[')[0]

        candidates = []
        seen_urls: Set[str] = set()

        for item in structural_items:
            # Belongs to dominant cluster or shares list container fingerprint pattern
            card_tag = item['card'].name if item.get('card') else ''
            is_match = (
                item['fingerprint'] == dominant_fp 
                or (dominant_fp.endswith('[a+]') and item['fingerprint'].startswith(dominant_prefix) and item['fingerprint'].endswith(']'))
                or fp_counts[item['fingerprint']] >= 3
                or card_tag in ['tr', 'td', 'li']
                or (dominant_freq < 3 and fp_counts[item['fingerprint']] >= 2)
            )
            if is_match:
                url = item['url']
                name = item['name']

                card_text = item['card'].get_text(separator=' ', strip=True) if item['card'] else ""
                parent_block = item['card'].find_parent(class_=re.compile(r'block|section|group|list', re.I)) or item['card']
                block_title_el = parent_block.find(class_=re.compile(r'title|heading|caption', re.I)) if parent_block else None
                block_title = block_title_el.get_text(strip=True) if block_title_el else ""
                context_text = f"{block_title} {card_text}"
                matched_title = next((t for t in TITLE_KEYWORDS if t in context_text), "教师")

                if url in seen_urls:
                    if matched_title != "教师":
                        for idx_c, existing_c in enumerate(candidates):
                            if existing_c.url == url and existing_c.title == "教师":
                                candidates[idx_c] = FacultyCandidate(
                                    name=existing_c.name,
                                    url=existing_c.url,
                                    title=matched_title,
                                    card_context=context_text
                                )
                                break
                    continue

                seen_urls.add(url)

                candidates.append(FacultyCandidate(
                    name=name,
                    url=url,
                    title=matched_title,
                    card_context=card_text
                ))

        return candidates


BLOCK_ELEMENTS = {
    'div', 'p', 'tr', 'td', 'th', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'article', 'section', 'header', 'footer', 'nav', 'table', 'tbody', 'thead', 'br', 'hr'
}

EMAIL_REGEX = re.compile(r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}'
                         r'(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}(?![A-Za-z0-9-])')

def find_emails(text: str) -> List[str]:
    """All addresses in reading order, incl. '(at)'/'＠' obfuscations."""
    if email_spans is not None:                     # original case kept for delivery
        return [re.sub(r"\s+", "", _AT_RE.sub("@", text[a:b])).strip(".") for a, b, _ in email_spans(text)]
    return [m.lower() for m in EMAIL_REGEX.findall(text)]

class LayoutAwareTokenizer:
    """Extracts text while strictly adhering to W3C physical CSS block boundaries."""

    @staticmethod
    def get_clean_text(root: Tag) -> str:
        if not root:
            return ""
        tokens = []
        stack = [(root, 0)]  # (node, stage): 0 = enter, 1 = exit

        while stack:
            curr, stage = stack.pop()
            if isinstance(curr, NavigableString):
                if not isinstance(curr, Comment):
                    raw = str(curr)
                    raw = raw.replace('\xa0', ' ').replace('\u3000', ' ')
                    cleaned = re.sub(r'[ \t\r\f\v]+', ' ', raw)
                    if cleaned and cleaned != ' ':
                        tokens.append(cleaned)
                    elif cleaned == ' ':
                        if tokens and not tokens[-1].endswith((' ', '\n')):
                            tokens.append(' ')
                continue

            if not isinstance(curr, Tag):
                continue

            if stage == 1:
                if curr.name in BLOCK_ELEMENTS:
                    if tokens and not tokens[-1].endswith('\n'):
                        tokens.append('\n')
                continue

            if is_noise_element(curr):
                continue

            is_block = curr.name in BLOCK_ELEMENTS
            if is_block:
                if tokens and not tokens[-1].endswith('\n'):
                    tokens.append('\n')

            stack.append((curr, 1))
            for child in reversed(list(curr.children)):
                stack.append((child, 0))

        full_text = "".join(tokens)
        full_text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', full_text)
        full_text = re.sub(r'[ \t]+', ' ', full_text)
        full_text = re.sub(r' ?\n ?', '\n', full_text)
        return re.sub(r'\n{3,}', '\n\n', full_text).strip()



class FacultyProfileExtractor:
    """
    Interface for scholar profile extraction:
    - Strictly scoped to Minimal Enclosing Content Container (MECC).
    - Preserves CSS block boundaries so phones and emails never concatenate.
    - Preserves numeric employee IDs intact.
    - Captures complete academic resume without AI summarization.
    """

    CONTENT_SELECTORS = [
        '.teacher', '.teacher-detail', '.teacher-info',
        '.col_news_con', '.wp_articlecontent', '.v_news_content', '#vsb_content', 
        '#vsb_content_2', '.article-content', '.main-content', 
        '.article', '.content', '.entry', '.detail', '.text', 'article', 'main'
    ]

    @classmethod
    def extract(cls, html: str, base_url: str, fallback_name: str = "") -> FacultyProfile:
        if not html:
            return FacultyProfile(name=fallback_name, title="教师", email="", full_bio="")

        if DETAIL_MECC_AVAILABLE and DetailMECCExtractor is not None:
            try:
                detail = DetailMECCExtractor.extract_single(html, base_url, fallback_name=fallback_name)
                return FacultyProfile(
                    name=detail.name,
                    title=detail.title,
                    email=detail.email,
                    full_bio=detail.full_bio,
                    emails=detail.emails
                )
            except Exception as ex:
                log.warning("DetailMECCExtractor.extract_single failed: %s; falling back", ex)

        soup = BeautifulSoup(html, 'html.parser')

        # Decompose universal chrome/footer zones that host institutional emails
        for junk in soup(['script', 'style', 'noscript', 'svg', 'iframe', 'header', 'footer', 'nav']):
            junk.decompose()
        for c in soup.find_all(string=lambda text: isinstance(text, Comment)):
            c.extract()
        for junk_class in ['header', 'footer', 'nav', 'bottom', 'copyright']:
            for el in soup.find_all(class_=lambda c: c and junk_class in c.split()):
                el.decompose()

        # Locate MECC (Minimal Enclosing Content Container)
        mecc = None
        for sel in cls.CONTENT_SELECTORS:
            mecc = soup.select_one(sel)
            if mecc:
                break
        if not mecc:
            mecc = soup.body or soup

        # Extract text adhering to CSS block layout boundaries
        full_bio = LayoutAwareTokenizer.get_clean_text(mecc)

        # Extract email strictly from the MECC text stream
        emails = [em for em in find_emails(full_bio)
                  if not em.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.css', '.js'))]
        valid_email = emails[0] if emails else ""

        # Check for glued phone numbers (e.g. 0510-859112348843120007@...)
        if valid_email and DomainConventionModel:
            try:
                conv = DomainConventionModel()
                fixed, phone, amb = conv.split_glued_numeric(valid_email)
                if fixed != valid_email:
                    valid_email = fixed
            except Exception:
                pass

        # Name resolution
        name = fallback_name
        if not name:
            h1 = mecc.find(['h1', 'h2', 'h3'])
            if h1:
                name = re.sub(r'\s+', '', h1.get_text(strip=True))

        # Title resolution: earliest non-overlapping keyword in header/bio
        title = resolve_title(full_bio[:400])

        return FacultyProfile(
            name=name,
            title=title,
            email=valid_email,
            full_bio=full_bio,
            emails=tuple(emails)
        )


class FacultyHarvestPipeline:
    """
    High-level orchestrator connecting Directory Extraction and Profile Extraction.
    Enforces the Dual Sheet Conservation Axiom:
    Sheet 1 (with email) + Sheet 2 (without email) == Total Scholars.
    """

    @classmethod
    def _find_exclusive_card_container(
        cls,
        soup: BeautifulSoup,
        target_cand: FacultyCandidate,
        peer_cands: List[FacultyCandidate]
    ) -> Optional[Tag]:
        peer_names = {c.name.strip() for c in peer_cands if c.name and c.name.strip() and c.name.strip() != target_cand.name.strip()}
        occurrences = []
        for text_node in soup.find_all(string=lambda t: t and target_cand.name.strip() in t):
            if text_node.parent:
                occurrences.append(text_node.parent)

        best_container = None
        best_score = -1

        for node in occurrences:
            curr = node
            valid_container = None
            while curr and curr.name not in ('body', 'html', '[document]'):
                curr_text = curr.get_text()
                if any(p_name in curr_text for p_name in peer_names):
                    break
                valid_container = curr
                curr = curr.parent

            if valid_container is not None:
                c_text = valid_container.get_text()
                emails = find_emails(c_text)
                has_title = any(t in c_text for t in TITLE_KEYWORDS)
                score = len(emails) * 50 + (25 if has_title else 0) + min(len(c_text.strip()), 200)
                if score > best_score:
                    best_score = score
                    best_container = valid_container

        return best_container

    @classmethod
    def _resolve_shared_page_candidates(
        cls,
        cands_on_page: List[FacultyCandidate],
        html: str,
        page_url: str,
        dcm: Optional[DomainConventionModel] = None
    ) -> Tuple[Dict[int, Dict[str, Any]], List[Dict[str, Any]]]:
        soup = BeautifulSoup(html, 'html.parser')
        for tag_name in PRUNE_TAGS:
            for el in soup.find_all(tag_name):
                el.decompose()
        for el in soup.find_all(lambda t: is_noise_element(t) if isinstance(t, Tag) else False):
            el.decompose()

        results = {}
        unresolved = []

        cand_cards = {}
        for cand in cands_on_page:
            peers = [c for c in cands_on_page if c is not cand]
            cand_cards[id(cand)] = cls._find_exclusive_card_container(soup, cand, peers)

        surname_counts = Counter(c.name[0] for c in cands_on_page if c.name)
        assigned_emails = set()
        card_email_sets = {
            id(c): {em.lower() for em in find_emails(LayoutAwareTokenizer.get_clean_text(cand_cards[id(c)]))}
            for c in cands_on_page if cand_cards.get(id(c))}

        for cand in cands_on_page:
            card = cand_cards.get(id(cand))
            card_text = LayoutAwareTokenizer.get_clean_text(card) if card else ""
            card_emails = find_emails(card_text) if card_text else []

            matched_title = cand.title
            if card_text:
                matched_title = next((t for t in TITLE_KEYWORDS if t in card_text), cand.title)

            email = ""
            same_sur_count = surname_counts.get(cand.name[0], 1) if cand.name else 1

            valid_card_emails = []
            for em in card_emails:
                # The exclusive card locates the owner; the spelling does not.
                mine = email_name_affinity(em, cand.name)
                other_matches = [p.name for p in cands_on_page if p is not cand and email_name_affinity(em, p.name)]
                if other_matches and not mine:
                    unresolved.append({
                        "姓名": cand.name, "主页": page_url,
                        "status": "conflicting_card_email",
                        "email": em,
                        "reason": f"Card for '{cand.name}' contains email '{em}' which matches peer(s) {other_matches}"
                    })
                    continue
                if sum(em.lower() in card_email_sets.get(id(peer), set()) for peer in cands_on_page) > 1:
                    continue  # one address in several cards is a shared contact

                local_part = em.split('@')[0].lower()
                clean_local = re.sub(r'[-_.]', '', local_part)
                # Disallow surname-only matching when multiple peers share this surname
                if same_sur_count > 1 and dcm is not None:
                    if any(len(syl) > 1 and clean_local == syl[0] for syl in dcm.readings(cand.name)):
                        unresolved.append({
                            "姓名": cand.name, "主页": page_url,
                            "status": "ambiguous_surname_email",
                            "email": em,
                            "reason": f"Surname-only email '{em}' on shared page with multiple peers sharing surname '{cand.name[0]}'"
                        })
                        continue

                valid_card_emails.append(em)

            if valid_card_emails:
                # Several addresses in one person's own card are all theirs:
                # one that spells the name first, then card order.
                email = min(valid_card_emails, key=lambda em: (not email_name_affinity(em, cand.name),
                                                               valid_card_emails.index(em)))
                assigned_emails.add(email.lower())

            results[id(cand)] = {
                "email": email,
                "title": matched_title,
                "bio": card_text
            }

        # Check page-level emails outside candidate exclusive cards
        all_page_emails = [em.lower() for em in find_emails(LayoutAwareTokenizer.get_clean_text(soup))]
        all_card_emails = set()
        for card in cand_cards.values():
            if card:
                all_card_emails.update(em.lower() for em in find_emails(LayoutAwareTokenizer.get_clean_text(card)))

        uncontained_emails = [em for em in all_page_emails if em not in all_card_emails]
        for u_em in uncontained_emails:
            matching_cands = [c for c in cands_on_page if is_personal_email_for_scholar(u_em, c.name)]
            if len(matching_cands) > 1:
                for mc in matching_cands:
                    unresolved.append({
                        "姓名": mc.name, "主页": page_url,
                        "status": "ambiguous_shared_email",
                        "email": u_em
                    })
            elif len(matching_cands) == 1:
                mc = matching_cands[0]
                if not results[id(mc)]["email"] and u_em not in assigned_emails:
                    local_part = u_em.split('@')[0].lower()
                    clean_local = re.sub(r'[-_.]', '', local_part)
                    same_sur_count = surname_counts.get(mc.name[0], 1) if mc.name else 1
                    if same_sur_count > 1 and dcm is not None:
                        if any(len(syl) > 1 and clean_local == syl[0] for syl in dcm.readings(mc.name)):
                            unresolved.append({
                                "姓名": mc.name, "主页": page_url,
                                "status": "ambiguous_surname_email",
                                "email": u_em
                            })
                            continue
                    results[id(mc)]["email"] = u_em
                    assigned_emails.add(u_em.lower())

        return results, unresolved

    @classmethod
    def harvest_candidates(
        cls,
        candidates: List[FacultyCandidate],
        profile_fetcher,
        base_url: str = "",
        school: str = "",
        college: str = "",
        agent_name: str = "Antigravity (IDE Agent)",
        date_str: str = "",
        profileless_urls: Optional[Set[str]] = None,
    ) -> Dict:
        import datetime
        if not date_str:
            date_str = datetime.datetime.now().strftime("%Y-%m-%d")

        # Step 1: Deduplicate candidates by canonical personal profile URL
        # For candidates with personal profile URLs, collapse identical URLs for the same scholar.
        # For profile-less candidates (url == base_url or empty), preserve distinct faculty members (homonyms).
        unique_candidates = []
        seen_cand_urls = set()
        for cand in candidates:
            if cand.url and cand.url != base_url and cand.url not in ('', '/'):
                cand_id = ('url', cand.name, cand.url)
                if cand_id in seen_cand_urls:
                    continue
                seen_cand_urls.add(cand_id)
            unique_candidates.append(cand)
        candidates = unique_candidates

        # Step 2: Fetch profile HTMLs
        # A failed fetch is NOT 'no e-mail': it is unresolved and
        # blocks delivery, otherwise timeouts silently land in Sheet 2.
        fetch_results = []                                    # (cand, html, status)
        no_profile_pages = set(profileless_urls or ())
        if base_url:
            no_profile_pages.add(base_url)
        for cand in candidates:
            if not cand.url or cand.url in no_profile_pages: # profile-less faculty
                fetch_results.append((cand, None, "no_profile"))
                continue
            html, status = None, "fetch_failed"
            for _attempt in range(2):
                try:
                    html = profile_fetcher(cand.url) if profile_fetcher else ""
                    if html is None or (isinstance(html, str) and not html.strip()):
                        status = "fetch_failed:empty_response"
                    else:
                        status = "ok"
                    break
                except Exception as ex:
                    status = f"fetch_failed:{type(ex).__name__}"
            fetch_results.append((cand, html, status))

        # Step 3: Secondary profile extraction
        # Use DetailMECCExtractor.extract_cluster for cross-page template inference & cluster-level detail-df
        successful_pages = [
            (cand.url, html, cand.name)
            for cand, html, status in fetch_results
            if status == "ok" and html
        ]

        details_by_url = {}
        if successful_pages and DETAIL_MECC_AVAILABLE and DetailMECCExtractor is not None:
            try:
                cluster_details = DetailMECCExtractor.extract_cluster(successful_pages)
                details_by_url = {d.url: d for d in cluster_details}
            except Exception as ex:
                log.warning("DetailMECCExtractor.extract_cluster failed: %s; falling back", ex)
                details_by_url = {}

        # Fallback to single page extractor if cluster extraction was not used
        single_profiles = {}
        owners: Dict[str, set] = {}
        if not details_by_url:
            for cand, html, status in fetch_results:
                if status == "ok" and html:
                    single_profiles[cand.url] = FacultyProfileExtractor.extract(
                        html, cand.url, fallback_name=cand.name
                    )

            # detail-df fallback across single profiles
            for cand, _, status in fetch_results:
                prof = single_profiles.get(cand.url)
                for em in (prof.emails if prof else ()):
                    owners.setdefault(em.lower(), set()).add((cand.name, cand.url))

        # Step 4: Assemble dual sheets with strict conservation
        url_counts = Counter(cand.url for cand in candidates if cand.url)
        assigned_emails: Set[str] = set()
        dcm = DomainConventionModel() if DomainConventionModel is not None else None

        # Addresses inside each teacher's own card and own profile body, and
        # every teacher holding each address, so a shared contact is visible.
        own_pools: Dict[int, List[str]] = {}
        email_holders: Dict[str, Set[Any]] = {}
        college_names = {cand.name for cand in candidates if cand.name}
        for cand, html, status in fetch_results:
            if url_counts.get(cand.url, 0) > 1 and cand.url != base_url:
                continue  # several people on one page: resolved card by card below
            pool = list(find_emails(cand.card_context))
            prof = (details_by_url.get(cand.url) or single_profiles.get(cand.url)) if status == "ok" and html else None
            if prof:
                if details_by_url and prof.email:
                    pool.append(prof.email)
                pool.extend(prof.emails)
            pool = list(dict.fromkeys(pool))
            own_pools[id(cand)] = pool
            for em in pool:
                email_holders.setdefault(em.lower(), set()).add((cand.name, cand.url))

        sheet1_with_email, sheet2_no_email, unresolved = [], [], []
        cand_indices_1, cand_indices_2 = set(), set()

        # Pre-process shared detail pages
        shared_results_by_cand_id = {}
        for cand_url, count in url_counts.items():
            if count > 1 and cand_url and cand_url != base_url:
                cands_on_page = [c for c in candidates if c.url == cand_url]
                page_html = next((h for c, h, s in fetch_results if c.url == cand_url and s == "ok"), None)
                if page_html:
                    p_res, p_unres = cls._resolve_shared_page_candidates(cands_on_page, page_html, cand_url, dcm)
                    shared_results_by_cand_id.update(p_res)
                    unresolved.extend(p_unres)

        for cand_idx, (cand, html, status) in enumerate(fetch_results):
            if status.startswith("fetch_failed"):
                unresolved.append({"姓名": cand.name, "主页": cand.url, "status": status})
                continue

            email = ""
            title = cand.title
            bio = ""

            if id(cand) in shared_results_by_cand_id:
                s_res = shared_results_by_cand_id[id(cand)]
                email = s_res["email"]
                title = s_res["title"]
                bio = s_res["bio"]
                if email:
                    assigned_emails.add(email.lower())
            else:
                prof = None
                if status == "ok" and html:
                    prof = details_by_url.get(cand.url) or single_profiles.get(cand.url)

                # The candidate's own card, then its own 1-to-1 profile body:
                # an address there is the teacher's whatever its domain.
                email = own_email(cand.name, own_pools.get(id(cand), []), email_holders,
                                  (cand.name, cand.url), college_names, assigned_emails)
                if email:
                    assigned_emails.add(email.lower())

                if email and dcm is not None:
                    try:
                        email = dcm.split_glued_numeric(email)[0]
                    except Exception:
                        pass

                title = prof.title if prof and prof.title != "教师" else cand.title
                bio = prof.full_bio if prof else ""

            record = {
                "序号": 0, "姓名": cand.name, "职称": title, "邮箱": email,
                "学校": school, "学院": college, "采集人": agent_name, "采集日期": date_str,
                "简介": bio, "主页": cand.url, "_cand_idx": cand_idx
            }
            if email:
                record["序号"] = len(sheet1_with_email) + 1
                sheet1_with_email.append(record)
                cand_indices_1.add(cand_idx)
            else:
                record["序号"] = len(sheet2_no_email) + 1
                sheet2_no_email.append(record)
                cand_indices_2.add(cand_idx)

        def _person_key(r):
            name = (r.get("姓名") or "").strip()
            url = (r.get("主页") or r.get("url") or "").strip()
            if not url or url == base_url:
                return ("idx", r.get("_cand_idx", r.get("序号", 0)), name)
            elif url_counts.get(url, 0) > 1:
                return ("shared", name, url)
            else:
                return ("url", name, url)

        sheet1_persons = set(_person_key(r) for r in sheet1_with_email)
        sheet2_persons = set(_person_key(r) for r in sheet2_no_email)

        # Check: No person can appear in both sheets!
        overlap = sheet1_persons & sheet2_persons
        has_internal_dups = (len(sheet1_persons) != len(sheet1_with_email)) or (len(sheet2_persons) != len(sheet2_no_email))

        total_count = len(sheet1_with_email) + len(sheet2_no_email)

        # Dual-Sheet Conservation Axiom:
        # Sheet 1 + Sheet 2 == Total Faculty, mutually exclusive by person identity, zero omissions, zero duplicates.
        is_conserved = (
            len(candidates) > 0
            and len(unresolved) == 0
            and (total_count == len(candidates))
            and not (cand_indices_1 & cand_indices_2)
            and len(overlap) == 0
            and not has_internal_dups
        )
        delivery_ready = is_conserved and len(unresolved) == 0 and total_count > 0

        return {
            "total_count": total_count,
            "sheet1_with_email": sheet1_with_email,
            "sheet2_no_email": sheet2_no_email,
            "unresolved": unresolved,
            "is_conserved": is_conserved,
            "delivery_ready": delivery_ready,
        }

    @classmethod
    def harvest_directory(
        cls,
        directory_html: str,
        base_url: str,
        profile_fetcher,
        school: str = "",
        college: str = "",
        agent_name: str = "Antigravity (IDE Agent)",
        date_str: str = "",
        executor: Optional[L1RuntimeExecutor] = None,
        emit_ticket: bool = False
    ) -> Dict:
        # Step 1: Structural directory extraction via cascade (L1 -> L2 -> Fallback)
        candidates = FacultyDirectoryExtractor.extract(
            directory_html, base_url, executor=executor, emit_ticket=emit_ticket
        )
        return cls.harvest_candidates(
            candidates=candidates,
            profile_fetcher=profile_fetcher,
            base_url=base_url,
            school=school,
            college=college,
            agent_name=agent_name,
            date_str=date_str
        )
