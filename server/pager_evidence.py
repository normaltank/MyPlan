"""Live terminal evidence for a pager explicitly bound to one roster widget.

Counter text alone and a no-op click alone are insufficient. The supported
WebPlus component binds its news and paging nodes by widget identity; count
arithmetic, visibility, live requests and an actual forward click must agree.
"""
from __future__ import annotations

import asyncio
import hashlib
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
    if tracker.failures or tracker.script_errors:
        return [{**proof, 'reason': 'page_load_failed'}]
    html = await page.content()
    digests = (scope_digest(html, card_selector), scope_digest(html, before['pager_selector']))
    failures = tracker.failures
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
        if popups or page.url != before['url'] or after != facts or current != digests:
            return [{**proof, 'reason': 'pager_state_changed'}]
        activity = tracker.activity
        await asyncio.sleep(0.3)
        stable_html = await page.content()
        stable = (scope_digest(stable_html, card_selector), scope_digest(stable_html, before['pager_selector']))
        if (popups or page.url != before['url'] or tracker.script_errors or tracker.pending
                or tracker.failures != failures or tracker.activity != activity
                or stable != current or await page.evaluate(FACTS, card_selector) != facts):
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
