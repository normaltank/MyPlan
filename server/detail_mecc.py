"""
Detail MECC: Minimal Enclosing Content Container Extractor for Faculty Profile Detail Pages.
Zero keyword blacklists. Zero AI summarization. Based on:
1. Cross-page template inference (infer_template_nodes_robust) between sibling profile pages.
2. Minimum Enclosing Content Container (MECC) scoping.
3. LayoutAwareTokenizer for physical block-aware text extraction.
4. Cluster-level detail-df email scoping for automatic institutional footer filtering.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

_OPUS = str(Path(__file__).resolve().parent.parent / "Opus")
if _OPUS not in sys.path:
    sys.path.insert(0, _OPUS)

try:
    from template_infer import infer_template_nodes_robust
    from email_matching import DomainConventionModel
    from text_lists import email_spans, _AT as _AT_PATTERN
    _AT_RE = re.compile(_AT_PATTERN)
except ImportError:
    infer_template_nodes_robust = None
    DomainConventionModel = None
    email_spans = None
    _AT_RE = None

BLOCK_ELEMENTS = {
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'tr', 'td', 'th',
    'article', 'section', 'header', 'footer', 'nav', 'table', 'tbody', 'thead', 'br', 'hr'
}

EMAIL_REGEX = re.compile(
    r'(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}'
    r'(?:\.[A-Za-z0-9-]{1,63}){0,8}\.[A-Za-z]{2,24}(?![A-Za-z0-9-])'
)

TITLE_KEYWORDS = sorted(
    ['教授', '副教授', '讲师', '助理教授', '研究员', '副研究员', '助理研究员',
     '师资博士后', '名誉教授', '特聘教授', '讲座教授', '高级实验师', '实验师'],
    key=len,
    reverse=True
)

PRUNE_TAGS = {'script', 'style', 'noscript', 'svg', 'iframe', 'header', 'footer', 'nav'}


def find_emails(text: str) -> List[str]:
    """Extract all valid email addresses in reading order, including obfuscations."""
    if email_spans is not None and _AT_RE is not None:
        return [
            re.sub(r"\s+", "", _AT_RE.sub("@", text[a:b])).strip(".")
            for a, b, _ in email_spans(text)
        ]
    return [m.lower() for m in EMAIL_REGEX.findall(text)]


def resolve_title(header_text: str) -> str:
    """Earliest non-overlapping academic title keyword in header/bio snippet."""
    matches = []
    for kw in TITLE_KEYWORDS:
        pos = 0
        while True:
            idx = header_text.find(kw, pos)
            if idx == -1:
                break
            matches.append((idx, len(kw), kw))
            pos = idx + 1

    if not matches:
        en_kw_map = {
            'associate professor': '副教授',
            'assistant professor': '讲师',
            'lecturer': '讲师',
            'professor': '教授'
        }
        low_header = header_text.lower()
        for en_kw, zh_t in en_kw_map.items():
            pos = 0
            while True:
                idx = low_header.find(en_kw, pos)
                if idx == -1:
                    break
                matches.append((idx, len(en_kw), zh_t))
                pos = idx + 1

    if matches:
        matches.sort(key=lambda x: (x[0], -x[1]))
        last_end = -1
        non_overlapping = []
        for pos, length, kw in matches:
            if pos >= last_end:
                non_overlapping.append(kw)
                last_end = pos + length
        if non_overlapping:
            return non_overlapping[0]

    return "教师"


def clean_email(email_str: str) -> str:
    """Split glued telephone prefix from numeric employee IDs if model is available."""
    if email_str and DomainConventionModel is not None:
        try:
            return DomainConventionModel().split_glued_numeric(email_str)[0]
        except Exception:
            return email_str
    return email_str


FREE_MAIL_DOMAINS = {
    "163.com", "126.com", "yeah.net", "qq.com", "foxmail.com", "gmail.com",
    "sina.com", "sina.cn", "sohu.com", "hotmail.com", "outlook.com",
    "live.com", "yahoo.com", "aliyun.com", "139.com", "189.cn", "icloud.com"
}

def is_personal_email_for_scholar(email: str, scholar_name: str = "") -> bool:
    """
    Determines if an email belongs to a scholar using information-theoretic
    and naming-convention affinity (zero keyword blacklists).
    1. Pure numeric employee ID (e.g. 8843120002@...) -> personal.
    2. Free mail provider (qq.com, 163.com, gmail.com, etc.) -> personal.
    3. If scholar name is known: check pinyin / initials naming convention affinity.
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

