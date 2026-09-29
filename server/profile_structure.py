"""Live structural proof for a profile container; never return its contents."""
from __future__ import annotations

import asyncio
import hashlib
from typing import Any


_FACTS = """args => {
  const nodes=[...document.querySelectorAll(args.selector)];
  const visible=n=>{
    if(!n || !n.getClientRects().length) return false;
    for(let p=n;p;p=p.parentElement){const s=getComputedStyle(p);
      if(p.hidden || p.getAttribute('aria-hidden')==='true' || s.display==='none'
         || s.visibility==='hidden' || s.visibility==='collapse' || s.contentVisibility==='hidden'
         || Number(s.opacity)===0) return false;}
    return true;
  };
  const root=nodes.length===1?nodes[0]:null;
  const starts=args.start?[...document.querySelectorAll(args.start)]:[];
  const startValid=!args.start || (starts.length===1 && starts[0].parentElement===root && visible(starts[0]));
  let kept=root?[...root.children]:[];
  if(args.start && startValid) kept=kept.slice(kept.indexOf(starts[0])+1);
  const retained=kept.flatMap(n=>[n,...n.querySelectorAll('*')]);
  const relevant=retained.filter(n=>!n.matches('script,style,noscript,template')
    && ((n.textContent||'').trim() || n.matches('img,picture,svg,canvas,iframe,object,embed,video,audio')));
  const hidden=relevant.filter(n=>!visible(n)).length;
  const active=root?root.getAnimations({subtree:true}).filter(a=>
    a.playState==='running' && (a.effect?.target===root || retained.includes(a.effect?.target))).length:0;
  const ancestors=[];
  for(let p=root?.parentElement;p;p=p.parentElement) ancestors.push(p);
  const visibilityProperties=new Set(['opacity','display','visibility','contentVisibility','transform',
    'translate','rotate','scale','clipPath','filter','width','height','maxWidth','maxHeight',
    'minWidth','minHeight','overflow','overflowX','overflowY']);
  const ancestorActive=ancestors.flatMap(n=>n.getAnimations()).filter(a=>a.playState==='running'
    && a.effect?.getKeyframes().some(frame=>Object.keys(frame).some(k=>visibilityProperties.has(k)))).length;
  const styles=[...(root?[root]:[]),...relevant,...ancestors].map(n=>{
    const s=getComputedStyle(n);
    return [s.display,s.visibility,s.opacity,s.contentVisibility,s.transform];
  });
  return {container_count:nodes.length,container_visible:visible(root),
    hidden_content_count:hidden,start_valid:startValid,active_animation_count:active+ancestorActive,styles};
}"""


_EXTERNAL_IMAGES = """args => {
  const root=document.querySelector(args.selector);
  if(!root) return args.urls.map(()=>false);
  return args.urls.map(url=>{
    const images=[...document.images].filter(img=>img.currentSrc===url || img.src===url);
    return images.length>0 && images.every(img=>!root.contains(img));
  });
}"""


_EXTERNAL_REQUESTS = """args => {
  const root=document.querySelector(args.selector);
  if(!root) return args.requests.map(()=>false);
  return args.requests.map(item=>{
    if(item.resource_type!=="image") return false;
    const images=[...document.images].filter(img=>img.currentSrc===item.url || img.src===item.url);
    return images.length>0 && images.every(img=>!root.contains(img));
  });
}"""


_EQUIVALENT_SELECTORS = """args => {
  const matches=args.selectors.map(selector=>[...document.querySelectorAll(selector)]);
  if(matches.some(group=>group.length!==1)) return -1;
  const nodes=matches.map(group=>group[0]);
  if(new Set(nodes).size!==nodes.length){
    // Two branches may describe the same node through different scoped
    // selectors. Accept that only when every selector is compound or
    // class/id qualified and at least one contains a structural relation;
    // a bare tag such as article remains an unresolved ambiguity.
    const bareTag=/^[A-Za-z][A-Za-z0-9_-]*$/;
    const safe=args.selectors.every(selector=>!bareTag.test(selector.trim())
      && /[.#]/.test(selector)) && args.selectors.some(selector=>/[>\\s]/.test(selector));
    if(!safe) return -1;
    return args.selectors.reduce((best, selector, index)=>
      selector.length > args.selectors[best].length ? index : best, 0);
  }
  const deepest=nodes.findIndex(node=>nodes.every(other=>other===node || other.contains(node)));
  if(deepest<0) return -1;
  const leaf=nodes[deepest];
  for(const outer of nodes){
    let node=outer;
    while(node!==leaf){
      if(node.children.length!==1) return -1;
      if([...node.childNodes].some(child=>child.nodeType===Node.TEXT_NODE && child.textContent.trim())) return -1;
      node=node.firstElementChild;
      if(!node) return -1;
    }
  }
  return deepest;
}"""


