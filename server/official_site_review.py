"""Task-level access observations and human review, independent of teacher decisions."""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html import escape
import json
import time
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field


class SiteDecisionRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    issue_id: str
    verdict: Literal['available', 'unavailable', 'unknown']
    reason: str = Field(default='', max_length=2000)


def _url_key(url: str) -> tuple:
    parsed = urlsplit(url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError('official access evidence requires an HTTP(S) URL')
    return parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip('/') or '/', parsed.query


def build_issue(task, observations: list[dict]) -> dict:
    """Register explicit access failures, never infer them from missing people or purpose."""
    urls = [url for url in [task.url, *task.roster_urls] if url]
    # Consume the core source-disposition contract; do not create another role taxonomy.
    urls.extend(item['url'] for item in task.stats.get('source_disposition_evidence', [])
                if item.get('reason') == 'source_unreadable' and item.get('status') == 'unavailable')
    allowed = {_url_key(url) for url in urls}
    if not observations:
        raise ValueError('current college-entry or roster access observations required')
    evidence = []
    for item in observations:
        if item.get('reason') is not None and (
                item['reason'] != 'source_unreadable' or item.get('status') != 'unavailable'):
            raise ValueError('source purpose or structure uncertainty is not a site access failure')
        if _url_key(item['url']) not in allowed:
            raise ValueError('access failure is not a known college entry or roster source')
        observed = datetime.fromisoformat(str(item['observed_at']).replace('Z', '+00:00'))
        if observed.tzinfo is None:
            raise ValueError('observation time must include its timezone')
        method = str(item.get('method') or '').strip()
        error = str(item.get('error') or item.get('error_kind') or '').strip()
        status = item.get('http_status')
        if status is not None and status >= 400:
            error = error or f'HTTP {status}'
        if not method or not error:
            raise ValueError('observed access method and error required')
        evidence.append({'url': item['url'], 'method': method,
                         'observed_at': observed.astimezone(timezone.utc).isoformat(),
                         'error': error, 'http_status': status})
    binding = {'task_id': task.task_id, 'school': task.school, 'college': task.college,
               'observations': evidence}
    identifier = sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True).encode('utf8')).hexdigest()
    return {'schema': 1, 'id': identifier, **binding, 'reported_at': time.time(), 'review': None}


def decision(issue: dict, request: SiteDecisionRequest, reviewed_by: int) -> dict:
    if request.issue_id != issue['id'] or type(reviewed_by) is not int or reviewed_by <= 0:
        raise ValueError('current issue and authenticated reviewer required')
    return {'issue_id': issue['id'], 'verdict': request.verdict, 'reason': request.reason.strip(),
            'reviewed_by': reviewed_by, 'reviewed_at': time.time()}


def label(issue: dict) -> str:
    verdict = (issue.get('review') or {}).get('verdict')
    return {'available': '官网已确认可用，等待显式重试',
            'unavailable': '官网不可访问，留待恢复后重试'}.get(verdict, '官网异常，待人工确认')


def render_page(task, *, csrf: str, field_review_url: str = '') -> str:
    from server.image_bio_review import _base_css, _REVIEW_CSS
    issue = task.stats['official_site_issue']
    evidence = ''.join('<li><a href="' + escape(item['url'], quote=True)
        + '" target="_blank" rel="noopener noreferrer">' + escape(item['url'])
        + '</a><p>方法：' + escape(item['method']) + ' · 时间：' + escape(item['observed_at'])
        + '</p><pre>' + escape(item['error']) + '</pre></li>' for item in issue['observations'])
    review = issue.get('review') or {}
    current = ('<p>已保存：' + escape(label(issue)) + '；审核人编号 ' + str(review['reviewed_by'])
               + '</p>') if review else ''
    config = json.dumps({'id': issue['id'], 'csrf': csrf,
        'save': f'/api/task/{task.task_id}/review/official-site',
        'retry': f'/api/task/{task.task_id}/official-site/retry'}, ensure_ascii=False).replace('<', '\\u003c')
    fields = ('<p><a href="' + escape(field_review_url, quote=True) + '">查看尚未完成的字段复核</a></p>') if field_review_url else ''
    disabled = '' if review.get('verdict') == 'available' else ' disabled'
    options = ''.join('<option value="' + value + '"'
                      + (' selected' if value == review.get('verdict', 'unknown') else '')
                      + '>' + text + '</option>' for value, text in
                      [('unknown', '暂无法确认'), ('unavailable', '当前不可访问'), ('available', '现在可访问')])
    return ('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"><title>官网异常复核</title><style>'
        + _base_css() + _REVIEW_CSS + '</style><p><a href="/">返回采集平台</a></p><h1>'
        + escape(task.school) + ' · ' + escape(task.college) + '</h1><h2>' + escape(label(issue)) + '</h2>'
        '<p>访问失败材料与已有采集记录保留。保存访问情况不会重新采集；确认恢复后可择时显式重试。</p>'
        '<section class="card"><h3>已保存的访问证据</h3><ul>' + evidence + '</ul>' + current
        + '<form id="site-review"><label>当前人工确认结果<select id="verdict">' + options + '</select></label>'
        '<label>复核说明（可选）<textarea id="reason" maxlength="2000">' + escape(review.get('reason', '')) + '</textarea></label>'
        '<button type="submit">保存访问复核</button></form>' + fields
        + '<button type="button" id="retry"' + disabled + '>显式重试采集</button>'
        '<p id="status" role="status"></p></section><script>const siteReview=' + config + ''';
const feedback=document.getElementById('status'), retry=document.getElementById('retry');
document.getElementById('site-review').onsubmit=async event=>{
 event.preventDefault();const verdict=document.getElementById('verdict').value;
 const response=await fetch(siteReview.save,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':siteReview.csrf},
 body:JSON.stringify({issue_id:siteReview.id,verdict,reason:document.getElementById('reason').value})});
 feedback.textContent=response.ok?'已保存；任务保留待处理，不会自动入队。':(await response.json()).detail;
 if(response.ok)retry.disabled=verdict!=='available';
};
retry.onclick=async()=>{const response=await fetch(siteReview.retry,{method:'POST',headers:{'X-CSRF-Token':siteReview.csrf}});
 feedback.textContent=response.ok?'已按明确请求进入原采集队列。':(await response.json()).detail;
 if(response.ok)retry.disabled=true;
};</script></html>''')
