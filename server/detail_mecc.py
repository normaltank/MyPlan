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
from urllib.parse import unquote

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from server.dom_regions import is_noise_element

try:
    from server.titles import resolve_title
except ImportError:
    from titles import resolve_title

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

PRUNE_TAGS = {'script', 'style', 'noscript', 'svg', 'iframe', 'header', 'footer', 'nav'}


def find_emails(text: str) -> List[str]:
    """Extract all valid email addresses in reading order, including obfuscations."""
    if email_spans is not None and _AT_RE is not None:
        return [
            re.sub(r"\s+", "", _AT_RE.sub("@", text[a:b])).strip(".")
            for a, b, _ in email_spans(text)
        ]
    return [m.lower() for m in EMAIL_REGEX.findall(text)]


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

            style = curr.get('style', '')
            is_block = curr.name in BLOCK_ELEMENTS or bool(
                style and re.search(r'display\s*:\s*(block|flex|grid|table|list-item|inline-block)', style, re.I)
            )

            if stage == 1:
                if is_block:
                    if tokens and not tokens[-1].endswith('\n'):
                        tokens.append('\n')
                continue

            if (curr.name in {'script', 'style', 'noscript', 'template'}
                    or (not preserve_content and is_noise_element(curr))):
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
    contact_emails: Tuple[str, ...] = ()  # addresses explicitly labeled inside this person's content
    mecc_selector: str = ""
    status: str = "ok"  # 'ok', 'no_profile', 'fetch_failed'


_CONTACT_FIELD = re.compile(r"^(?:(?:电子|联系)?(?:邮箱|邮件)|e-?mail)(?:\s*[:：]\s*|\s+|$)", re.I)
# A table cell standing beside the value is a label only when it holds nothing else.
_CONTACT_LABEL_CELL = re.compile(r"^(?:(?:电子|联系)?(?:邮箱|邮件|信箱)(?:地址)?|e-?mail(?:\s*address)?)\s*[:：]?$", re.I)


# A label right before an address. It must start a field: '学院公共邮箱' or
# '课题组邮箱' qualify a mailbox as someone else's and are not this label.
_TEXT_CONTACT_LABEL = re.compile(
    r"(?<![一-鿿A-Za-z])(?:个人|联系|工作)?(?:电子)?(?:邮箱|邮件|信箱|e-?mail)(?:地址|\s*address)?\s*[:：]?\s*$",
    re.I)
_CONTACT_JOINER = re.compile(r"^\s*(?:[;；,，、/]|或|or)\s*$", re.I)


def labeled_text_emails(text: str) -> List[str]:
    """Addresses a contact label introduces in running text ('电话：… 邮箱：a@x；b@y').

    The text form of contact-field evidence: an address counts only when a
    label, or a labelled address and a list separator, stands right before it.
    """
    if not text:
        return []
    if email_spans is not None and _AT_RE is not None:
        spans = [(a, b, re.sub(r"\s+", "", _AT_RE.sub("@", text[a:b])).strip("."))
                 for a, b, _ in email_spans(text)]
    else:
        spans = [(m.start(), m.end(), m.group(0).lower()) for m in EMAIL_REGEX.finditer(text)]
    found, previous_end, previous_labeled = [], 0, False
    for start, end, address in spans:
        labeled = bool(_TEXT_CONTACT_LABEL.search(text[max(0, start - 24):start])) or (
            previous_labeled and _CONTACT_JOINER.match(text[previous_end:start]) is not None)
        if labeled and address.lower() not in {item.lower() for item in found}:
            found.append(address)
        previous_end, previous_labeled = end, labeled
    return found


def _mailto_address(node: Tag) -> List[str]:
    target = str(node.get('href', '')).strip()
    return find_emails(unquote(target[len('mailto:'):].split('?', 1)[0])) if target.lower().startswith('mailto:') else []


def contact_email_candidates(root: Tag) -> Tuple[str, ...]:
    """Read positive contact-field evidence, without guessing from the local part."""
    addresses = []
    for node in [root, *root.find_all(['p', 'div', 'td', 'dd', 'li', 'span', 'a'])]:
        # An outer container is not a contact field simply because its first
        # paragraph is one; that would absorb addresses in the research prose.
        if node.find(['p', 'div', 'table', 'section', 'article', 'li'], recursive=False):
            continue
        text = LayoutAwareTokenizer.get_clean_text(node, preserve_content=True)
        label = node.find_previous_sibling(['th', 'dt', 'td']) if node.name in {'td', 'dd'} else None
        label_text = label.get_text(' ', strip=True) if label else ''
        labeled = bool(_CONTACT_FIELD.match(text) or (label and (
            _CONTACT_FIELD.match(label_text) if label.name in {'th', 'dt'} else _CONTACT_LABEL_CELL.match(label_text))))
        mailto = _mailto_address(node) if node.name == 'a' else []
        if labeled or mailto:
            # A mailto link whose visible text is a word ('写信') still names its address.
            addresses.extend(find_emails(text) or mailto)
        else:
            # A field inside a line that starts with another one ('电话：… 邮箱：…').
            addresses.extend(labeled_text_emails(text))
    seen = set()
    return tuple(address for address in addresses
                 if not (address.lower() in seen or seen.add(address.lower())))


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
    def extract_single(cls, html: str, page_url: str, fallback_name: str = "",
                       *, preserve_content: bool = False) -> ProfileDetail:
        """Extract a single profile page without sibling context."""
        if not html or not html.strip():
            return ProfileDetail(name=fallback_name, url=page_url, status="no_profile")

        soup = BeautifulSoup(html, 'lxml')
        mecc = (soup.body or soup) if preserve_content else cls._find_single_mecc(soup)
        full_bio = LayoutAwareTokenizer.get_clean_text(mecc, preserve_content=preserve_content)
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
        contact_emails = contact_email_candidates(mecc)
        valid_email = (clean_email(contact_emails[0]) if len(contact_emails) == 1
                       else clean_email(personal_emails[0]) if personal_emails and not contact_emails else "")

        return ProfileDetail(
            name=name or fallback_name,
            url=page_url,
            title=title,
            email=valid_email,
            full_bio=full_bio,
            emails=tuple(emails),
            contact_emails=contact_emails,
            status="ok"
        )

    @classmethod
    def extract_cluster(cls, pages: List[Tuple[str, str, str]],
                        *, scoped_urls: Optional[Set[str]] = None) -> List[ProfileDetail]:
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
            scoped = url in (scoped_urls or ())
            mecc = ((soup.body or soup) if scoped else
                    cls._find_cluster_mecc(soup, siblings) if siblings else cls._find_single_mecc(soup))
            full_bio = LayoutAwareTokenizer.get_clean_text(mecc, preserve_content=scoped)
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
                contact_emails=contact_email_candidates(mecc),
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
                contacts = [em for em in d.contact_emails if len(owners.get(em.lower(), set())) < 2]
                d.email = (clean_email(contacts[0]) if len(contacts) == 1 and len(d.contact_emails) == 1
                           else clean_email(personal[0]) if personal and not d.contact_emails else "")

        return details