async def resolve_equivalent_profile_selectors(
    page: Any, pairs: set[tuple[str, str | None]],
) -> tuple[str, str | None] | None:
    """Pick the innermost selector only when all others add empty wrappers."""
    ordered = sorted(pairs, key=lambda pair: (pair[0], pair[1] or ""))
    if not ordered or len({start for _, start in ordered}) != 1:
        return None
    try:
        index = await page.evaluate(_EQUIVALENT_SELECTORS,
                                    {"selectors": [selector for selector, _ in ordered]})
    except Exception:
        return None
    return ordered[index] if isinstance(index, int) and 0 <= index < len(ordered) else None


async def verify_profile_container(page: Any, selector: str, start_selector: str | None = None,
                                   *, local_capture: dict[str, str] | None = None) -> dict:
    """Return compact facts; an optional Python-only sink retains the bound HTML.

    Browser tools do not expose the sink. It prevents a second page.content()
    call from replacing the exact snapshot hashed by this proof.
    """
    from server.browser_readiness import install_request_tracking
    from server.profile_content import scope_profile_html
    tracker = install_request_tracking(page)
    result = {"url": page.url, "profile_content_selector": selector,
              "profile_content_start_selector": start_selector, "confirmed": False,
              "container_count": 0, "container_visible": False, "dom_stable": False,
              "network_idle": False}
    html = None
    try:
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
        except Exception as exc:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            if not isinstance(exc, PlaywrightTimeoutError):
                raise
            # Playwright's networkidle is only a preliminary wait. The
            # request tracker, selected DOM snapshots, and animation checks
            # below provide the actual completion proof.
        activity = tracker.activity
        initially_idle = tracker.available and tracker.navigation_observed
        html = await page.content()
        _, first_evidence, issue = scope_profile_html(html, selector, page.url, start_selector=start_selector)
        if issue:
            result['scope_issue'] = issue['status']
            return result
        args = {"selector": selector, "start": start_selector}
        first_facts = await page.evaluate(_FACTS, args)
        await asyncio.sleep(0.3)
        html = await page.content()
        _, evidence, issue = scope_profile_html(html, selector, page.url, start_selector=start_selector)
        facts = await page.evaluate(_FACTS, args)
        selected_stable = bool(not issue and evidence
                               and first_evidence['selected_html_sha256'] == evidence['selected_html_sha256'])
        visibility_stable = first_facts == facts and not facts['active_animation_count']
        stable = bool(selected_stable and visibility_stable)
        missing_images = [(url, kind, status) for url, kind, status in tracker.failed_requests
                          if kind == 'image' and status in (404, 410)]
        ignored = 0
        if missing_images and facts['container_count'] == 1:
            outside = await page.evaluate(_EXTERNAL_IMAGES,
                                          {'selector': selector, 'urls': [url for url, _, _ in missing_images]})
            ignored = sum(bool(value) for value in outside)
        pending_requests = [
            (getattr(request, 'url', ''), getattr(request, 'resource_type', ''))
            for request in tracker.pending
        ]
        ignored_pending = 0
        if pending_requests and facts['container_count'] == 1:
            outside_pending = await page.evaluate(
                _EXTERNAL_REQUESTS,
                {'selector': selector,
                 'requests': [{'url': url, 'resource_type': kind}
                              for url, kind in pending_requests]})
            ignored_pending = sum(bool(value) for value in outside_pending)
        blocking_failures = tracker.failures - ignored
        blocking_pending = len(tracker.pending) - ignored_pending
        idle = (initially_idle and blocking_pending == 0 and blocking_failures == 0
                and activity == tracker.activity)
        result.update(container_count=facts["container_count"], container_visible=facts["container_visible"],
                      dom_stable=stable, network_idle=idle,
                      ignored_external_image_failures=ignored,
                      ignored_external_image_pending=ignored_pending,
                      blocking_pending_request_count=blocking_pending,
                      blocking_failed_request_count=blocking_failures,
                      selected_dom_stable=selected_stable, visibility_stable=bool(visibility_stable),
                      network_activity_changed=activity != tracker.activity,
                      hidden_content_count=facts['hidden_content_count'], start_valid=facts['start_valid'],
                      active_animation_count=facts['active_animation_count'])
        if evidence:
            result['selected_html_sha256'] = evidence['selected_html_sha256']
        if issue:
            result['scope_issue'] = issue['status']
        result["confirmed"] = (facts["container_count"] == 1 and facts["container_visible"]
                               and facts["start_valid"] and not facts['hidden_content_count'] and stable and idle)
    except Exception as exc:
        result["confirmed"] = False
        result['error_type'] = type(exc).__name__
    finally:
        result.update(pending_request_count=len(tracker.pending), failed_request_count=tracker.failures)
        if html is not None:
            result['html_sha256'] = hashlib.sha256(html.encode('utf-8')).hexdigest()
            if local_capture is not None:
                local_capture['html'] = html
    return result
