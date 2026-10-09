"""Live structural proof for a profile container; never return its contents."""
from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any
from urllib.parse import urlsplit


_FACTS = r"""args => {
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
  const media='img,picture,svg,canvas,iframe,object,embed,video,audio';
  const relevant=retained.filter(n=>!n.matches('script,style,noscript,template')
    && ((n.textContent||'').trim() || n.matches(media)));
  // Same rule as profile_content.trivial_hidden: an editor placeholder such as
  // 'invisible' is not hidden profile content; names, numbers, e-mails or Chinese are.
  const wordHidden=n=>(n.getAttribute('style')||'').replace(/\s+/g,'').toLowerCase().includes('mso-hide:all');
  const trivial=n=>!n.matches(media) && !n.querySelector(media)
    && (n.textContent||'').trim().length<=args.trivialChars
    && (!/[\u4e00-\u9fff@\d]/.test(n.textContent||'') || wordHidden(n));
  const hidden=relevant.filter(n=>!visible(n) && !trivial(n)).length;
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


_CONTENT_IMAGES = """args => {
  const root=document.querySelector(args.selector);
  if(!root) return args.urls.map(()=>true);
  return args.urls.map(url=>[...root.querySelectorAll('img')].some(img=>img.currentSrc===url || img.src===url));
}"""

# Requests that cannot change the text or images inside a profile container.
_PRESENTATION_TYPES = {"font", "media", "manifest", "texttrack"}
# A stylesheet or script the server answers 404/410 is missing for every reader,
# so the page as rendered is the page as published. So is one that fails the
# same way again after the page is opened anew (a mixed-content block, a 404
# page Chromium refuses as a stylesheet). Any other failure may be transient
# (and could change what is hidden), so it still blocks.
_GONE = (404, 410)
_RECURRING_TYPES = {"script", "stylesheet"}


def failed_resources(failed: list[tuple[str, str, int | None]]) -> list[tuple[str, str, int | None]]:
    """Failures as (address without query, type, status), comparable across page opens."""
    return [(urlsplit(url)._replace(query="", fragment="").geturl(), kind, status)
            for url, kind, status in failed]


# An image inside the profile the server answers 404/410 on two opens is missing
# for every reader. When the room the page gives it is no larger than an icon
# (MAX_MISSING_ICON_PX square, badr 2026-10-09), it could never have shown a
# line of a biography, so the profile as rendered is the profile as published
# (an editor's leftover placeholder, a broken 22x22 'spacer.gif'). A loaded 1x1
# copy laid out in its place measures the room: only the page's own sizing
# (attributes, CSS) makes it larger. A broken image's own icon is the
# browser's, not the page's, so it is not measured.
MAX_MISSING_ICON_PX = 32
_UNSIZED_IMAGES = """async args => {
  const root=document.querySelector(args.selector);
  if(!root) return args.urls.map(()=>false);
  const pixel='data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
  const result=[];
  for(const url of args.urls){
    const images=[...root.querySelectorAll('img')].filter(img=>img.currentSrc===url || img.src===url);
    let unsized=images.length>0;
    for(const img of images){
      if(img.parentElement && img.parentElement.tagName==='PICTURE'){ unsized=false; continue; }
      const probe=img.cloneNode(false);
      probe.removeAttribute('srcset');
      probe.removeAttribute('loading');
      probe.removeAttribute('id');
      probe.src=pixel;
      img.after(probe);
      let loaded=true;
      try { await probe.decode(); } catch(error) { loaded=false; }
      const box=probe.getBoundingClientRect();
      probe.remove();
      if(!loaded || box.width>args.limit || box.height>args.limit) unsized=false;
    }
    result.push(unsized);
  }
  return result;
}"""


# Where each pending image's <img> is: "outside" the container, "inside" it,
# or "" when no <img> element names it (a CSS background, a preload, a script).
_PENDING_IMAGE_PLACES = """args => {
  const root=document.querySelector(args.selector);
  if(!root) return args.requests.map(()=>"");
  return args.requests.map(item=>{
    if(item.resource_type!=="image") return "";
    const images=[...document.images].filter(img=>img.currentSrc===item.url || img.src===item.url);
    if(!images.length) return "";
    if(images.every(img=>!root.contains(img))) return "outside";
    return images.every(img=>root.contains(img)) ? "inside" : "";
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


def _profile_text(html: str, selector: str, start_selector: str | None, url: str) -> str:
    from bs4 import BeautifulSoup
    from server.profile_content import scope_profile_html
    scoped, evidence, issue = scope_profile_html(html, selector, url, start_selector=start_selector)
    if issue or not evidence:
        return ""
    return " ".join(BeautifulSoup(scoped, "html.parser").get_text(" ", strip=True).split())


def _profile_media(html: str, selector: str, start_selector: str | None, url: str) -> list:
    from bs4 import BeautifulSoup
    from server.profile_content import scope_profile_html
    scoped, _, issue = scope_profile_html(html, selector, url, start_selector=start_selector)
    if issue:
        return []
    return [(node.name, tuple((key, str(node.get(key, ''))) for key in
                             ('src', 'srcset', 'data', 'href', 'poster')))
            for node in BeautifulSoup(scoped, 'html.parser').find_all(
                ['img', 'picture', 'source', 'svg', 'canvas', 'iframe', 'object', 'embed', 'video', 'audio'])]


PROFILE_CANDIDATE_LIMIT = 4

# The live counterpart of profile_content.hidden_blockers for computed styles.
_HIDDEN_PATHS = r"""args => {
  const nodes=[...document.querySelectorAll(args.selector)];
  if(nodes.length!==1) return [];
  const root=nodes[0];
  const shown=n=>{
    if(!n.getClientRects().length) return false;
    const s=getComputedStyle(n);
    return !(n.hidden || n.getAttribute('aria-hidden')==='true' || s.display==='none' || s.visibility==='hidden'
      || s.visibility==='collapse' || s.contentVisibility==='hidden' || Number(s.opacity)===0);
  };
  const media='img,picture,svg,canvas,iframe,object,embed,video,audio';
  const label=n=>n.tagName.toLowerCase()+(n.id?'#'+n.id:'')+[...n.classList].slice(0,2).map(c=>'.'+c).join('');
  const where=n=>{const parts=[];for(let p=n;p&&p!==root&&parts.length<4;p=p.parentElement)parts.unshift(label(p));return parts.join(' > ');};
  const out=[];
  for(const n of root.querySelectorAll('*')){
    if(n.matches('script,style,noscript,template') || out.some(item=>item.node.contains(n))) continue;
    const text=(n.textContent||'').trim();
    if(!(text || n.matches(media)) || shown(n)) continue;
    if(!n.matches(media) && !n.querySelector(media) && text.length<=args.trivialChars
       && !/[一-鿿@\d]/.test(text)) continue;
    out.push({node:n, where:'descendant', path:where(n), text_chars:text.length,
      media:n.querySelectorAll(media).length+(n.matches(media)?1:0)});
    if(out.length>=3) break;
  }
  return out.map(({node, ...item})=>item);
}"""


async def profile_candidates(page: Any, selector: str | None = None, *, html: str | None = None,
                             limit: int = PROFILE_CANDIDATE_LIMIT) -> list[dict]:
    """Containers the scout may verify next, each already checked against the
    same static scope rules the harvest applies. Paths and counts only."""
    from server.profile_content import scope_profile_html
    try:
        probe = await page.evaluate("args => __facultyRecon.profileCandidates(args.selector, 10)",
                                    {"selector": selector})
    except Exception:
        return []
    if html is None:
        html = await page.content()
    passing, failing = [], []
    for item in (probe or {}).get("items") or []:
        # Every listed selector names the same content, so one static check covers them.
        _, evidence, issue = scope_profile_html(html, item["selectors"][0], page.url)
        item["static_check"] = "ok" if evidence else issue["status"]
        (passing if evidence else failing).append(item)
    return (passing + failing)[:limit]


async def failure_details(page: Any, html: str, result: dict) -> dict:
    """Why a proof failed, in paths and counts, so a scout need not probe for it."""
    from server.profile_content import TRIVIAL_HIDDEN_CHARS, hidden_blockers
    details = {}
    selector = result.get("profile_content_selector")
    if not selector:
        return details
    if result.get("reason") == "profile_content_selector_hidden":
        blockers = hidden_blockers(html, selector, start_selector=result.get("profile_content_start_selector"))
    elif result.get("reason") == "hidden_content":
        try:
            blockers = await page.evaluate(_HIDDEN_PATHS, {"selector": selector, "trivialChars": TRIVIAL_HIDDEN_CHARS})
        except Exception:
            blockers = []
    else:
        blockers = []
    if blockers:
        details["hidden_blockers"] = blockers
    return details


async def verify_profile_container(page: Any, selector: str, start_selector: str | None = None,
                                   *, local_capture: dict[str, Any] | None = None,
                                   recurring_failures: list[tuple[str, str]] | None = None) -> dict:
    """Return compact facts; an optional Python-only sink retains the bound HTML.

    Browser tools do not expose the sink. It prevents a second page.content()
    call from replacing the exact snapshot hashed by this proof. The sink also
    receives this open's ``failed_requests``; pass them back as
    ``recurring_failures`` when the page is opened again.
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
        activity, image_activity = tracker.activity, tracker.image_activity
        initially_idle = tracker.available and tracker.navigation_observed
        html = await page.content()
        _, first_evidence, issue = scope_profile_html(html, selector, page.url, start_selector=start_selector)
        if issue:
            result['scope_issue'] = result['reason'] = issue['status']
            return result
        from server.profile_content import TRIVIAL_HIDDEN_CHARS
        args = {"selector": selector, "start": start_selector, "trivialChars": TRIVIAL_HIDDEN_CHARS}
        first_facts = await page.evaluate(_FACTS, args)
        # A finite entrance animation is not evidence of an incomplete article.
        # Wait for two stable samples; continuing animation/DOM changes still block.
        settle_deadline = time.monotonic() + 2.0
        settle_samples = 0
        while True:
            await asyncio.sleep(0.3)
            html = await page.content()
            _, evidence, issue = scope_profile_html(html, selector, page.url, start_selector=start_selector)
            facts = await page.evaluate(_FACTS, args)
            settle_samples += 1
            selected_stable = bool(not issue and evidence and first_evidence
                                   and first_evidence['selected_html_sha256'] == evidence['selected_html_sha256'])
            visibility_stable = first_facts == facts and not facts['active_animation_count']
            stable = bool(selected_stable and visibility_stable)
            if stable or issue or time.monotonic() >= settle_deadline:
                break
            first_evidence, first_facts = evidence, facts
            activity, image_activity = tracker.activity, tracker.image_activity
        # A failed stylesheet, font or an image that is not inside the container
        # (theme icons, CSS backgrounds) cannot change the captured profile.
        failed = list(tracker.failed_requests)
        recurring = {tuple(item) for item in recurring_failures or ()}
        gone = {item for item, resource in zip(failed, failed_resources(failed))
                if item[2] in _GONE or (item[1] in _RECURRING_TYPES and resource in recurring)}
        presentation = [item for item in failed if item[1] in _PRESENTATION_TYPES
                        or (item[1] == 'stylesheet' and item in gone)]
        failed_images = [item for item in failed if item[1] == 'image']
        ignored = len(presentation)
        unpublished_images = []
        if failed_images and facts['container_count'] == 1:
            inside = await page.evaluate(_CONTENT_IMAGES,
                                         {'selector': selector, 'urls': [url for url, _, _ in failed_images]})
            ignored += sum(not value for value in inside)
            gone_inside = [item[0] for item, value, resource
                           in zip(failed_images, inside, failed_resources(failed_images))
                           if value and item[2] in _GONE and resource in recurring]
            # A body made only of images goes to image review instead.
            if gone_inside and _profile_text(html, selector, start_selector, page.url):
                unsized = await page.evaluate(_UNSIZED_IMAGES, {'selector': selector, 'urls': gone_inside,
                                                                  'limit': MAX_MISSING_ICON_PX})
                unpublished_images = [url for url, value in zip(gone_inside, unsized) if value]
                ignored += len(unpublished_images)
        # A permanently missing script or fetch/XHR (e.g. a visit counter) does
        # not block an intact server-rendered profile. Compare both text and
        # media references; browser-built content and transient errors still block.
        failed_content_requests = [item for item in failed
                                   if item[1] in {'script', 'xhr', 'fetch'} and item in gone]
        other_blocking = [item for item in failed if item not in presentation and item not in failed_content_requests
                          and item[1] != 'image']
        server_rendered = False
        if failed_content_requests and not other_blocking and evidence and tracker.document_response is not None:
            try:
                raw = await tracker.document_response.text()
                _, raw_evidence, raw_issue = scope_profile_html(raw, selector, page.url,
                                                                start_selector=start_selector)
                server_rendered = bool(not raw_issue and raw_evidence
                                       and _profile_text(raw, selector, start_selector, page.url)
                                       and _profile_text(raw, selector, start_selector, page.url)
                                       == _profile_text(html, selector, start_selector, page.url)
                                       and _profile_media(raw, selector, start_selector, page.url)
                                       == _profile_media(html, selector, start_selector, page.url))
            except Exception:
                server_rendered = False
            if server_rendered:
                ignored += len(failed_content_requests)
        # Failures no container choice can ignore, seen on both opens: the
        # page itself blocks every profile proof, whatever the plan says.
        page_blocking = [item for item, resource in zip(other_blocking, failed_resources(other_blocking))
                         if recurring_failures is not None and resource in recurring]
        pending_requests = [
            (getattr(request, 'url', ''), getattr(request, 'resource_type', ''))
            for request in tracker.pending
        ]
        ignored_pending = 0
        content_images_pending = 0
        if pending_requests and facts['container_count'] == 1:
            places = await page.evaluate(
                _PENDING_IMAGE_PLACES,
                {'selector': selector,
                 'requests': [{'url': url, 'resource_type': kind}
                              for url, kind in pending_requests]})
            ignored_pending = places.count('outside')
            # An <img> already in the captured, stable HTML whose bytes are
            # still arriving (a 13 MB artwork) cannot change that HTML. Its
            # picture is fetched and checked by the image audit on its own, so
            # it is recorded here instead of failing the proof.
            content_images_pending = places.count('inside')
        blocking_failures = tracker.failures - ignored
        blocking_pending = len(tracker.pending) - ignored_pending - content_images_pending
        idle = (initially_idle and blocking_pending == 0 and blocking_failures == 0
                and activity - image_activity == tracker.activity - tracker.image_activity)
        result.update(container_count=facts["container_count"], container_visible=facts["container_visible"],
                      dom_stable=stable, network_idle=idle,
                      ignored_external_image_failures=ignored,
                      server_rendered_content=server_rendered,
                      unpublished_images=sorted(set(unpublished_images)),
                      ignored_external_image_pending=ignored_pending,
                      pending_content_image_count=content_images_pending,
                      blocking_pending_request_count=blocking_pending,
                      blocking_failed_request_count=blocking_failures,
                      selected_dom_stable=selected_stable, visibility_stable=bool(visibility_stable),
                      network_activity_changed=(activity - image_activity
                                                != tracker.activity - tracker.image_activity),
                      hidden_content_count=facts['hidden_content_count'], start_valid=facts['start_valid'],
                      active_animation_count=facts['active_animation_count'])
        result['settle_samples'] = settle_samples
        if page_blocking:
            result['page_blocking_requests'] = [
                {"type": kind, "status": status, "path": urlsplit(url).path[-80:]}
                for url, kind, status in page_blocking[:3]]
        if evidence:
            result['selected_html_sha256'] = evidence['selected_html_sha256']
        if issue:
            result['scope_issue'] = issue['status']
        result["confirmed"] = (facts["container_count"] == 1 and facts["container_visible"]
                               and facts["start_valid"] and not facts['hidden_content_count'] and stable and idle)
        if not result["confirmed"]:
            # Say what failed, so a scout does not keep trying other selectors
            # for a page condition no selector can change.
            result["reason"] = ("container_not_unique" if facts["container_count"] != 1
                                else "container_hidden" if not facts["container_visible"]
                                else "start_invalid" if not facts["start_valid"]
                                else "hidden_content" if facts['hidden_content_count']
                                else "dom_unstable" if not stable
                                else "network_not_idle")
            if result["reason"] == "network_not_idle":
                blocking = [item for item in failed if item not in presentation
                            and not (item in failed_content_requests and server_rendered)]
                result["blocking_requests"] = [
                    {"type": kind, "status": status, "path": urlsplit(url).path[-80:]}
                    for url, kind, status in blocking[:3]]
    except Exception as exc:
        result["confirmed"] = False
        result['error_type'] = type(exc).__name__
    finally:
        result.update(pending_request_count=len(tracker.pending), failed_request_count=tracker.failures)
        if local_capture is not None:
            local_capture['failed_requests'] = failed_resources(tracker.failed_requests)
        if html is not None:
            result['html_sha256'] = hashlib.sha256(html.encode('utf-8')).hexdigest()
            if local_capture is not None:
                local_capture['html'] = html
    return result
