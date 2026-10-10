"""
Faculty Extractor: Deep Module for University Faculty Directory & Profile Harvesting.
Zero keyword blacklists. Based on:
1. DOM Isomorphic Subtree Clustering (Tree signature & structural clustering)
2. Layout-Aware Tokenization (Preserves CSS Block Formatting Context)
3. Minimal Enclosing Content Container (MECC Scoping)
"""

import re
from dataclasses import dataclass, replace
from typing import List, Dict, Optional, Set, Tuple, Any
from collections import Counter
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Tag, Comment
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

# The algorithm layer is required: without it there is no rule for whose
# address is whose, so a missing layer fails the import instead of running on
# looser stand-ins.
from iso_mining import (VirtualNode, first_href, mine_records, primary_tokens,  # noqa: E402
                        name_prob, rare_surname_name, strip_invisible)
from email_matching import DomainConventionModel  # noqa: E402
L2_AVAILABLE = True  # kept for worker_selfcheck; the import above raises otherwise

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
    from server.titles import TITLE_KEYWORDS, resolve_title, select_title
except ImportError:
    from titles import TITLE_KEYWORDS, resolve_title, select_title

TEMPLATE_NAME_PRIOR = 0.7
_NAME_SHAPED = re.compile(r"[\u4e00-\u9fff]{2,4}|[\u4e00-\u9fff]{1,8}(?:[·•][\u4e00-\u9fff]{1,8}){1,3}")

# '李星教授介绍': a 2-4 character name, a title keyword, then an introduction suffix.
_INTRODUCED_NAME = re.compile(
    r"^([\u4e00-\u9fff]{2,4}?)(" + "|".join(map(re.escape, TITLE_KEYWORDS))
    + r")(?:个人简介|简介|介绍|个人主页|主页)$")

# '钟俊华 讲师 (金融学)': text in brackets annotates the person before it
# (subject, role, '兼任'); _clean_name already drops it from a whole label.
_BRACKETED = re.compile(r"[（(][^（()）]*[）)]")


def _annotation_token(token: str, text: str) -> bool:
    """The token's first occurrence, the one a leading token is read from, is
    inside a bracketed annotation (a summary may repeat the word later)."""
    compact = re.sub(r"\s+", "", text)
    first = compact.find(token)
    return first >= 0 and any(m.start() < first and first + len(token) < m.end()
                              for m in _BRACKETED.finditer(compact))

# One definition of "this address spells this teacher" and of address reading
# for every path (profile pages, shared pages, roster cards, OCR biographies).
from server.detail_mecc import (DetailMECCExtractor, ProfileDetail, LayoutAwareTokenizer,  # noqa: E402,F401
                                contact_email_candidates, labeled_text_emails, explicit_title_candidates,
                                mailto_address_conflicts, find_emails,
                                is_institutional_email, is_personal_email_for_scholar)
DETAIL_MECC_AVAILABLE = True

_DEFAULT_L1_EXECUTOR = None

_PUBLIC_SECOND_LEVEL = {"edu", "ac", "com", "org", "net", "gov"}


def _organisation_domain(host: str) -> str:
    """'hxhgxy.sqnu.edu.cn' -> 'sqnu.edu.cn'; 'jszy.whu.edu.cn' -> 'whu.edu.cn'."""
    labels = (host or "").lower().split(":")[0].split(".")
    keep = 3 if len(labels) >= 3 and labels[-2] in _PUBLIC_SECOND_LEVEL and len(labels[-1]) == 2 else 2
    return ".".join(labels[-keep:])


def on_university_domain(email: str, page_url: str) -> bool:
    """The address is hosted by the organisation that publishes the page."""
    domain = email.lower().rpartition("@")[2]
    org = _organisation_domain(urlparse(page_url).netloc)
    return bool(org) and (domain == org or domain.endswith("." + org))


def prefer_university_email(emails, page_url: str):
    """Of several addresses that are all this person's, the one on the
    university's own domain; None when that does not single one out."""
    hosted = [em for em in emails if on_university_domain(em, page_url)]
    return hosted[0] if len(hosted) == 1 else None


