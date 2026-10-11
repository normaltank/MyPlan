"""Per-observation relationship decisions, never teacher fields or plan data."""
from __future__ import annotations

import hashlib
import json
import math
from html import escape
from pathlib import Path
import time

from server import review_store

STATUS = 'source_qualification_missing'
STATUSES = frozenset({STATUS, 'possible_unreviewed_appointment'})
DECISIONS_FILE = 'source_relationship_reviews.json'
VERDICTS = frozenset({'confirm', 'exclude', 'unknown', 'revoked'})
BASES = frozenset({'official_source', 'reviewer_attestation'})


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def contract() -> str:
    root = Path(__file__).parent
    return digest({name: hashlib.sha256((root/name).read_bytes()).hexdigest() for name in
                   ('source_review.py', 'source_qualification.py', 'harvest_eligibility.py',
                    'faculty_extractor.py', 'detail_mecc.py', 'harvest_workflow.py', 'segmented_roster.py',
                    'pdf_roster.py', 'image_table_roster.py', 'profile_content.py', 'profile_structure.py',
                    'page_types.py', 'page_type_rank_grid.py')})


def item_id(item: dict) -> str:
    return digest(item['binding'])


def canonical_path(directory: Path, task_id: str) -> Path:
    directory = Path(directory).resolve()
    if directory.name.startswith('attempt-') and directory.parent.name == task_id:
        return directory.parent/DECISIONS_FILE
    return directory/DECISIONS_FILE


