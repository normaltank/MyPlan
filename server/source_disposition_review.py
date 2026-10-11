"""Review a page's collection purpose, independently of personnel eligibility."""
from __future__ import annotations

import hashlib
import json
import time
from html import escape
from pathlib import Path

from server import review_store
from server.source_review import digest

STATUSES = frozenset({'source_relationship_unverified', 'source_structure_unresolved', 'source_unreadable'})
DECISIONS_FILE = 'source_disposition_reviews.json'
DECISION_LABELS = {'exclude': '审核人选择该保存页面不是师资名录',
                   'recon': '审核人要求重新侦察该来源',
                   'unknown': '审核人暂不能确定该来源用途',
                   'revoked': '审核人撤回原来源决定'}
VERDICTS = frozenset(DECISION_LABELS)
SOURCE_KEYS = ('url', 'reason', 'source_role', 'source_container_selector',
               'role_evidence_selector', 'source_context_selector')


def decision_path(directory: Path, task_id: str) -> Path:
    directory = Path(directory).resolve()
    if directory.name.startswith('attempt-') and directory.parent.name == task_id:
        directory = directory.parent
    return directory / DECISIONS_FILE


def build_items(plan: dict, evidence: list[dict], directory: Path, task_id: str) -> list[dict]:
    root = Path(directory).resolve()
    items = []
    for source in evidence:
        if source.get('reason') not in STATUSES or source.get('status') == 'inactive':
            continue
        original = None
        if source.get('original_path'):
            path = Path(source['original_path']).resolve()
            if (path.is_relative_to(root) and path.is_file()
                    and hashlib.sha256(path.read_bytes()).hexdigest() == source.get('source_html_sha256')):
                original = str(path)
        binding = {'contract': 'source-disposition-review-v1', 'task_id': task_id,
                   'school': plan['school'], 'college': plan['college'],
                   'source': {key: source[key] for key in SOURCE_KEYS if key in source},
                   'source_html_sha256': source.get('source_html_sha256'),
                   'http_status': source.get('http_status'), 'error_kind': source.get('error_kind')}
        item = {'id': digest(binding), 'binding': binding, 'url': source['url'],
                'reason': source['reason'], 'original_path': original}
        items.append(item)
    return items


def collect_items(audit_path: Path) -> list[dict]:
    path = Path(audit_path).resolve()
    audit = json.loads(path.read_text(encoding='utf-8'))
    task_id = audit.get('task_id', path.parent.parent.name)
    items = build_items(audit, audit.get('source_disposition_evidence', []), path.parent, task_id)
    stored = review_store.load(decision_path(path.parent, task_id))
    for item in items:
        item['saved_decision'] = valid_decision(item, stored.get(item['id']))
    return items


def valid_decision(item: dict, decision: dict | None) -> dict | None:
    if (not isinstance(decision, dict) or decision.get('id') != item['id']
            or decision.get('binding') != item['binding'] or decision.get('verdict') not in VERDICTS
            or type(decision.get('reviewed_by')) is not int or decision['reviewed_by'] <= 0
            or not isinstance(decision.get('reason'), str) or not decision['reason'].strip()):
        return None
    if decision['verdict'] == 'exclude':
        if not can_exclude(item):
            return None
        path = Path(item['original_path']) if item.get('original_path') else None
        if not path or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item['binding']['source_html_sha256']:
            return None
    return decision


def can_exclude(item: dict) -> bool:
    binding = item['binding']
    status = binding.get('http_status')
    return bool(item.get('original_path') and item['reason'] != 'source_unreadable'
                and not binding.get('error_kind') and (status is None or status < 400))


def save_decision(directory: Path, item: dict, verdict: str, reason: str,
                  reviewed_by: int, entry_url: str = '') -> dict:
    from server.agent_recon import _is_structure_url
    if (entry_url and (verdict != 'recon' or not _is_structure_url(entry_url))) or len(reason) > 2000:
        raise ValueError('collection requests accept only a source URL, never a plan or teacher fields')
    decision = {'id': item['id'], 'binding': item['binding'], 'verdict': verdict,
                'reason': reason.strip() or DECISION_LABELS.get(verdict, ''),
                'note': reason.strip(),
                'reviewed_by': reviewed_by, 'reviewed_at': time.time(),
                'entry_url': entry_url}
    if valid_decision(item, decision) is None:
        raise ValueError('current original and explicit reviewer basis required')
    review_store.put(decision_path(directory, item['binding']['task_id']), decision)
    return decision


def _pending_urls(path: Path, audit: dict) -> set[str]:
    pending = {issue.get('url') for issue in audit.get('unresolved', []) if issue.get('status') in STATUSES}
    stored = review_store.load(decision_path(path.parent, audit.get('task_id', path.parent.parent.name)))
    for applied in audit.get('source_disposition_reviews', []):
        if stored.get(applied['id']) != applied:
            pending.add(applied['binding']['source']['url'])
    return pending


def has_pending(audit_path: Path) -> bool:
    path = Path(audit_path)
    return bool(_pending_urls(path, json.loads(path.read_text(encoding='utf-8'))))