def is_institutional_email(email: str, scholar_name: str = "") -> bool:
    """
    Inverse of scholar personal affinity, without keyword blacklists.
    """
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
    # Footers, nav, sidebar, copyright, bottom are strictly noise regardless of 'profile'
    if any(k in combined for k in ('footer', 'nav', 'menu', 'sidebar', 'copyright', 'bottom')):
        return True
    # Header is noise unless explicitly a profile/teacher/author container
    if 'header' in combined:
        if not any(k in combined for k in ('profile', 'teacher', 'author', 'person', 'faculty')):
            return True
    return False


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

            style = curr.get('style', '')
            is_block = curr.name in BLOCK_ELEMENTS or bool(
                style and re.search(r'display\s*:\s*(block|flex|grid|table|list-item|inline-block)', style, re.I)
            )

            if stage == 1:
                if is_block:
                    if tokens and not tokens[-1].endswith('\n'):
                        tokens.append('\n')
                continue

            if is_noise_element(curr):
                continue

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


@dataclass
class ProfileDetail:
    name: str
    url: str
    title: str = "教师"
    email: str = ""
    full_bio: str = ""
    emails: Tuple[str, ...] = ()
    mecc_selector: str = ""
    status: str = "ok"  # 'ok', 'no_profile', 'fetch_failed'


def _find_lca(nodes: List[Tag]) -> Optional[Tag]:
    """Compute the lowest common ancestor of a collection of Tag nodes."""
    if not nodes:
        return None
    if len(nodes) == 1:
        return nodes[0] if nodes[0].name != "[document]" else None

    # Get ancestry chain for each node (from root down to node)
    chains = []
    for n in nodes:
        chain = []
        curr = n
        while curr is not None and isinstance(curr, Tag):
            chain.append(curr)
            curr = curr.parent
        chains.append(list(reversed(chain)))

    common = None
    for level in zip(*chains):
        if all(x is level[0] for x in level):
            common = level[0]
        else:
            break
    return common


