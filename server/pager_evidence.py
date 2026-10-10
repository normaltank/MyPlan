"""Live terminal evidence for a pager explicitly bound to one roster widget.

Counter text alone and a no-op click alone are insufficient. The supported
WebPlus component binds its news and paging nodes by widget identity; count
arithmetic, visibility, live requests and an actual forward click must agree.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
from typing import Any

from bs4 import BeautifulSoup

FACTS = r'''selector => {
  const cards = [...document.querySelectorAll(selector)];
  const visible = node => {
    if (!node.getClientRects().length) return false;
    for (let n=node; n; n=n.parentElement) {
      const s=getComputedStyle(n);
      if (n.hidden || n.getAttribute('aria-hidden')==='true' || s.display==='none'
          || s.visibility==='hidden' || s.visibility==='collapse' || Number(s.opacity)===0) return false;
    }
    return true;
  };
  if (!cards.length || !cards.every(visible)) return [];
  const found=[];
  for (const host of document.querySelectorAll('[id^="wp_paging_"]')) {
    const widget=host.id.slice('wp_paging_'.length);
    const news=document.getElementById('wp_news_'+widget);
    if (!news || !cards.every(c=>news.contains(c))) continue;
    const boxes=host.querySelectorAll('.wp_paging');
    if (boxes.length!==1 || !visible(boxes[0])) continue;
    const box=boxes[0];
    const number = css => {
      const ns=box.querySelectorAll(css);
      return ns.length===1 && visible(ns[0]) && /^\d+$/.test(ns[0].textContent.trim())
        ? Number(ns[0].textContent.trim()) : null;
    };
    const current=number('.page_jump em.curr_page'), pages=number('.page_jump em.all_pages');
    const total=number('.pages_count em.all_count'), per=number('.pages_count em.per_count');
    const next=box.querySelectorAll('.page_nav > a.next');
    if (next.length!==1 || !visible(next[0]) || !/^(?:javascript:void\(0\);?|#)?$/i.test(next[0].getAttribute('href')||'')) continue;
    if (!(current===1 && pages===1 && total>0 && per>0 && Math.ceil(total/per)===pages
        && cards.length===total-(current-1)*per)) continue;
    found.push({url:location.href, roster_selector:selector,
      pager_selector:'#'+CSS.escape(host.id)+' .wp_paging',
      control_selector:'#'+CSS.escape(host.id)+' .wp_paging .page_nav > a.next',
      current_page:current,total_pages:pages,record_total:total,page_size:per,card_count:cards.length});
  }
  return found;
}'''


def scope_digest(html: str, selector: str) -> str:
    nodes = BeautifulSoup(html, 'html.parser').select(selector)
    return hashlib.sha256(''.join(str(node) for node in nodes).encode('utf-8')).hexdigest()


async def _controller_evidence(page: Any, facts: dict, html: str, tracker: Any) -> dict | None:
    """Server-published terminal navigation and a responsive, bound controller.

    No missing-asset URL whitelist: only permanent script/style failures may
    be scoped away after source DOM and actual controller responses agree.
    """
    if (tracker.script_errors or not tracker.failed_requests or tracker.document_response is None
            or any(kind not in {'script','stylesheet'} or status not in (404,410)
                   for _,kind,status in tracker.failed_requests)):
        return None
    raw = await tracker.document_response.text()
    selectors = (facts['roster_selector'],facts['pager_selector'])
    if tracker.document_response.status >= 400 or any(scope_digest(raw,s) != scope_digest(html,s) for s in selectors):
        return None
    soup = BeautifulSoup(raw,'html.parser')
    boxes = soup.select(facts['pager_selector'])
    if len(boxes) != 1:
        return None
    for role in ('first','prev','next','last'):
        controls = boxes[0].select('.page_nav > a.'+role)
        if len(controls) != 1 or not re.fullmatch(r'(?:javascript:void\(0\);?|#)?',controls[0].get('href',''),re.I):
            return None
    field = page.locator(facts['pager_selector']+' .pageNum')
    jump = page.locator(facts['pager_selector']+' .pagingJump')
    if await field.count() != 1 or await jump.count() != 1 or not await field.is_visible() or not await jump.is_visible():
        return None
    initial = await field.input_value()
    alerts = []
    popups = []
    def opened(_popup):
        popups.append(True)
    async def shown(dialog):
        alerts.append((dialog.type,hashlib.sha256(dialog.message.encode()).hexdigest()))
        await dialog.dismiss()
    page.on('dialog',shown)
    page.on('popup',opened)
    passed = False
    try:
        responses = []
        for value in ('',str(facts['total_pages']+1)):
            await field.fill(value,timeout=1500)
            alerts.clear()
            await jump.click(timeout=1500)
            if len(alerts) != 1 or alerts[0][0] != 'alert':
                return None
            responses.append(alerts[0][1])
        if responses[0] == responses[1] or page.url != facts['url'] or popups:
            return None
        passed = True
    except Exception:
        return None
    finally:
        page.remove_listener('dialog',shown)
        await field.fill(initial,timeout=1500)
        page.remove_listener('popup',opened)
    if passed and not popups and page.url == facts['url']:
        return {'server_rendered':True,'probes':2,'failed_resource_count':len(tracker.failed_requests)}
    return None


async def terminal_pagers(page: Any, card_selector: str) -> list[dict]:
    """Only current-browser evidence, never selector guesses or stale counters."""
    if not card_selector or not callable(getattr(page, 'evaluate', None)):
        return []
    try:
        facts = await page.evaluate(FACTS, card_selector)
    except Exception:
        return []
    if not isinstance(facts, list) or len(facts) != 1:
        return []
    from server.browser_readiness import install_request_tracking
    tracker = install_request_tracking(page)
    before = facts[0]
    proof = {**before, 'confirmed': False}
    if not tracker.available or not tracker.navigation_observed or tracker.pending:
        return [{**proof, 'reason': 'network_not_idle'}]
    html = await page.content()
    failures = tracker.failures
    failure_set = list(tracker.failed_requests)
    initial_activity = tracker.activity
    document = tracker.document_response
    if tracker.script_errors:
        return [{**proof, 'reason': 'page_load_failed'}]
    if tracker.failures:
        try:
            controller = await _controller_evidence(page,before,html,tracker)
        except Exception:
            controller = None
        if controller is None:
            return [{**proof, 'reason': 'page_load_failed'}]
        if (tracker.failures != failures or tracker.failed_requests != failure_set
                or tracker.document_response is not document
                or tracker.pending or tracker.script_errors or tracker.activity != initial_activity):
            return [{**proof, 'reason': 'controller_requests_changed'}]
        proof['controller_evidence'] = controller
    digests = (scope_digest(html, card_selector), scope_digest(html, before['pager_selector']))
    popups = []
    def opened(_popup):
        popups.append(True)
    page.on('popup', opened)
    try:
        await page.locator(before['control_selector']).click(timeout=1500)
        await page.wait_for_load_state('networkidle', timeout=3000)
        await asyncio.sleep(0.3)
        after = await page.evaluate(FACTS, card_selector)
        final_html = await page.content()
        current = (scope_digest(final_html, card_selector), scope_digest(final_html, before['pager_selector']))
        if (popups or page.url != before['url'] or after != facts or current != digests
                or tracker.document_response is not document):
            return [{**proof, 'reason': 'pager_state_changed'}]
        activity = tracker.activity
        await asyncio.sleep(0.3)
        stable_html = await page.content()
        stable = (scope_digest(stable_html, card_selector), scope_digest(stable_html, before['pager_selector']))
        stable_facts = await page.evaluate(FACTS, card_selector)
        if (popups or page.url != before['url'] or tracker.script_errors or tracker.pending
                or tracker.failures != failures or tracker.activity != activity
                or tracker.failed_requests != failure_set or tracker.document_response is not document
                or stable != current or stable_facts != facts):
            return [{**proof, 'reason': 'pager_not_stable'}]
        return [{**proof, 'confirmed': True, 'source_sha256': current[0], 'pager_sha256': current[1]}]
    except Exception:
        return [{**proof, 'reason': 'pager_click_unverified'}]
    finally:
        page.remove_listener('popup', opened)


def proof_valid(proof: dict, html: str, card_selector: str) -> bool:
    """Bind saved terminal evidence to its own captured source and pager."""
    try:
        return (proof.get('confirmed') is True and proof['roster_selector'] == card_selector
                and proof['source_sha256'] == scope_digest(html, card_selector)
                and proof['pager_sha256'] == scope_digest(html, proof['pager_selector']))
    except (KeyError, TypeError, ValueError):
        return False


def without_terminal_pagers(html: str, pagination: dict, proofs: list[dict], card_selector: str) -> dict:
    soup = BeautifulSoup(html, 'html.parser')
    ignored = [node for proof in proofs if proof_valid(proof, html, card_selector)
               for node in soup.select(proof['control_selector'])]
    items = pagination.get('items') or []
    kept = []
    for item in items:
        nodes = soup.select(item['selector'])
        if not (len(nodes) == 1 and any(nodes[0] is node for node in ignored)):
            kept.append(item)
    return {**pagination, 'items': kept, 'total': pagination['total']-(len(items)-len(kept)),
            **({'terminal_pagers': proofs} if proofs else {})}
