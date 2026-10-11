"""Source-snapshot-bound review of an unavailable profile image's declared role.

No image bytes exist here. This evidence is not an original-image hash and can
never classify a PDF, unreadable text or an inaccessible profile as a portrait.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from server import review_store
from server.profile_content import scope_profile_html

STATUS = 'missing_profile_media_needs_review'
BASIS = 'missing_source_snapshot'
DECISIONS_FILE = 'media_kind_reviews.json'
IDENTITY_FIELDS = ('profile_url','requested_profile_url','media_url','profile_content_selector',
                   'profile_content_start_selector','selected_html_sha256','http_status')


def prepare_item(html: str, profile_url: str, selector: str, start: str | None,
                 media_url: str, status: int, *, requested_profile_url: str | None = None) -> dict | None:
    scoped, evidence, issue = scope_profile_html(html,selector,profile_url,start_selector=start)
    if issue or not evidence or status not in (404,410):
        return None
    soup = BeautifulSoup(scoped,'html.parser')
    images = [node for node in soup.select('img[src]')
              if urljoin(profile_url,node['src']) == media_url]
    if not images or any(node.has_attr('srcset') or node.find_parent('picture') for node in images):
        return None  # Rendition/fallback ownership needs a separate proof.
    if not soup.get_text(' ',strip=True):
        return None
    item = {'profile_url':profile_url,'requested_profile_url':requested_profile_url or profile_url,
            'media_url':media_url,'profile_content_selector':selector,
            'profile_content_start_selector':start,'selected_html_sha256':evidence['selected_html_sha256'],
            'http_status':status,'evidence_basis':BASIS,'missing_on_two_opens':True}
    item['id'] = identity(item)
    return item


def identity(item: dict) -> str:
    return hashlib.sha256(json.dumps([BASIS,*[item[key] for key in IDENTITY_FIELDS]],
        ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def _valid(decision: dict, item: dict) -> bool:
    actor = decision.get('actor',{})
    if not isinstance(actor,dict):
        return False
    human = ((actor.get('channel') == 'web' and type(actor.get('user_id')) is int and actor['user_id'] > 0)
             or (actor.get('channel') == 'chat' and isinstance(actor.get('confirmation_ref'),str)
                 and bool(actor['confirmation_ref'].strip())))
    return (human and decision.get('id') == identity(item)
            and decision.get('verdict') == 'photo' and decision.get('evidence_basis') == BASIS
            and all(decision.get(key) == item[key] for key in IDENTITY_FIELDS))


def confirmed(path: Path | None, item: dict, *, source_html: str | None = None) -> bool:
    if path is None:
        return False
    if item.get('id') != identity(item):
        return False
    decisions = review_store.load(path)
    decision = decisions.get(identity(item),{})
    if identity(item) in decisions:
        return _valid(decision,item)
    if source_html is None and item.get('_snapshot_path'):
        snapshot = Path(item['_snapshot_path']).resolve()
        if snapshot.is_relative_to(Path(path).resolve().parent):
            source_html = snapshot.read_text(encoding='utf8')
    if source_html is None:
        return False
    from server.review_evidence import archived_sources, equivalence, native_scope_projection, retain_current
    candidates = []
    for bundle in archived_sources(Path(path).parent.parent):
        candidates.extend((bundle, previous) for previous in bundle.audit.get('missing_profile_media_evidence', []))
        # A legacy role decision may predate the audit's selected container.
        # Recover its item only from that actual archive's original and its own
        # exact selector/hash, never from today's text or a manufactured verdict.
        for old_decision in bundle.decisions(DECISIONS_FILE).values():
            if (decisions.get(old_decision.get('id')) != old_decision
                    or not _valid(old_decision, old_decision)
                    or any(old_decision.get(key) != item[key] for key in IDENTITY_FIELDS
                           if key not in {'profile_content_selector', 'selected_html_sha256'})):
                continue
            try:
                previous = prepare_item(bundle.source_html(item['profile_url']), item['profile_url'],
                    old_decision['profile_content_selector'], old_decision['profile_content_start_selector'],
                    item['media_url'], item['http_status'], requested_profile_url=item['requested_profile_url'])
                if previous and previous['id'] == old_decision['id']:
                    candidates.append((bundle, previous))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    for bundle, previous in candidates:
        try:
            old_decision = bundle.decisions(DECISIONS_FILE).get(previous['id'],{})
            if (review_store.load(path).get(previous['id']) != old_decision or not _valid(old_decision,previous)
                    or any(previous[k] != item[k] for k in IDENTITY_FIELDS
                           if k not in {'profile_content_selector', 'selected_html_sha256'})):
                continue
            proof = equivalence(bundle.source_html(item['profile_url']),source_html,item['profile_url'],
                (previous['profile_content_selector'],previous['profile_content_start_selector']),
                (item['profile_content_selector'],item['profile_content_start_selector']),
                previous['selected_html_sha256'],item['selected_html_sha256'])
            if proof is None:
                proof = native_scope_projection(bundle.source_html(item['profile_url']), source_html,
                    item['profile_url'], (previous['profile_content_selector'], previous['profile_content_start_selector']),
                    (item['profile_content_selector'], item['profile_content_start_selector']),
                    previous['selected_html_sha256'], item['selected_html_sha256'], wrapper_only=True)
            if proof:
                proof.update(old_original_file_sha256=bundle.original_hash(item['profile_url']),
                             new_original_file_sha256=retain_current(bundle.root,source_html))
                item['review_equivalence'] = {**proof,'original_decision_id':old_decision['id'],
                    'current_review_id':identity(item)}
                return True
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return False


def save_decision(path: Path, item: dict, verdict: str, reviewed_by: int | None = None,
                  *, confirmation_ref: str | None = None) -> None:
    if verdict not in {'photo','unreadable'} or item.get('evidence_basis') != BASIS:
        raise ValueError('invalid missing image review')
    if type(reviewed_by) is int and reviewed_by > 0:
        actor = {'channel':'web','user_id':reviewed_by}
    elif isinstance(confirmation_ref,str) and confirmation_ref.strip():
        actor = {'channel':'chat','confirmation_ref':confirmation_ref}
    else:
        raise ValueError('missing image role requires a human decision')
    decision = {key:item[key] for key in IDENTITY_FIELDS}
    decision.update(id=identity(item),evidence_basis=BASIS,verdict=verdict,actor=actor,reviewed_at=time.time())
    review_store.put(path,decision)


def collect_items(audit_path: Path) -> list[dict]:
    root = audit_path.resolve().parent
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    flagged = {issue.get('evidence_id') for issue in audit.get('unresolved',[]) if issue.get('status') == STATUS}
    items = {}
    for item in audit.get('missing_profile_media_evidence',[]):
        if item.get('id') not in flagged:
            continue
        snapshot = Path(item['snapshot_path']).resolve()
        if not snapshot.is_relative_to(root) or not snapshot.is_file():
            raise ValueError('missing media source snapshot unavailable')
        html = snapshot.read_text(encoding='utf-8')
        fresh = prepare_item(html,item['profile_url'],item['profile_content_selector'],
                             item['profile_content_start_selector'],item['media_url'],item['http_status'],
                             requested_profile_url=item['requested_profile_url'])
        if (fresh is None or item.get('missing_on_two_opens') is not True
                or fresh['id'] != item['id'] or identity(item) != item['id']):
            raise ValueError('missing media source evidence changed')
        scoped,_,_ = scope_profile_html(html,item['profile_content_selector'],item['profile_url'],
                                       start_selector=item['profile_content_start_selector'])
        fresh['context_text'] = BeautifulSoup(scoped,'html.parser').get_text('\n',strip=True)
        fresh['_snapshot_path'] = str(snapshot)
        fresh['saved_verdict'] = review_store.load(root/DECISIONS_FILE).get(fresh['id'],{}).get('verdict','')
        if confirmed(root/DECISIONS_FILE,fresh,source_html=html):
            fresh['saved_verdict'] = 'photo'
        items[fresh['id']] = fresh
    if set(items) != flagged or None in flagged:
        raise ValueError('missing media review item is not backed by evidence')
    return list(items.values())


def review_complete(audit_path: Path, items: list[dict]) -> bool:
    audit = json.loads(audit_path.read_text(encoding='utf-8'))
    flagged = {issue.get('evidence_id') for issue in audit.get('unresolved',[]) if issue.get('status') == STATUS}
    return (None not in flagged and {item.get('id') for item in items} == flagged
            and all(confirmed(audit_path.parent/DECISIONS_FILE,item) for item in items))
