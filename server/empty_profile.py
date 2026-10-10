"""A missing biography is a positive, capture-bound observation, never a fallback."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import re

from bs4 import BeautifulSoup


def heading_digest(text: str) -> str:
    return sha256(re.sub(r'\s+', '', text).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class EmptyProfileEvidence:
    requested_url: str
    final_url: str
    name: str
    html_sha256: str
    selected_html_sha256: str
    profile_content_selector: str
    identity_heading_sha256: str
    source_rosters: tuple[str, ...]
    status: str = 'browser_verified_empty_profile'

    def audit(self) -> dict:
        return asdict(self)

    @classmethod
    def verify(cls, url, html, selector, proof, candidates, sources):
        """Reject empty suffixes, redirects, shared identities and unproved sources."""
        from server.profile_content import scope_profile_html
        if not proof or proof.get('confirmed') is not True or proof.get('url') != url:
            return None
        if (proof.get('profile_content_selector') != selector
                or proof.get('profile_content_start_selector') is not None
                or proof.get('empty_container') is not True
                or proof.get('html_sha256') != sha256(html.encode('utf-8')).hexdigest()
                or proof.get('container_count') != 1
                or any(proof.get(key) is not True for key in
                       ('container_visible', 'dom_stable', 'network_idle'))
                or proof.get('hidden_content_count') != 0
                or proof.get('active_animation_count') != 0
                or proof.get('document_complete') is not True):
            return None
        owners = {candidate.name for candidate in candidates if candidate.url == url}
        rosters = tuple(sorted({row.get('roster_url') for row in sources if row.get('roster_url')}))
        if len(owners) != 1 or not rosters:
            return None
        name = next(iter(owners))
        _, scoped, issue = scope_profile_html(html, selector, url, allow_empty=True)
        if issue or not scoped or not scoped['empty_container']:
            return None
        if scoped['selected_html_sha256'] != proof.get('selected_html_sha256'):
            return None
        # A non-PDF frame or other unrecognized media is unread, not empty.
        node = BeautifulSoup(html, 'html.parser').select_one(selector)
        if node.find(['img', 'picture', 'source', 'svg', 'canvas', 'iframe', 'object', 'embed', 'video', 'audio']):
            return None
        identity = heading_digest(name)
        if identity not in proof.get('visible_identity_heading_sha256', []):
            return None
        if not any(heading_digest(heading.get_text()) == identity and not heading.find_parent(
                ['nav', 'header', 'footer', 'aside']) for heading in
                BeautifulSoup(html, 'html.parser').find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])):
            return None
        return cls(url, proof['url'], name, proof['html_sha256'], scoped['selected_html_sha256'],
                   selector, identity, rosters)