def build_items(candidates, sources, profiles, plan, task_id: str, snapshots: list[dict], media: list[dict]) -> list[dict]:
    """Bind each native observation to every relevant original and current code."""
    result = []
    version = contract()
    artifact_hashes = {}
    def artifact_hash(path):
        if path not in artifact_hashes:
            artifact_hashes[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        return artifact_hashes[path]
    for candidate in candidates:
        source_objects = [source.to_dict() for source in sources.get((candidate.name, candidate.url), ())
                          if hasattr(source, 'to_dict')]
        urls = {candidate.url, *(source['source_url'] for source in source_objects)}
        artifacts = []
        for snap in snapshots:
            if snap.get('url') in urls:
                path = Path(snap['path']).resolve()
                artifacts.append({'url': snap['url'], 'path': str(path), 'sha256': artifact_hash(path),
                                  'kind': snap.get('kind', 'source')})
        for asset in media:
            if (asset.get('profile_url') or asset.get('url')) in urls and asset.get('original_path'):
                path = Path(asset['original_path']).resolve()
                artifacts.append({'url': asset.get('media_url', ''), 'path': str(path),
                                  'sha256': artifact_hash(path), 'kind': asset.get('kind', 'media')})
        artifacts.sort(key=lambda artifact: (artifact['url'], artifact['kind'], artifact['sha256']))
        native = {'name': candidate.name, 'profile_url': candidate.url, 'title': candidate.title,
                  'card_context': candidate.card_context}
        binding = {'task_id': task_id, 'school': plan['school'], 'college': plan['college'],
                   'candidate_name': candidate.name, 'profile_url': candidate.url, 'native_sha256': digest(native),
                   'sources': sorted(source_objects, key=lambda source: json.dumps(source, sort_keys=True)),
                   'profile_raw_sha256': hashlib.sha256((profiles.get(candidate.url) or '').encode()).hexdigest(),
                   'artifacts': [{key: value for key, value in artifact.items() if key != 'path'} for artifact in artifacts],
                   'contract': version}
        item = {'binding': binding, 'name': candidate.name, 'profile_url': candidate.url,
                'native_observation': native, 'artifacts': artifacts,
                'source_roles': sorted({source['role'] for source in source_objects})}
        item['id'] = item_id(item)
        result.append(item)
    return result


def _current_artifacts(item: dict, root: Path | None = None) -> bool:
    try:
        if item.get('id') != item_id(item) or item['binding'].get('contract') != contract():
            return False
        if (item.get('name') != item['binding']['candidate_name']
                or item.get('profile_url') != item['binding']['profile_url']
                or digest(item['native_observation']) != item['binding']['native_sha256']
                or [{key: value for key, value in artifact.items() if key != 'path'}
                    for artifact in item['artifacts']] != item['binding']['artifacts']):
            return False
        if not item.get('artifacts'):
            return False
        for artifact in item['artifacts']:
            path = Path(artifact['path']).resolve()
            if root is not None and not path.is_relative_to(root.resolve()):
                return False
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
                return False
        return True
    except (OSError, KeyError, TypeError, ValueError):
        return False


def valid_decision(item: dict, decision: dict | None, *, include_unknown: bool = False) -> dict | None:
    try:
        if (not isinstance(decision, dict) or decision['id'] != item_id(item)
                or decision['binding'] != item['binding'] or not _current_artifacts(item)
                or decision['verdict'] not in ({'confirm', 'exclude', 'unknown'} if include_unknown else {'confirm', 'exclude'})
                or decision['evidence_basis'] not in BASES or not decision['reason'].strip()
                or type(decision['reviewed_by']) is not int or decision['reviewed_by'] <= 0
                or not math.isfinite(decision['reviewed_at']) or decision['reviewed_at'] <= 0):
            return None
        allowed = {artifact['url'] for artifact in item['artifacts']}
        if not set(decision['source_refs']) <= allowed:
            return None
        if decision['verdict'] != 'unknown' and decision['evidence_basis'] == 'official_source' and not decision['source_refs']:
            return None
        return decision
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def save_decision(path: Path, item: dict, verdict: str, *, evidence_basis: str,
                  reason: str, source_refs: list[str], reviewed_by: int) -> dict:
    if (verdict not in VERDICTS or evidence_basis not in BASES or type(reviewed_by) is not int
            or reviewed_by <= 0 or not isinstance(reason, str) or not reason.strip() or len(reason) > 2000
            or not _current_artifacts(item)):
        raise ValueError('current per-observation evidence, reviewer and basis required')
    decision = {'id': item['id'], 'binding': item['binding'], 'verdict': verdict, 'evidence_basis': evidence_basis,
                'reason': reason.strip(), 'source_refs': list(source_refs), 'reviewed_by': reviewed_by,
                'reviewed_at': time.time()}
    if verdict in {'confirm', 'exclude'} and valid_decision(item, decision) is None:
        raise ValueError('official decisions must cite the current original; attestation stays labelled human')
    path = Path(path)
    authoritative = canonical_path(path.parent, item['binding']['task_id'])
    review_store.put(authoritative, decision)
    if path.resolve() != authoritative.resolve():
        review_store.put(path, decision)
    return decision


def collect_items(audit_path: Path) -> list[dict]:
    path = Path(audit_path).resolve()
    audit = json.loads(path.read_text(encoding='utf8'))
    pending = _pending_keys(path, audit)
    return [item for item in audit.get('source_review_items', [])
            if (item.get('name'), item.get('profile_url')) in pending
            and item.get('binding', {}).get('school') == audit.get('school')
            and item.get('binding', {}).get('college') == audit.get('college')
            and item.get('binding', {}).get('task_id') == audit.get('task_id')
            and _current_artifacts(item, path.parent)]


def existing_items(audit_path: Path) -> list[dict]:
    """Current exact observations, including previously confirmed/excluded ones."""
    path = Path(audit_path).resolve()
    audit = json.loads(path.read_text(encoding='utf8'))
    return [item for item in audit.get('source_review_items', [])
            if item.get('binding', {}).get('school') == audit.get('school')
            and item.get('binding', {}).get('college') == audit.get('college')
            and item.get('binding', {}).get('task_id') == audit.get('task_id')
            and _current_artifacts(item, path.parent)]


def decisions_for(directory: Path, task_id: str) -> dict:
    return review_store.load(canonical_path(directory, task_id))


def _pending_keys(path: Path, audit: dict) -> set:
    pending = {(issue.get('name'), issue.get('url')) for issue in audit.get('unresolved', []) if issue.get('status') in STATUSES}
    stored = decisions_for(path.parent, audit.get('task_id', ''))
    applied_by_id = {decision['id']: decision for decision in audit.get('source_relationship_reviews', [])}
    for item in audit.get('source_review_items', []):
        decision = stored.get(item['id'])
        if decision is not None and decision != applied_by_id.get(item['id']):
            pending.add((item['name'], item['profile_url']))
    for applied in applied_by_id.values():
        if stored.get(applied['id']) != applied:
            binding = applied['binding']
            pending.add((binding['candidate_name'], binding['profile_url']))
    return pending


def rerun_only(audit: dict) -> bool:
    # New parser/rules must produce a new observation before anybody can approve
    # it. This permits native recapture, never reuse of an old human permission.
    items = audit.get('source_review_items', [])
    return bool(items) and any(item.get('binding', {}).get('contract') != contract() for item in items)


def review_complete(audit_path: Path, items: list[dict]) -> bool:
    audit = json.loads(Path(audit_path).read_text(encoding='utf8'))
    pending = _pending_keys(Path(audit_path), audit)
    if not pending:
        applied = audit.get('source_relationship_reviews', [])
        by_id = {item['id']: item for item in audit.get('source_review_items', [])}
        return bool(applied) and all(decision['id'] in by_id and valid_decision(by_id[decision['id']], decision)
                                    is not None for decision in applied)
    if pending != {(item['name'], item['profile_url']) for item in items}:
        return False
    return all(valid_decision(item, decisions_for(Path(audit_path).parent, item['binding']['task_id']).get(item['id']))
               is not None for item in items)


def apply_decisions(candidates, admitted, decisions, unresolved, items, directory: Path, task_id: str):
    """Change only relationship disposition; native fields stay untouched."""
    stored = decisions_for(directory, task_id)
    items_by_key = {(item['name'], item['profile_url']): item for item in items}
    accepted = {(candidate.name, candidate.url) for candidate in admitted}
    applied = []
    for candidate in candidates:
        key = (candidate.name, candidate.url)
        item = items_by_key.get(key)
        decision = valid_decision(item, stored.get(item['id']), include_unknown=True) if item else None
        if decision is None:
            continue
        unresolved = [issue for issue in unresolved if not (issue.get('status') in STATUSES
                      and (issue.get('name'), issue.get('url')) == key)]
        if decision['verdict'] == 'confirm' and key not in accepted:
            admitted.append(candidate)
            accepted.add(key)
        if decision['verdict'] != 'confirm':
            admitted = [person for person in admitted if (person.name, person.url) != key]
            accepted.discard(key)
        if decision['verdict'] == 'unknown':
            unresolved.append({'url': candidate.url, 'name': candidate.name, 'status': STATUS})
        for disposition in decisions:
            if (disposition.get('name'), disposition.get('profile_url')) == key:
                disposition.update(status={'confirm': 'eligible', 'exclude': 'excluded', 'unknown': 'unresolved'}[decision['verdict']],
                    evidence_kind='human_confirmed_relationship' if decision['evidence_basis'] == 'official_source'
                    else 'human_attested_relationship', relationship_review=decision)
        applied.append(decision)
    return admitted, decisions, unresolved, applied


def render_section(items: list[dict], *, endpoint: str, artifact_endpoint: str, csrf: str) -> str:
    cards = []
    for item in items:
        originals = []
        for index, artifact in enumerate(item['artifacts']):
            url = artifact_endpoint + '?item_id=' + item['id'] + '&index=' + str(index)
            originals.append('<label><input type="checkbox" class="source-ref" value="' + escape(artifact['url'], quote=True)
                + '"> <a href="' + escape(url, quote=True) + '" target="_blank" rel="noopener">查看保存原件</a> '
                + escape(artifact['url']) + '</label><br>')
        cards.append('<section class="card source-relationship" data-id="' + item['id'] + '"><h2>学院师资关系：'
            + escape(item['name']) + '</h2><p>来源用途：' + escape('、'.join(item['source_roles']))
            + '；组织成员身份不自动等于本学院师资。</p><p>本观察原文：</p><pre>'
            + escape(item['native_observation']['card_context']) + '</pre>' + ''.join(originals)
            + '<p>只判断这一条观察，不能凭同名或整份委员会名单确认。姓名、职称、邮箱和简介仍从原件读取。</p>'
            + '<select class="basis"><option value="official_source">官网原件支持关系</option>'
            + '<option value="reviewer_attestation">人工掌握依据，官网尚不足</option></select>'
            + '<textarea class="reason" placeholder="记录判断依据；不填写或改写教师字段"></textarea>'
            + '<button data-source-verdict="confirm">确认本学院正式／兼职／外聘师资关系</button>'
            + '<button data-source-verdict="exclude">排除本观察的师资关系</button>'
            + '<button data-source-verdict="unknown">证据不足，保持未知</button>'
            + '<button data-source-verdict="revoked">撤回此前决定</button><p class="status" role="status"></p></section>')
    if not cards:
        return ''
    config = json.dumps({'endpoint': endpoint, 'csrf': csrf}, ensure_ascii=False).replace('<', '\\u003c')
    script = '<script>const sourceReviewConfig=' + config + ''';
document.querySelectorAll('[data-source-verdict]').forEach(button=>button.addEventListener('click',async()=>{
 const card=button.closest('.source-relationship'), status=card.querySelector('.status');
 const payload={id:card.dataset.id,verdict:button.dataset.sourceVerdict,evidence_basis:card.querySelector('.basis').value,
 reason:card.querySelector('.reason').value,source_refs:[...card.querySelectorAll('.source-ref:checked')].map(x=>x.value)};
 const response=await fetch(sourceReviewConfig.endpoint,{method:'POST',headers:{'Content-Type':'application/json',
 'X-CSRF-Token':sourceReviewConfig.csrf},body:JSON.stringify(payload)});
 status.textContent=response.ok?'已保存逐观察决定；未知或撤回仍需核验。':(await response.json()).detail||'保存失败';
}));</script>'''
    return '<h2>来源与人员关系复核</h2><p>人工掌握但官网不足的决定会明确记录审核人和依据，不表示程序自动证明。</p>' + ''.join(cards) + script