def review_complete(audit_path: Path, items: list[dict]) -> bool:
    if not items:
        return False
    audit = json.loads(Path(audit_path).read_text(encoding='utf-8'))
    pending = _pending_urls(Path(audit_path), audit)
    by_url = {item['url']: item for item in items}
    stored = review_store.load(decision_path(Path(audit_path).parent, items[0]['binding']['task_id']))
    if not pending:
        by_id = {item['id']: item for item in items}
        applied = audit.get('source_disposition_reviews', [])
        return bool(applied) and all(decision['id'] in by_id
            and valid_decision(by_id[decision['id']], stored.get(decision['id'])) == decision for decision in applied)
    return bool(pending) and pending <= by_url.keys() and all(
        (decision := valid_decision(by_url[url], stored.get(by_url[url]['id']))) is not None
        and decision['verdict'] in {'exclude', 'recon'} for url in pending)


def apply_decisions(plan: dict, evidence: list[dict], issues: list[dict], directory: Path, task_id: str):
    items = build_items(plan, evidence, directory, task_id)
    stored = review_store.load(decision_path(directory, task_id))
    applied = []
    for item in items:
        decision = valid_decision(item, stored.get(item['id']))
        if decision and decision['verdict'] == 'exclude':
            issues = [issue for issue in issues if not (issue.get('url') == item['url']
                      and issue.get('status') in STATUSES)]
            applied.append(decision)
    # A request to inspect structure is never permission to deliver its people.
    return issues, applied


def recon_feedback(task_root: Path, task_id: str) -> dict | None:
    marker = Path(task_root) / 'review-resume.json'
    if not marker.is_file() or not decision_path(task_root, task_id).is_file():
        return None
    resume = json.loads(marker.read_text(encoding='utf-8'))
    audit_path = Path(resume['audit_path']).resolve()
    if resume.get('task_id') != task_id or not audit_path.is_relative_to(Path(task_root).resolve()):
        return None
    stored = review_store.load(decision_path(task_root, task_id))
    urls = []
    for item in collect_items(audit_path):
        decision = valid_decision(item, stored.get(item['id']))
        if decision and decision['verdict'] == 'recon':
            urls.extend([item['url'], *([decision['entry_url']] if decision.get('entry_url') else [])])
    return {'known_urls': list(dict.fromkeys(urls)),
            'issues': [{'url': url, 'status': 'source_structure_unresolved'} for url in dict.fromkeys(urls)]} if urls else None


def render_section(items: list[dict], *, endpoint: str, artifact_endpoint: str, csrf: str) -> str:
    cards = []
    for item in items:
        saved = item.get('saved_decision') or {}
        original = ('<a target="_blank" rel="noopener" href="' + escape(artifact_endpoint + '?item_id=' + item['id'], quote=True)
                    + '">查看保存原件</a>') if item.get('original_path') else '未取得可读原件，不能据此排除师资来源'
        cards.append('<section class="card source-disposition" data-id="' + item['id'] + '"><h2>来源页面用途与结构复核</h2>'
            '<p><a target="_blank" rel="noopener" href="' + escape(item['url'], quote=True) + '">' + escape(item['url']) + '</a></p>'
            '<p>' + escape(item['reason']) + ' · ' + original + '</p>'
            '<p>只判断这页是否需要作为师资来源采集，不确认其中每个人的教师身份。请求重新侦察不会视为采集成功。</p>'
            '<label>补充说明（选填）<textarea class="disposition-reason" placeholder="可说明原件中的页面用途或仍需检查的问题">'
            + escape(saved.get('note', saved.get('reason', ''))) + '</textarea></label>'
            '<label>可选补充官方入口<input class="disposition-entry" type="url" value="'
            + escape(saved.get('entry_url', ''), quote=True) + '"></label>'
            '<div class="actions"><button data-verdict="exclude"' + ('' if can_exclude(item) else ' disabled')
            + '>这页不是师资名录</button><button data-verdict="recon">需要采集，重新侦察</button>'
            '<button data-verdict="unknown">暂无法确定</button><button data-verdict="revoked">撤回原决定</button>'
            '</div><p class="status" role="status">' + escape({
                'exclude': '已保存：该页不是师资名录，可改选', 'recon': '已保存：请求重新侦察',
                'unknown': '已保存：仍待复核，可改选', 'revoked': '原决定已撤回，仍待复核'
            }.get(saved.get('verdict'), '尚未保存来源决定')) + '</p></section>')
    return ''.join(cards) + '<script>document.querySelectorAll(".source-disposition").forEach(card=>{' \
        'card.querySelectorAll("button").forEach(button=>button.onclick=async()=>{const status=card.querySelector(".status");' \
        'try{const response=await fetch(' + json.dumps(endpoint) + ',{method:"POST",headers:{"Content-Type":"application/json",' \
        '"X-CSRF-Token":' + json.dumps(csrf) + '},body:JSON.stringify({id:card.dataset.id,verdict:button.dataset.verdict,' \
        'reason:card.querySelector(".disposition-reason").value,entry_url:button.dataset.verdict==="recon"?' \
        'card.querySelector(".disposition-entry").value:""})});' \
        'if(!response.ok)throw new Error((await response.json()).detail||"保存失败");' \
        'status.textContent=["unknown","revoked"].includes(button.dataset.verdict)?"已保存：仍待复核，可改选":' \
        'button.dataset.verdict==="recon"?"已保存重新侦察请求；复核完成后重试，原问题仍须重新核验":' \
        '"已保存来源用途决定，可改选；不改变人员资格";status.style.color="";}' \
        'catch(error){status.textContent="未保存："+error.message;status.style.color="var(--danger,#b91c1c)";}});});</script>'
