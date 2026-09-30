"""Fail-closed, structure-only evidence for an explicitly visible empty roster."""
from __future__ import annotations

import hashlib
import math
import re
import time
from pathlib import Path
from typing import Any
from server.browser_readiness import RequestTracker, install_request_tracking


SELECTORS = ("card_selector", "empty_state_selector", "roster_container_selector")
_TRUE = ("network_idle", "dom_stable", "container_visible", "marker_visible",
         "marker_contained", "marker_has_content")
_ZERO = ("card_count", "other_content_count", "media_count", "busy_count", "pagination_count")
_FIELDS = {"version", "confirmed", "url", "html_sha256", "captured_at", "container_count", "marker_count",
           *SELECTORS, *_TRUE, *_ZERO}


def install_empty_roster_tracking(page: Any) -> RequestTracker:
    """Compatibility entry point; uses the shared pre-navigation tracker."""
    return install_request_tracking(page)


def empty_roster_branch_valid(branch: dict[str, Any]) -> bool:
    """Only ordinary single-page branches may declare the two CSS locators."""
    return (branch.get("kind", "roster") == "roster" and branch.get("single_page") is True
            and not branch.get("next_selector")
            and all(isinstance(branch.get(key), str) and branch[key].strip() for key in SELECTORS))


def empty_roster_proof_valid(proof: Any, branch: dict[str, Any], html: str | None = None,
                            *, url: str | None = None) -> bool:
    """Validate complete facts; collection callers must supply the captured HTML.

    Without HTML this checks a browser tool's recorded proof for reconnaissance.
    It never turns a model-provided confirmed flag into collection evidence.
    """
    if not isinstance(proof, dict) or set(proof) != _FIELDS or not empty_roster_branch_valid(branch):
        return False
    if type(proof.get("version")) is not int or proof["version"] != 1 or proof.get("confirmed") is not True:
        return False
    if proof.get("url") != (url if url is not None else branch.get("url")):
        return False
    if any(proof.get(key) != branch[key] for key in SELECTORS):
        return False
    if any(proof.get(key) is not True for key in _TRUE):
        return False
    for key, count in [("container_count", 1), ("marker_count", 1), *((key, 0) for key in _ZERO)]:
        if type(proof.get(key)) is not int or proof[key] != count:
            return False
    captured = proof.get("captured_at")
    if (type(captured) not in (int, float) or not 0 < captured < 10 ** 12
            or not math.isfinite(captured)):
        return False
    digest = proof.get("html_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
        return False
    return html is None or digest == hashlib.sha256(html.encode("utf-8")).hexdigest()


_PROBE = r"""async selectors => {
  const visible = node => {
    if (!node || !node.isConnected || node.getClientRects().length === 0) return false;
    for (let n = node; n; n = n.parentElement) {
      const s = getComputedStyle(n);
      if (s.display === 'none' || s.visibility === 'hidden' || s.visibility === 'collapse'
          || Number(s.opacity) === 0 || n.hidden || n.getAttribute('aria-hidden') === 'true') return false;
    }
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const inspect = () => {
    const roots = [...document.querySelectorAll(selectors.roster_container_selector)];
    const markers = [...document.querySelectorAll(selectors.empty_state_selector)];
    const root = roots.length === 1 ? roots[0] : null;
    const marker = markers.length === 1 ? markers[0] : null;
    const contained = Boolean(root && marker && root !== marker && root.contains(marker)
      && root !== document.body && root !== document.documentElement);
    const nodes = root ? [root, ...root.querySelectorAll('*')] : [];
    const disabledPager = n => visible(n) && n.matches('button:disabled,input[type=button]:disabled,input[type=submit]:disabled,input[type=reset]:disabled')
      && n.children.length === 0 && __facultyRecon.isPaginationControl(n)
      && ![...n.attributes].some(a => /^on/i.test(a.name));
    // Only inert controls and empty layout wrappers containing those controls
    // are exempt. Hidden text, links, media and scripted wrappers remain content.
    const inertPagerLayout = n => {
      if (disabledPager(n)) return true;
      const descendants=[...n.querySelectorAll('*')];
      return descendants.length>0 && descendants.some(disabledPager)
        && !n.matches('a,button,input,select,textarea,script,template,[contenteditable=true]')
        && ![n,...descendants].some(p=>[...p.attributes].some(a=>/^on/i.test(a.name)))
        && descendants.every(p=>disabledPager(p) || (p.children.length>0
          && ![...p.childNodes].some(c=>c.nodeType===Node.TEXT_NODE && c.textContent.trim())))
        && ![...n.childNodes].some(c=>c.nodeType===Node.TEXT_NODE && c.textContent.trim());
    };
    let other = 0, media = 0, busy = 0;
    for (const n of nodes) {
      const style = getComputedStyle(n);
      const hiddenMarkerContent = Boolean(marker && n !== marker && marker.contains(n)
        && !visible(n));
      // A visible empty-state wrapper must not conceal text or pending content
      // in its descendants. Visible inline markup remains part of the marker.
      if (hiddenMarkerContent && ((n.textContent || '').trim() || n.children.length)) other++;
      if (n.matches('img,picture,svg,canvas,iframe,object,embed,video,audio,source')
          || style.backgroundImage !== 'none' || style.listStyleImage !== 'none') media++;
      for (const pseudo of ['::before', '::after']) {
        const s = getComputedStyle(n, pseudo);
        if (s.backgroundImage !== 'none' || s.listStyleImage !== 'none') media++;
        const hiddenPseudoContent = !visible(n) || s.display === 'none'
          || s.visibility === 'hidden' || s.visibility === 'collapse' || Number(s.opacity) === 0;
        if ((hiddenPseudoContent || (n !== marker && !(marker && marker.contains(n))))
            && !['none','normal','""',"''"].includes(s.content)) other++;
      }
      if (n.getAttribute('aria-busy') === 'true') busy++;
      if (n.matches('a[href],button,input,select,textarea,[contenteditable=true],script,template') && !disabledPager(n)) other++;
      // Only the empty marker and its layout ancestors may occupy the container.
      if (contained && n !== root && n !== marker && !n.contains(marker) && !marker.contains(n) && !inertPagerLayout(n)) other++;
      if (contained && !marker.contains(n) && n !== marker && !disabledPager(n)) {
        for (const child of n.childNodes)
          if (child.nodeType === Node.TEXT_NODE && child.textContent.trim()) other++;
      }
    }
    for (let n = root?.parentElement; n; n = n.parentElement)
      if (n.getAttribute('aria-busy') === 'true') busy++;
    return {url: location.href, container_count: roots.length, marker_count: markers.length,
      container_visible: visible(root), marker_visible: visible(marker), marker_contained: contained,
      marker_has_content: Boolean(marker && (marker.innerText || '').trim()),
      card_count: document.querySelectorAll(selectors.card_selector).length,
      other_content_count: other, media_count: media, busy_count: busy,
      pagination_count: __facultyRecon.pagination('body',0,12,true).total,
      ready: document.readyState === 'complete'};
  };
  const first = inspect();
  let mutations = 0;
  const observer = new MutationObserver(() => mutations++);
  observer.observe(document.documentElement, {subtree:true,childList:true,attributes:true,characterData:true});
  await new Promise(resolve => setTimeout(resolve, 300));
  const second = inspect();
  observer.disconnect();
  const stable = mutations === 0 && JSON.stringify(first) === JSON.stringify(second) && second.ready;
  delete second.ready;
  return {...second, dom_stable: stable};
}"""


async def verify_empty_roster(page: Any, branch: dict[str, Any], html: str) -> dict[str, Any]:
    """Revisit the live DOM, then bind a bounded, stable structural proof to HTML.

    Network-idle timeout, unavailable locators, late loading, or changed HTML all
    return unconfirmed evidence. No text, HTML, or images leave this helper.
    """
    proof = {"version": 1, "confirmed": False, "url": getattr(page, "url", branch.get("url")),
             **{key: branch.get(key) for key in SELECTORS},
             "html_sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(), "captured_at": time.time(),
             **{key: False for key in _TRUE}, **{key: -1 for key in _ZERO},
             "container_count": 0, "marker_count": 0}
    if not empty_roster_branch_valid(branch):
        return proof
    tracker = install_empty_roster_tracking(page)
    if not tracker.available or not tracker.navigation_observed:
        return proof
    initially_idle = not tracker.pending and not tracker.failures
    try:
        await page.wait_for_load_state("networkidle", timeout=2000)
        proof["network_idle"] = True
        # Install only a window object; unlike a script tag this does not change captured HTML.
        await page.evaluate(Path(__file__).with_name("recon_dom_probe.js").read_text(encoding="utf-8"))
        baseline_activity = tracker.activity
        facts = await page.evaluate(_PROBE, {key: branch[key] for key in SELECTORS})
        proof.update(facts)
        current = await page.content()
        if current != html:
            proof["dom_stable"] = False
        if not initially_idle or tracker.pending or tracker.failures or tracker.activity != baseline_activity:
            proof["network_idle"] = False
        proof["confirmed"] = True
        proof["confirmed"] = empty_roster_proof_valid(proof, branch, html, url=proof["url"])
    except Exception:
        proof["confirmed"] = False
    return proof