class DetailMECCExtractor:
    """
    Extracts scholar details from profile detail pages.
    - Uses cross-page template inference when sibling pages are present to find the exact MECC.
    - Scopes text extraction to MECC with layout-aware boundaries.
    - Applies cluster-level detail-df to eliminate institutional footer emails.
    """

    @classmethod
    def _find_single_mecc(cls, soup: BeautifulSoup, excluded_regions: Optional[Set[int]] = None) -> Tag:
        """Fallback MECC heuristic for a single isolated detail page."""
        body = soup.find('body')
        if not body:
            return soup

        # Candidate containers with text
        candidates = []
        for tag in body.find_all(['div', 'article', 'section', 'td', 'main']):
            if excluded_regions and id(tag) in excluded_regions:
                continue
            if is_noise_element(tag):
                continue
            if any(is_noise_element(p) for p in tag.parents if isinstance(p, Tag)):
                continue
            text = LayoutAwareTokenizer.get_clean_text(tag)
            if len(text) >= 10:
                candidates.append((text, tag))

        if not candidates:
            return body

        # Remove redundant wrappers only when the complete reading text is
        # conserved. A long resume alone can exceed a percentage threshold and
        # still omit its heading, title and contact block.
        candidates.sort(key=lambda x: len(x[0]), reverse=True)
        complete_text = candidates[0][0]
        deepest = candidates[0][1]
        for text, t in candidates[1:]:
            if text == complete_text:
                if any(p is deepest for p in t.parents if isinstance(p, Tag)):
                    deepest = t

        return deepest

    @classmethod
    def _find_cluster_mecc(cls, soup: BeautifulSoup, siblings: List[BeautifulSoup]) -> Tag:
        """Find the MECC using cross-page template inference against sibling pages."""
        if not infer_template_nodes_robust or not siblings:
            return cls._find_single_mecc(soup)

        body = soup.find('body')
        if not body:
            return cls._find_single_mecc(soup)

        template_ids = infer_template_nodes_robust(soup, siblings)

        # Identify informative content leaves (non-template, non-noise text leaves strictly within body)
        content_leaves: List[Tag] = []
        for tag in body.find_all(True):
            if id(tag) in template_ids or is_noise_element(tag):
                continue
            if any(is_noise_element(p) for p in tag.parents if isinstance(p, Tag)):
                continue
            # A leaf with text
            kids = [c for c in tag.children if isinstance(c, Tag)]
            if not kids and tag.get_text(strip=True):
                content_leaves.append(tag)

        if not content_leaves:
            return cls._find_single_mecc(soup, template_ids)

        lca = _find_lca(content_leaves)
        if lca is None or lca.name in ('html', 'body', '[document]'):
            return cls._find_single_mecc(soup)

        # Similarity establishes a location anchor, not permission to discard
        # authored blocks. Numeric contact fields and largely shared academic
        # prose can be template matches while only the name remains variable.
        # Enclose both the anchor and the independently complete reading region.
        # A wholly shared, separate container may be site chrome. Reject it as
        # the primary reading region, while keeping shared descendants inside
        # the chosen profile container intact.
        complete = cls._find_single_mecc(soup, template_ids)
        enclosing = _find_lca([lca, complete])
        if enclosing is None or enclosing.name in ('html', 'body', '[document]'):
            return complete
        return enclosing

    @classmethod
    def extract_single(cls, html: str, page_url: str, fallback_name: str = "") -> ProfileDetail:
        """Extract a single profile page without sibling context."""
        if not html or not html.strip():
            return ProfileDetail(name=fallback_name, url=page_url, status="no_profile")

        soup = BeautifulSoup(html, 'lxml')
        mecc = cls._find_single_mecc(soup)
        full_bio = LayoutAwareTokenizer.get_clean_text(mecc)
        emails = [
            em for em in find_emails(full_bio)
            if not em.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.css', '.js'))
        ]

        title = resolve_title(full_bio[:500])

        name = fallback_name
        if not name:
            # Try to find name in h1, h2 or title
            for heading in mecc.find_all(['h1', 'h2', 'h3']):
                h_txt = heading.get_text(strip=True)
                if 2 <= len(h_txt) <= 10 and all('\u4e00' <= ch <= '\u9fff' or ch.isspace() for ch in h_txt):
                    name = re.sub(r'\s+', '', h_txt)
                    break

        target_name = name or fallback_name
        personal_emails = [em for em in emails if is_personal_email_for_scholar(em, target_name)]
        valid_email = clean_email(personal_emails[0]) if personal_emails else ""

        return ProfileDetail(
            name=name or fallback_name,
            url=page_url,
            title=title,
            email=valid_email,
            full_bio=full_bio,
            emails=tuple(emails),
            status="ok"
        )

    @classmethod
    def extract_cluster(cls, pages: List[Tuple[str, str, str]]) -> List[ProfileDetail]:
        """
        Extract a batch of sibling profile pages from the same department/college.
        pages: List of (url, html, fallback_name)
        Applies:
        1. Sibling cross-page template inference for robust MECC boundary.
        2. detail-df filter across all extracted emails.
        """
        if not pages:
            return []

        # Parse soups
        parsed = []
        for url, html, fb_name in pages:
            if html and html.strip():
                soup = BeautifulSoup(html, 'lxml')
                parsed.append((url, html, fb_name, soup))
            else:
                parsed.append((url, html, fb_name, None))

        soups = [s for _, _, _, s in parsed if s is not None]
        details: List[ProfileDetail] = []

        for url, html, fb_name, soup in parsed:
            if soup is None:
                details.append(ProfileDetail(name=fb_name, url=url, status="fetch_failed"))
                continue

            siblings = [s for s in soups if s is not soup]
            mecc = cls._find_cluster_mecc(soup, siblings) if siblings else cls._find_single_mecc(soup)
            full_bio = LayoutAwareTokenizer.get_clean_text(mecc)
            raw_emails = [
                em for em in find_emails(full_bio)
                if not em.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.css', '.js'))
            ]

            title = resolve_title(full_bio[:500])

            details.append(ProfileDetail(
                name=fb_name,
                url=url,
                title=title,
                full_bio=full_bio,
                emails=tuple(raw_emails),
                status="ok"
            ))

        # Cluster-level detail-df filter
        owners: Dict[str, Set[str]] = {}
        for d in details:
            if d.status == "ok":
                for em in d.emails:
                    owners.setdefault(em.lower(), set()).add(d.url)

        for d in details:
            if d.status == "ok":
                # 1. Filter out cross-page duplicate emails (detail-df)
                valid = [em for em in d.emails if len(owners.get(em.lower(), set())) < 2]
                # 2. Check scholar personal affinity
                personal = [em for em in valid if is_personal_email_for_scholar(em, d.name)]
                d.email = clean_email(personal[0]) if personal else ""

        return details