def spells_other_person(email: str, own_name: str, text: str) -> bool:
    """The address spells a name other than ``own_name`` that the text shows
    (a co-author, a student, a secretary). A numeric address spells nobody."""
    local = re.sub(r"[-_.]", "", email.lower().partition("@")[0])
    if not local or local.isdigit():
        return False
    own = re.sub(r"\s+", "", own_name or "")
    seen = set()
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text or ""):
        for size in (2, 3):
            for start in range(len(run) - size + 1):
                word = run[start:start + size]
                if word in seen or word in own or own in word:
                    continue
                seen.add(word)
                if name_prob(word) >= 0.5 and is_personal_email_for_scholar(email, word):
                    return True
    return False


def owned_by_position(email: str, own_name: str, text: str) -> bool:
    """badr 2026-10-09: inside a person's own verified content, the one address
    shown there is theirs whatever its spelling, unless it spells someone else
    named in that content, or is a bare abbreviation (zz@, jwc@) that reads as
    an office rather than a person."""
    local = re.sub(r"[-_.]", "", email.lower().partition("@")[0])
    if not local.isdigit() and len(re.sub(r"\d", "", local)) <= 3 and not is_personal_email_for_scholar(
            email, own_name):
        return False
    return not spells_other_person(email, own_name, text)


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


@dataclass(frozen=True)
class FacultyCandidate:
    name: str
    url: str
    title: str = "教师"
    card_context: str = ""
    name_review_reason: str = ""
    mailto_conflicts: Tuple[Tuple[str, ...], ...] = ()
    table_row: Optional[dict] = None

@dataclass(frozen=True)
class FacultyProfile:
    name: str
    title: str
    email: str
    full_bio: str
    emails: tuple = ()          # every address inside the content container, in order
    contact_emails: tuple = ()
    status: str = 'ok'


def html_table_rows(soup: BeautifulSoup) -> dict[int, dict]:
    """Read row-table structure, without deciding which staff roles to keep.

    Only tables with an explicit 姓名 column participate; name grids and nested
    member cards keep their existing path. Keys refer to nodes in this soup.
    A full-width cell is a section heading, and repeated column headers keep
    the current section. Rowspans carry a shared remark into each covered row.
    """
    result = {}

    def span(cell, attr):
        value = str(cell.get(attr, "1"))
        return max(1, int(value)) if value.isdigit() else 1

    for table_index, table in enumerate(soup.find_all("table"), 1):
        rows = [row for row in table.find_all("tr") if row.find_parent("table") is table]
        headers = [row for row in rows
                   if "姓名" in [re.sub(r"\s+", "", cell.get_text())
                                 for cell in row.find_all(["td", "th"], recursive=False)]
                   and len(row.find_all(["td", "th"], recursive=False)) > 1]
        if not headers:
            continue
        width = sum(span(cell, "colspan") for cell in headers[0].find_all(["td", "th"], recursive=False))
        header, section, pending = None, None, {}
        for row_index, row in enumerate(rows, 1):
            cells = row.find_all(["td", "th"], recursive=False)
            texts = [LayoutAwareTokenizer.get_clean_text(cell, preserve_content=True) for cell in cells]
            compact = [re.sub(r"\s+", "", text) for text in texts]
            if "姓名" in compact and len(cells) > 1:
                header = [label for label, cell in zip(compact, cells)
                          for _ in range(span(cell, "colspan"))]
                width = len(header)
                pending = {}
                result[id(row)] = {"kind": "header"}
                continue
            if len(cells) == 1 and span(cells[0], "colspan") >= width:
                if not texts[0]:
                    continue  # An empty spacer does not end the staff section.
                section = texts[0]
                pending = {}
                result[id(row)] = {"kind": "section"}
                continue
            if header is None:
                continue
            grid = {column: cell for column, (cell, _) in pending.items()}
            pending = {column: (cell, remaining - 1) for column, (cell, remaining) in pending.items()
                       if remaining > 1}
            column = 0
            for cell in cells:
                while column in grid:
                    column += 1
                for offset in range(span(cell, "colspan")):
                    grid[column + offset] = cell
                    if span(cell, "rowspan") > 1:
                        pending[column + offset] = (cell, span(cell, "rowspan") - 1)
                column += span(cell, "colspan")
            fields = {}
            for column, cell in sorted(grid.items()):
                label = header[column] if column < len(header) else ""
                key = label if label and label not in fields else f"column_{column + 1}"
                fields[key] = LayoutAwareTokenizer.get_clean_text(cell, preserve_content=True)
            # Several 姓名 columns mean several people per row: no single name cell.
            name_cell = grid.get(header.index("姓名")) if header.count("姓名") == 1 else None
            result[id(row)] = {"kind": "person", "name_cell": name_cell,
                               "evidence": {"table_index": table_index, "row_index": row_index,
                                            "section": section, "cells": fields}}
    return result

