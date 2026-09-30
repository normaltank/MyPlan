"""
Faculty Extractor: Deep Module for University Faculty Directory & Profile Harvesting.
Zero keyword blacklists. Based on:
1. DOM Isomorphic Subtree Clustering (Tree signature & structural clustering)
2. Layout-Aware Tokenization (Preserves CSS Block Formatting Context)
3. Minimal Enclosing Content Container (MECC Scoping)
"""

import re
from dataclasses import dataclass
from typing import List, Dict, Optional, Set, Tuple, Any
from collections import Counter
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag, NavigableString, Comment
from server.dom_regions import is_noise_element

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
from name_labels import without_degree_annotation  # noqa: E402 (Opus is on sys.path)

try:
    from iso_mining import (VirtualNode, first_href, mine_records, primary_tokens,
                            name_prob, rare_surname_name, strip_invisible)
    from email_matching import DomainConventionModel
    from text_lists import email_spans, _AT as _AT_PATTERN
    _AT_RE = re.compile(_AT_PATTERN)
    L2_AVAILABLE = True
except ImportError as _ex:                          # never silently: log it
    log.error("L2 algorithm layer unavailable: %s", _ex)
    mine_records = DomainConventionModel = VirtualNode = first_href = primary_tokens = email_spans = None
    rare_surname_name = None
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

TEMPLATE_NAME_PRIOR = 0.7
_NAME_SHAPED = re.compile(r"[\u4e00-\u9fff]{2,4}|[\u4e00-\u9fff]{1,8}(?:[·•][\u4e00-\u9fff]{1,8}){1,3}")

# '李星教授介绍': a 2-4 character name, a title keyword, then an introduction suffix.
_INTRODUCED_NAME = re.compile(
    r"^([\u4e00-\u9fff]{2,4}?)(" + "|".join(map(re.escape, TITLE_KEYWORDS))
    + r")(?:个人简介|简介|介绍|个人主页|主页)$")

try:
    from server.detail_mecc import (DetailMECCExtractor, ProfileDetail, LayoutAwareTokenizer,
                                    contact_email_candidates, labeled_text_emails)
    DETAIL_MECC_AVAILABLE = True
except ImportError:
    try:
        from detail_mecc import (DetailMECCExtractor, ProfileDetail, LayoutAwareTokenizer,
                                 contact_email_candidates, labeled_text_emails)
        DETAIL_MECC_AVAILABLE = True
    except ImportError as _ex:
        log.warning("Detail MECC extractor unavailable: %s", _ex)
        DetailMECCExtractor = ProfileDetail = LayoutAwareTokenizer = contact_email_candidates = None

        def labeled_text_emails(text):  # no evidence reader: nothing is labelled
            return []
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

if DETAIL_MECC_AVAILABLE:
    # One definition of "this address spells this teacher" for every path
    # (profile pages, shared pages, roster cards, OCR biographies).
    from server.detail_mecc import is_institutional_email, is_personal_email_for_scholar  # noqa: E402,F401
else:
    def is_personal_email_for_scholar(email: str, scholar_name: str = "") -> bool:
        """Degraded mode: without the pinyin model only numeric IDs identify a person."""
        if not email or '@' not in email:
            return False
        local = re.sub(r'[-_.]', '', email.lower().partition('@')[0])
        return local.isdigit() or not scholar_name

    def is_institutional_email(email: str, scholar_name: str = "") -> bool:
        if not email or '@' not in email:
            return False
        return bool(scholar_name) and not is_personal_email_for_scholar(email, scholar_name)


@dataclass(frozen=True)
class FacultyCandidate:
    name: str
    url: str
    title: str = "教师"
    card_context: str = ""
    name_review_reason: str = ""