class FacultyDirectoryExtractor:
    """
    Interface for extracting faculty members from directory/list pages
    using DOM Isomorphic Subtree Clustering.
    """

    @staticmethod
    def links_outside_cards(html: str, base_url: str, selector: str) -> list[str]:
        """Selected text leaves cannot discard the link wrapping their card.

        The selector must be corrected by reconnaissance; never widen the
        extraction boundary or assign an ancestor URL to a person implicitly.
        """
        urls = []
        for card in BeautifulSoup(html, 'html.parser').select(selector):
            if card.name == 'a':
                continue
            anchor = card.find_parent('a', href=True)
            if anchor is None or not card.get_text(strip=True):
                continue
            href = anchor['href'].strip()
            if not href or href.startswith(('#','javascript:','mailto:')):
                continue
            url = urljoin(base_url, href)
            parsed = urlparse(url)
            if parsed.fragment and parsed._replace(fragment='').geturl() == urlparse(base_url)._replace(fragment='').geturl():
                continue  # Same-document navigation keeps its existing path.
            if urlparse(url).scheme in ('http','https') and url not in urls:
                urls.append(url)
        return urls

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
        # A news-list roster may put the publish date inside the name link
        # ('2026-05-14班爱玲（副教授）'); the date is not part of the name.
        raw = re.sub(r'^\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}日?\s*', '', raw)
        # Match Chinese or ASCII parentheses: （教授）, (副教授), （博导/教授）
        paren_match = re.search(r'[（\(]([^）\)]+)[）\)]', raw)
        if paren_match:
            paren_content = paren_match.group(1).strip()
            title_found = resolve_title(paren_content)
            raw = re.sub(r'[（\(][^）\)]+[）\)]', '', raw).strip()

        # Strip bracket prefixes like [校示范课教师], 【教授】
        raw = re.sub(r'^[\[【][^\]】]+[\]】]\s*', '', raw).strip()

        # Handle title - name or title: name separators like '校示范课教师-郑佳生', '院长：李旭明'
        sep_match = re.split(r'[-－:：]', raw)
        if len(sep_match) >= 2:
            potential_title, potential_name = sep_match[0].strip(), sep_match[-1].strip()
            if 2 <= len(potential_name) <= 6 and all('\u4e00' <= ch <= '\u9fff' for ch in potential_name):
                raw = potential_name
                prefix_title = resolve_title(potential_title)
                if prefix_title != '教师':
                    title_found = prefix_title

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
                card = "\n".join(LayoutAwareTokenizer.get_clean_text(n, preserve_content=True) for n in r)
                href = first_href(r, base_url)
                section = ""
            name, title = cls._clean_name(raw)
            if not name:
                continue
            # The title vocabulary only identifies a title standing in the name slot.
            if needs_review and (name in TITLE_KEYWORDS or not rare_surname_name(name)):
                continue
            title = select_title(explicit=explicit_title_candidates(card), card_title=title,
                                 body_title=resolve_title(card)).title
            if title == '教师' and not explicit_title_candidates(card):
                title = resolve_title(section)
            url = href or base_url
            node_key = href if href else id(head)
            if (name, node_key) in seen:
                continue
            seen.add((name, node_key))
            contact_conflicts = tuple(dict.fromkeys(
                conflict for node in r if isinstance(node, Tag)
                for conflict in mailto_address_conflicts(node)))
            candidate = FacultyCandidate(name=name, url=url, title=title, card_context=card,
                                         mailto_conflicts=contact_conflicts)
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
    def _name_slot_path(cls, scope: Tag, name: str) -> Optional[tuple]:
        """Child-index path from ``scope`` to the innermost element holding ``name``."""
        matches = [node for node in [scope, *scope.find_all(True)]
                   if cls._clean_name(node.get_text(' ', strip=True))[0] == name]
        if not matches:
            return None
        node = max(matches, key=lambda item: len(list(item.parents)))
        steps = []
        while node is not scope:
            parent = node.parent
            siblings = parent.find_all(node.name, recursive=False)
            steps.append((node.name, next(i for i, item in enumerate(siblings) if item is node)))
            node = parent
        return tuple(reversed(steps))

    @staticmethod
    def _node_at_path(scope: Tag, path: tuple) -> Optional[Tag]:
        node = scope
        for name, index in path:
            children = node.find_all(name, recursive=False)
            if index >= len(children):
                return None
            node = children[index]
        return node

    @classmethod
    def profile_introduction_links(cls, html: str, base_url: str, selector: str) -> list[str]:
        """Selected links explicitly labelled as a person's introduction.

        Reuse the field grammar used by the card reader. This is conflicting
        evidence for a directory plan, not proof of what the linked page holds;
        the caller must stop and verify its role rather than rewrite the plan.
        Bare names, institutions and generic detail links stay unclassified.
        """
        links = []
        for node in BeautifulSoup(html, 'html.parser').select(selector):
            anchors = ([node] if node.name == 'a' else []) + node.find_all('a')
            for anchor in anchors:
                label = re.sub(r'\s+', '', strip_invisible(anchor.get_text(' ', strip=True)))
                href = first_href([anchor], base_url)
                if (_INTRODUCED_NAME.fullmatch(label) and href and href not in links
                        and urlparse(href).scheme in ('http', 'https')):
                    links.append(href)
        return links

    @classmethod
    def _verified_card_candidates(cls, html: str, base_url: str, selector: str):
        """Account for each verified card; full-page mining cannot override its boundary."""
        soup = BeautifulSoup(html, 'html.parser')
        cards = soup.select(selector)
        table_rows = html_table_rows(soup)
        if cards and all(card.name == 'img' for card in cards):
            return cls.extract_with_review(html, base_url)
        entries = []
        for card in cards:
            row = card if card.name == 'tr' else card.find_parent('tr')
            table = table_rows.get(id(row), {})
            if table.get('kind') in ('header', 'section'):
                continue
            # A labelled name column is stronger than another cell that looks
            # like a surname (e.g. a department or an office contact).
            name_scope = table.get('name_cell') if card is row else None
            name_scope = name_scope if name_scope is not None else card
            anchors = ([name_scope] if name_scope.name == 'a' else []) + name_scope.find_all('a')
            labels = []
            for anchor in anchors:
                raw = anchor.get_text(' ', strip=True)
                if raw:
                    href = first_href([anchor], base_url)
                    labels.append((raw, href))
                    labels.extend((token, href) for token in primary_tokens([anchor])
                                  if not _annotation_token(token, raw))
            scope_text = name_scope.get_text(' ', strip=True)
            if name_scope is not card:
                labels.append((scope_text, None))
            labels.extend((token, None) for token in primary_tokens([name_scope])
                          if not _annotation_token(token, scope_text))
            # One table cell may split a name across inline runs ('<span>杨</span>
            # <span>曼</span>'); its leaf strings alone expose only the surname.
            labels.extend((cell.get_text(' ', strip=True), None)
                          for cell in name_scope.find_all(['td', 'th'])
                          if len(list(cell.stripped_strings)) > 1)
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
            path = cls._name_slot_path(name_scope, slot[0]) if slot else None
            entries.append((card, table, name_scope, labels, linked_slots, slot, path))
        # Cards matched by one verified selector share a layout. Where the
        # recognised names sit (e.g. the <h2> inside a link that also wraps a
        # summary) is where an unrecognised card keeps its name, so that slot is
        # read before the card's whole first label.
        paths = Counter(path for *_, path in entries if path is not None)
        agreed = next((path for path, support in paths.most_common(1) if support >= 3), None)
        slots = []
        for card, table, name_scope, labels, linked_slots, slot, _ in entries:
            tries = []
            if slot is None and agreed is not None:
                node = cls._node_at_path(name_scope, agreed)
                if node is not None:
                    anchor = node if node.name == 'a' else node.find_parent('a')
                    inside = anchor is not None and (
                        anchor is name_scope or any(item is name_scope for item in anchor.parents))
                    owner = anchor if inside else node
                    tries.append((node.get_text(' ', strip=True), first_href([owner], base_url)))
            if slot is None and labels:
                tries.append(labels[0])
            for raw, href in tries:
                name, title = cls._clean_name(raw)
                if name and name not in TITLE_KEYWORDS and len(name) <= 40:
                    slot = (name, title, href)
                    break
            if slot is None:
                continue
            context = LayoutAwareTokenizer.get_clean_text(card, preserve_content=True)
            for name, title, href in (list(linked_slots.values()) if len(linked_slots) > 1 else [slot]):
                href = href or first_href([name_scope], base_url)
                title = select_title(explicit=explicit_title_candidates(context), card_title=title,
                                     body_title=resolve_title(context)).title
                reason = '' if name_prob(name) >= 0.5 or rare_surname_name(name) else 'unrecognized_name_slot'
                # A rare surname standing in a column the table itself labels
                # 姓名 is a name (badr 2026-10-09); elsewhere a person confirms it.
                labelled_name = name_scope is not card and rare_surname_name(name)
                slots.append((FacultyCandidate(name, href or base_url, title, context, reason,
                                                mailto_address_conflicts(card), table.get('evidence')),
                              name_prob(name), labelled_name))
        trusted = sum(prob >= 0.5 for _, prob, _ in slots)
        linked = sum(person.url != base_url for person, _, _ in slots)
        found, review = [], []
        for person, prob, labelled_name in slots:
            structural = (len(cards) >= 4 and trusted >= 3 and linked / len(cards) >= 0.8
                          and person.url != base_url and bool(re.fullmatch(r'[\u4e00-\u9fff]{2,4}', person.name)))
            (found if prob >= 0.5 or structural or labelled_name else review).append(person)
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
            if name_prob(clean_name) < 0.35:
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

                card_text = (LayoutAwareTokenizer.get_clean_text(item['card'], preserve_content=True)
                             if item['card'] else "")
                parent_block = item['card'].find_parent(class_=re.compile(r'block|section|group|list', re.I)) or item['card']
                block_title_el = parent_block.find(class_=re.compile(r'title|heading|caption', re.I)) if parent_block else None
                block_title = block_title_el.get_text(strip=True) if block_title_el else ""
                context_text = card_text
                matched_title = select_title(explicit=explicit_title_candidates(card_text),
                                             body_title=resolve_title(card_text)).title
                if matched_title == '教师' and not explicit_title_candidates(card_text):
                    matched_title = resolve_title(block_title)
                contact_conflicts = mailto_address_conflicts(item['card']) if item['card'] is not None else ()

                if url in seen_urls:
                    for idx_c, existing_c in enumerate(candidates):
                        if existing_c.url == url:
                            upgrade = existing_c.title == '教师' and matched_title != '教师'
                            candidates[idx_c] = replace(
                                existing_c, title=matched_title if upgrade else existing_c.title,
                                card_context=context_text if upgrade else existing_c.card_context,
                                mailto_conflicts=tuple(dict.fromkeys(
                                    (*existing_c.mailto_conflicts, *contact_conflicts))))
                            break
                    continue

                seen_urls.add(url)

                candidates.append(FacultyCandidate(
                    name=name,
                    url=url,
                    title=matched_title,
                    card_context=card_text,
                    mailto_conflicts=contact_conflicts,
                ))

        return candidates


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
                    status=detail.status,
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
        email_text = LayoutAwareTokenizer.get_clean_text(
            mecc, preserve_content=preserve_content, separate_fields=True)
        emails = [em for em in find_emails(email_text)
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

        title = select_title(explicit=explicit_title_candidates(full_bio),
                             body_title=resolve_title(full_bio)).title

        return FacultyProfile(
            name=name,
            title=title,
            email=valid_email,
            full_bio=full_bio,
            emails=tuple(emails),
            contact_emails=contacts,
            status='mailto_address_conflict' if mailto_address_conflicts(mecc) else 'ok',
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
            email_text = (LayoutAwareTokenizer.get_clean_text(
                card, preserve_content=preserve_content, separate_fields=True) if card else '')
            for em in find_emails(email_text) + contacts:
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

            title_choice = select_title(explicit=(explicit_title_candidates(card_text)
                                                  or explicit_title_candidates(cand.card_context)),
                                        card_title=cand.title, body_title=resolve_title(card_text))
            matched_title = title_choice.title
            if title_choice.conflicts:
                unresolved.append({'姓名': cand.name, '主页': page_url,
                                   'status': 'title_field_conflict', 'matches': list(title_choice.conflicts)})

            def flag(status: str, **detail):
                flagged.add(id(cand))
                unresolved.append({"姓名": cand.name, "主页": page_url, "status": status, **detail})

            contact_conflicts = mailto_address_conflicts(cand_cards[id(cand)]) if cand_cards[id(cand)] is not None else ()
            if contact_conflicts:
                flag('mailto_address_conflict', matches=list(dict.fromkeys(
                    email for conflict in contact_conflicts for email in conflict)))

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
                "email": '' if contact_conflicts else email,
                "title": matched_title,
                "bio": card_text
            }

        # Check page-level emails outside candidate exclusive cards
        all_page_emails = [em.lower() for em in find_emails(
            LayoutAwareTokenizer.get_clean_text(soup, preserve_content=preserve_content, separate_fields=True))]
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
        seen_cand_urls = {}
        for cand in candidates:
            if cand.url and cand.url != base_url and cand.url not in ('', '/'):
                cand_id = ('url', cand.name, cand.url)
                if cand_id in seen_cand_urls:
                    index = seen_cand_urls[cand_id]
                    prior = unique_candidates[index]
                    unique_candidates[index] = replace(prior, mailto_conflicts=tuple(dict.fromkeys(
                        (*prior.mailto_conflicts, *cand.mailto_conflicts))))
                    continue
                seen_cand_urls[cand_id] = len(unique_candidates)
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
        # How an address was chosen when its spelling did not decide it, and
        # the person's other addresses: audit evidence, not a blocker.
        email_alternates: List[Dict[str, Any]] = []

        for cand in candidates:
            if cand.mailto_conflicts:
                unresolved.append({'姓名': cand.name, '主页': cand.url,
                                   'status': 'mailto_address_conflict', 'matches': list(dict.fromkeys(
                                       email for conflict in cand.mailto_conflicts for email in conflict))})
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
                contact_conflict = prof is not None and getattr(prof, 'status', '') == 'mailto_address_conflict'
                if contact_conflict:
                    unresolved.append({'姓名': cand.name, '主页': cand.url,
                                       'status': 'mailto_address_conflict', 'matches': list(prof.contact_emails)})

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
                    return not contact_conflict and usable_on_profile(em, cand) and not (
                        colleagues and not is_personal_email_for_scholar(em, cand.name)
                        and any(is_personal_email_for_scholar(em, name) for name in colleagues))

                contacts = tuple(em for em in prof.contact_emails if usable(em)) if prof else ()
                page_emails = [em for em in prof.emails if usable(em)] if prof else []
                preferred = prefer_university_email(contacts, cand.url) if len(contacts) > 1 else None
                if preferred and not email and preferred.lower() not in assigned_emails:
                    # Several labelled addresses of this person: the university's own.
                    email = preferred
                    assigned_emails.add(email.lower())
                    email_alternates.append({"姓名": cand.name, "主页": cand.url, "邮箱": email,
                                             "others": [em for em in contacts if em != email],
                                             "basis": "labelled_university_domain"})
                    contacts = ()
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
                        free = [em for em in dict.fromkeys(page_emails) if em.lower() not in assigned_emails]
                        positional = ([em for em in free if owned_by_position(em, cand.name, prof.full_bio)]
                                      if cand.url in (verified_profile_urls or ()) else [])
                        chosen = (positional[0] if len(positional) == 1 and len(free) == 1
                                  else prefer_university_email(positional, cand.url)
                                  if len(positional) == len(free) else None)
                        if chosen:
                            email = chosen
                            assigned_emails.add(email.lower())
                            email_alternates.append({"姓名": cand.name, "主页": cand.url, "邮箱": email,
                                                     "others": [em for em in free if em != email],
                                                     "basis": "position_in_verified_profile"})
                        else:
                            unresolved.append({"姓名": cand.name, "主页": cand.url,
                                               "status": "ambiguous_email_assignment", "matches": page_emails})
                if card_unowned and not email and len(unresolved) == issues_before:
                    # The one address in this person's own roster card is theirs by position.
                    free = [em for em in card_unowned if em.lower() not in assigned_emails]
                    if (len(free) == len(card_unowned) == 1
                            and owned_by_position(free[0], cand.name, cand.card_context)):
                        email = free[0]
                        assigned_emails.add(email.lower())
                        email_alternates.append({"姓名": cand.name, "主页": cand.url, "邮箱": email,
                                                 "others": [], "basis": "position_in_roster_card"})
                    else:
                        unresolved.append({"姓名": cand.name, "主页": cand.url,
                                           "status": "ambiguous_email_assignment", "matches": card_unowned})

                if email and dcm is not None:
                    try:
                        email = dcm.split_glued_numeric(email)[0]
                    except Exception:
                        pass

                if contact_conflict and email:
                    assigned_emails.discard(email.lower())
                    email = ''
                bio = prof.full_bio if prof else ""
                title_choice = select_title(explicit=(explicit_title_candidates(bio)
                                                      or explicit_title_candidates(cand.card_context)),
                                            card_title=cand.title, body_title=prof.title if prof else '教师')
                title = title_choice.title
                if title_choice.conflicts:
                    unresolved.append({'姓名': cand.name, '主页': cand.url,
                                       'status': 'title_field_conflict', 'matches': list(title_choice.conflicts)})

            if cand.mailto_conflicts:
                if email:
                    assigned_emails.discard(email.lower())
                email = ''
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
        records_conserved = (
            len(candidates) > 0
            and (total_count == len(candidates))
            and not (cand_indices_1 & cand_indices_2)
            and len(overlap) == 0
            and not has_internal_dups
        )
        is_conserved = records_conserved and len(unresolved) == 0
        delivery_ready = is_conserved and len(unresolved) == 0 and total_count > 0

        return {
            "total_count": total_count,
            "sheet1_with_email": sheet1_with_email,
            "sheet2_no_email": sheet2_no_email,
            "unresolved": unresolved,
            "is_conserved": is_conserved,
            # The sheets themselves balance; a caller may settle ``unresolved`` by evidence-bound review.
            "records_conserved": records_conserved,
            "delivery_ready": delivery_ready,
            "email_alternates": email_alternates,
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