@dataclass(frozen=True)
class FacultyProfile:
    name: str
    title: str
    email: str
    full_bio: str
    emails: tuple = ()          # every address inside the content container, in order
    contact_emails: tuple = ()

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
        return without_degree_annotation(name), title_found

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
        # Score the same name slot we will store. Otherwise an attached title
        # makes a three-character name five characters long and suppresses its
        # record before _clean_name is ever reached. Raw card text stays intact.
        res = mine_records(BeautifulSoup(html, 'lxml'), page_url=base_url,
                           name_fn=lambda token: name_prob(cls._clean_name(token)[0]))
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
                proposed = cls._clean_name(tokens[0])[0] if tokens else ""
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
                            base_url: str, *, card_selector: str | None = None
                            ) -> Tuple[List[FacultyCandidate], List[FacultyCandidate]]:
        """Admitted candidates, and rare-surname names a person must confirm first."""
        review: List[FacultyCandidate] = []
        if card_selector:
            return cls._verified_card_candidates(html, base_url, card_selector)
        return cls.extract(html, base_url, review=review), review

    @classmethod
    def _verified_card_candidates(cls, html: str, base_url: str, selector: str):
        """Account for each verified card; full-page mining cannot override its boundary."""
        cards = BeautifulSoup(html, 'html.parser').select(selector)
        if cards and all(card.name == 'img' for card in cards):
            return cls.extract_with_review(html, base_url)
        slots = []
        for card in cards:
            anchors = ([card] if card.name == 'a' else []) + card.find_all('a')
            labels = []
            for anchor in anchors:
                raw = anchor.get_text(' ', strip=True)
                if raw:
                    href = first_href([anchor], base_url)
                    labels.append((raw, href))
                    labels.extend((token, href) for token in primary_tokens([anchor]))
            labels.extend((token, None) for token in primary_tokens([card]))
            linked_slots = {}
            for raw, href in labels:
                name, title = cls._clean_name(raw)
                if href and name and name not in TITLE_KEYWORDS and name_prob(name) >= 0.5:
                    linked_slots.setdefault((name, href), (name, title, href))
            slot = None
            for raw, href in labels:
                name, title = cls._clean_name(raw)
                if name and name not in TITLE_KEYWORDS and name_prob(name) >= 0.5:
                    slot = (name, title, href)
                    break
            if slot is None and labels:
                name, title = cls._clean_name(labels[0][0])
                if name and name not in TITLE_KEYWORDS and len(name) <= 40:
                    slot = (name, title, labels[0][1])
            if slot is None:
                continue
            context = card.get_text(' ', strip=True)
            for name, title, href in (list(linked_slots.values()) if len(linked_slots) > 1 else [slot]):
                href = href or first_href([card], base_url)
                if title == '教师':
                    title = resolve_title(context)
                reason = '' if name_prob(name) >= 0.5 or rare_surname_name(name) else 'unrecognized_name_slot'
                slots.append((FacultyCandidate(name, href or base_url, title, context, reason), name_prob(name)))
        trusted = sum(prob >= 0.5 for _, prob in slots)
        linked = sum(person.url != base_url for person, _ in slots)
        found, review = [], []
        for person, prob in slots:
            structural = (len(cards) >= 4 and trusted >= 3 and linked / len(cards) >= 0.8
                          and person.url != base_url and bool(re.fullmatch(r'[\u4e00-\u9fff]{2,4}', person.name)))
            (found if prob >= 0.5 or structural else review).append(person)
        return found, review

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
    def get_clean_text(root: Tag, *, preserve_content: bool = False) -> str:
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

            if (curr.name in {'script', 'style', 'noscript', 'template'}
                    or (not preserve_content and is_noise_element(curr))):
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
    def extract(cls, html: str, base_url: str, fallback_name: str = "",
                *, preserve_content: bool = False) -> FacultyProfile:
        if not html:
            return FacultyProfile(name=fallback_name, title="教师", email="", full_bio="")

        if DETAIL_MECC_AVAILABLE and DetailMECCExtractor is not None:
            try:
                detail = DetailMECCExtractor.extract_single(
                    html, base_url, fallback_name=fallback_name, preserve_content=preserve_content)
                return FacultyProfile(
                    name=detail.name,
                    title=detail.title,
                    email=detail.email,
                    full_bio=detail.full_bio,
                    emails=detail.emails,
                    contact_emails=detail.contact_emails,
                )
            except Exception as ex:
                log.warning("DetailMECCExtractor.extract_single failed: %s; falling back", ex)

        soup = BeautifulSoup(html, 'html.parser')

        # Decompose universal chrome/footer zones that host institutional emails
        prune_tags = ['script', 'style', 'noscript', 'template'] if preserve_content else PRUNE_TAGS
        for junk in soup(prune_tags):
            junk.decompose()
        for c in soup.find_all(string=lambda text: isinstance(text, Comment)):
            c.extract()
        for junk_class in ([] if preserve_content else ['header', 'footer', 'nav', 'bottom', 'copyright']):
            for el in soup.find_all(class_=lambda c: c and junk_class in c.split()):
                el.decompose()

        # Locate MECC (Minimal Enclosing Content Container)
        mecc = None
        for sel in ([] if preserve_content else cls.CONTENT_SELECTORS):
            mecc = soup.select_one(sel)
            if mecc:
                break
        if not mecc:
            mecc = soup.body or soup

        # Extract text adhering to CSS block layout boundaries
        full_bio = LayoutAwareTokenizer.get_clean_text(mecc, preserve_content=preserve_content)

        # Extract email strictly from the MECC text stream
        emails = [em for em in find_emails(full_bio)
                  if not em.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.css', '.js'))]
        valid_email = emails[0] if emails else ""
        contacts = contact_email_candidates(mecc) if contact_email_candidates is not None else ()

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
            emails=tuple(emails),
            contact_emails=contacts,
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
        dcm: Optional[DomainConventionModel] = None,
        *, preserve_content: bool = False,
    ) -> Tuple[Dict[int, Dict[str, Any]], List[Dict[str, Any]]]:
        soup = BeautifulSoup(html, 'html.parser')
        for tag_name in (['script', 'style', 'noscript', 'template'] if preserve_content else PRUNE_TAGS):
            for el in soup.find_all(tag_name):
                el.decompose()
        for el in ([] if preserve_content else
                   soup.find_all(lambda t: is_noise_element(t) if isinstance(t, Tag) else False)):
            el.decompose()

        results = {}
        unresolved = []

        cand_cards = {}
        for cand in cands_on_page:
            peers = [c for c in cands_on_page if c is not cand]
            cand_cards[id(cand)] = cls._find_exclusive_card_container(soup, cand, peers)

        surname_counts = Counter(c.name[0] for c in cands_on_page if c.name)
        assigned_emails = set()

        card_texts, card_contacts, card_addresses, holders = {}, {}, {}, {}
        for cand in cands_on_page:
            card = cand_cards.get(id(cand))
            card_texts[id(cand)] = (LayoutAwareTokenizer.get_clean_text(card, preserve_content=preserve_content)
                                    if card else "")
            contacts = (list(contact_email_candidates(card))
                        if card is not None and contact_email_candidates is not None else [])
            card_contacts[id(cand)] = {em.lower() for em in contacts}
            # A mailto link may carry an address its visible text does not show.
            addresses = {}
            for em in find_emails(card_texts[id(cand)]) + contacts:
                addresses.setdefault(em.lower(), em)
            card_addresses[id(cand)] = list(addresses.values())
            for em in addresses:
                holders.setdefault(em, set()).add(id(cand))

        def surname_only(em: str, cand: FacultyCandidate) -> bool:
            if dcm is None or not cand.name or surname_counts.get(cand.name[0], 1) < 2:
                return False
            clean_local = re.sub(r'[-_.]', '', em.split('@')[0].lower())
            return any(len(syl) > 1 and clean_local == syl[0] for syl in dcm.readings(cand.name))

        flagged = set()
        for cand in cands_on_page:
            card_text = card_texts[id(cand)]
            card_emails = card_addresses[id(cand)]

            matched_title = cand.title
            if card_text:
                matched_title = next((t for t in TITLE_KEYWORDS if t in card_text), cand.title)

            def flag(status: str, **detail):
                flagged.add(id(cand))
                unresolved.append({"姓名": cand.name, "主页": page_url, "status": status, **detail})

            # Ownership evidence inside this teacher's exclusive card: a contact
            # field (label, label cell, mailto) first, else the spelling of the
            # name. An address spelling a colleague, or only a shared surname,
            # is never guessed at.
            labelled, spelled, unowned = [], [], []
            for em in card_emails:
                key = em.lower()
                own_spelling = is_personal_email_for_scholar(em, cand.name)
                if not own_spelling:
                    other_matches = [p.name for p in cands_on_page
                                     if p is not cand and is_personal_email_for_scholar(em, p.name)]
                    if other_matches:
                        flag("conflicting_card_email", email=em,
                             reason=f"Card for '{cand.name}' contains email '{em}' which matches peer(s) {other_matches}")
                        continue
                if surname_only(em, cand):
                    flag("ambiguous_surname_email", email=em,
                         reason=f"Surname-only email '{em}' on shared page with multiple peers sharing surname '{cand.name[0]}'")
                    continue
                if key in card_contacts[id(cand)]:
                    labelled.append(em)
                elif own_spelling:
                    spelled.append(em)
                elif len(holders.get(key, ())) < 2:
                    unowned.append(em)
                # An unlabelled address on several cards is a public contact.

            owned = labelled or spelled
            repeated = [em for em in owned if len(holders.get(em.lower(), ())) > 1]
            for em in repeated:
                flag("ambiguous_shared_email", email=em)
            owned = [em for em in owned if em not in repeated]

            email = ""
            if len(owned) == 1:
                email = owned[0]
                assigned_emails.add(email.lower())
            elif len(owned) > 1:
                flag("ambiguous_email_assignment", matches=owned)
            elif unowned and id(cand) not in flagged:
                # An address stands in this teacher's card with no evidence
                # of whose it is: a person decides, not the no-email sheet.
                flag("ambiguous_email_assignment", matches=unowned)

            results[id(cand)] = {
                "email": email,
                "title": matched_title,
                "bio": card_text
            }

        # Check page-level emails outside candidate exclusive cards
        all_page_emails = [em.lower() for em in find_emails(
            LayoutAwareTokenizer.get_clean_text(soup, preserve_content=preserve_content))]
        uncontained_emails = [em for em in dict.fromkeys(all_page_emails) if em not in holders]
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
                if not results[id(mc)]["email"] and id(mc) not in flagged and u_em not in assigned_emails:
                    if surname_only(u_em, mc):
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
        verified_profile_urls: Optional[Set[str]] = None,
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
                cluster_details = DetailMECCExtractor.extract_cluster(
                    successful_pages, scoped_urls=verified_profile_urls)
                details_by_url = {d.url: d for d in cluster_details}
            except Exception as ex:
                log.warning("DetailMECCExtractor.extract_cluster failed: %s; falling back", ex)
                details_by_url = {}

        # Fallback to single page extractor if cluster extraction was not used
        single_profiles = {}
        if not details_by_url:
            for cand, html, status in fetch_results:
                if status == "ok" and html:
                    single_profiles[cand.url] = FacultyProfileExtractor.extract(
                        html, cand.url, fallback_name=cand.name,
                        preserve_content=cand.url in (verified_profile_urls or ())
                    )

        # detail-df: an address on several sibling profile pages (a template
        # block such as '地址… 邮箱：college@…', or a colleague quoted) is
        # evidence for nobody, labelled or not, unless it spells exactly one
        # of the teachers whose pages show it.
        page_holders: Dict[str, Dict[str, List[str]]] = {}
        for cand, _, status in fetch_results:
            prof = details_by_url.get(cand.url) or single_profiles.get(cand.url)
            for em in {em.lower() for em in (*(prof.emails if prof else ()), *(prof.contact_emails if prof else ()))}:
                page_holders.setdefault(em, {}).setdefault(cand.url, []).append(cand.name)
        sibling_owner: Dict[str, Optional[Tuple[str, str]]] = {}
        for em, holders_by_url in page_holders.items():
            if len(holders_by_url) > 1:
                spelled = [(url, name) for url, names in holders_by_url.items() for name in names
                           if is_personal_email_for_scholar(em, name)]
                sibling_owner[em] = spelled[0] if len(spelled) == 1 else None

        def usable_on_profile(em: str, cand: FacultyCandidate) -> bool:
            key = em.lower()
            return key not in sibling_owner or sibling_owner[key] == (cand.url, cand.name)

        # Step 4: Assemble dual sheets with strict conservation
        url_counts = Counter(cand.url for cand in candidates if cand.url)
        assigned_emails: Set[str] = set()
        dcm = DomainConventionModel() if DomainConventionModel is not None else None

        sheet1_with_email, sheet2_no_email, unresolved = [], [], []
        cand_indices_1, cand_indices_2 = set(), set()

        # Pre-process shared detail pages
        shared_results_by_cand_id = {}
        for cand_url, count in url_counts.items():
            if count > 1 and cand_url and cand_url != base_url:
                cands_on_page = [c for c in candidates if c.url == cand_url]
                page_html = next((h for c, h, s in fetch_results if c.url == cand_url and s == "ok"), None)
                if page_html:
                    p_res, p_unres = cls._resolve_shared_page_candidates(
                        cands_on_page, page_html, cand_url, dcm,
                        preserve_content=cand_url in (verified_profile_urls or ()))
                    shared_results_by_cand_id.update(p_res)
                    unresolved.extend(p_unres)

        # Roster-card contact fields. A card that also names another teacher
        # is not this teacher's alone, so its labels prove nothing.
        roster_names = {c.name for c in candidates if c.name and len(c.name) >= 2}
        card_labels: Dict[int, List[str]] = {}
        label_holders: Dict[str, set] = {}
        card_holders: Dict[str, set] = {}
        for cand in candidates:
            if not cand.card_context or id(cand) in shared_results_by_cand_id:
                continue
            for em in {em.lower() for em in find_emails(cand.card_context)}:
                card_holders.setdefault(em, set()).add(id(cand))
            if any(other != cand.name and other not in cand.name and other in cand.card_context
                   for other in roster_names):
                continue
            card_labels[id(cand)] = labeled_text_emails(cand.card_context)
            for em in card_labels[id(cand)]:
                label_holders.setdefault(em.lower(), set()).add(id(cand))

        for cand_idx, (cand, html, status) in enumerate(fetch_results):
            if status.startswith("fetch_failed"):
                unresolved.append({"姓名": cand.name, "主页": cand.url, "status": status})
                continue
            issues_before = len(unresolved)

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

                # 1. Candidate's own card container (MECC from directory listing):
                #    a labelled contact field first, else the spelling of the name.
                labels = card_labels.get(id(cand), [])
                spelled = [em for em in find_emails(cand.card_context) if is_personal_email_for_scholar(em, cand.name)]
                card_unowned = []
                if labels:
                    for em in labels:
                        if len(label_holders.get(em.lower(), ())) > 1:
                            unresolved.append({"姓名": cand.name, "主页": cand.url,
                                               "status": "ambiguous_shared_email", "email": em})
                    labels = [em for em in labels if len(label_holders.get(em.lower(), ())) < 2]
                    if len(labels) > 1:
                        unresolved.append({"姓名": cand.name, "主页": cand.url,
                                           "status": "ambiguous_email_assignment", "matches": labels})
                    elif labels and labels[0].lower() in assigned_emails:
                        unresolved.append({"姓名": cand.name, "主页": cand.url,
                                           "status": "ambiguous_shared_email", "email": labels[0]})
                    elif labels:
                        email = labels[0]
                        assigned_emails.add(email.lower())
                elif spelled:
                    for em in spelled:
                        if em.lower() not in assigned_emails:
                            email = em
                            assigned_emails.add(em.lower())
                            break
                elif id(cand) in card_labels:
                    # An exclusive card showing an address it does not label or spell.
                    card_unowned = [em for em in dict.fromkeys(find_emails(cand.card_context))
                                    if len(card_holders.get(em.lower(), ())) < 2]

                # 2. Individual 1-to-1 profile page
                # A colleague named on this page and spelled by the address owns it.
                colleagues = ([name for name in roster_names if name != cand.name and name not in cand.name
                               and name in (prof.full_bio or "")] if prof else [])

                def usable(em: str) -> bool:
                    return usable_on_profile(em, cand) and not (
                        colleagues and not is_personal_email_for_scholar(em, cand.name)
                        and any(is_personal_email_for_scholar(em, name) for name in colleagues))

                contacts = tuple(em for em in prof.contact_emails if usable(em)) if prof else ()
                page_emails = [em for em in prof.emails if usable(em)] if prof else []
                if contacts:
                    if len(contacts) != 1 or (email and email.lower() != contacts[0].lower()):
                        unresolved.append({"姓名": cand.name, "主页": cand.url,
                                           "status": "ambiguous_email_assignment", "matches": list(contacts)})
                    elif not email and contacts[0].lower() in assigned_emails:
                        unresolved.append({"姓名": cand.name, "主页": cand.url,
                                           "status": "ambiguous_shared_email", "email": contacts[0]})
                    elif not email:
                        email = contacts[0]
                        assigned_emails.add(email.lower())
                if not email and prof:
                    cand_pool = []
                    if details_by_url and prof.email and usable(prof.email):
                        cand_pool.append(prof.email)
                    for em in page_emails:
                        if em not in cand_pool:
                            cand_pool.append(em)

                    for em in ([] if contacts else cand_pool):
                        if em.lower() not in assigned_emails and is_personal_email_for_scholar(em, cand.name):
                            email = em
                            assigned_emails.add(em.lower())
                            break
                    if page_emails and not email and not contacts:
                        unresolved.append({"姓名": cand.name, "主页": cand.url,
                                           "status": "ambiguous_email_assignment", "matches": page_emails})
                if card_unowned and not email and len(unresolved) == issues_before:
                    unresolved.append({"姓名": cand.name, "主页": cand.url,
                                       "status": "ambiguous_email_assignment", "matches": card_unowned})

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
