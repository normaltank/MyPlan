"""Bound literal image references to a captured WebPlus widget, never to a URL allowlist."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json

from server.pager_evidence import terminal_pagers as _terminal_pagers


def install_stylesheet_capture(page):
    responses = []
    page._faculty_pager_stylesheets = responses
    generation = 0
    requests = {}
    def navigation(request):
        nonlocal generation
        try:
            if request.is_navigation_request() and request.frame == page.main_frame:
                generation += 1
                requests.clear()
                responses.clear()
            if request.resource_type == 'stylesheet' and request.frame == page.main_frame:
                requests[request] = generation
        except Exception:
            page._faculty_pager_stylesheet_observer_failed = True
    def received(response):
        if response.request.resource_type == 'stylesheet' and requests.get(response.request) == generation:
            responses.append(response)
    page.on('request', navigation)
    page.on('response', received)


_REFERENCES = r'''args => {
  const root=document.querySelector(args.source_widget_selector);
  const pager=document.querySelector(args.pager_selector);
  const all=[...document.querySelectorAll('*')];
  if(!root || !pager || all.length>5000 || args.failures.length>32 || all.some(n=>n.shadowRoot)
      || document.querySelector('base,svg,object,embed,iframe'))
    return {confirmed:false,reason:'unsupported_document_scope'};
  const path=node=>{const parts=[];for(let n=node;n;n=n.parentElement){
    const index=n.parentElement?[...n.parentElement.children].indexOf(n)+1:1;
    parts.unshift(n.tagName.toLowerCase()+':nth-child('+index+')');}return parts.join('>');};
  const place=node=>({path:path(node),within_roster:root.contains(node),within_pager:pager.contains(node),
    contains_scope:node.contains(root)||node.contains(pager)});
  const absolute=(value,base)=>{try{return new URL(value,base).href;}catch{return null;}};
  const refs=new Map(args.failures.map(f=>[f[0],[]])), sheets=[], seen=new Set();
  let reason=null, count=0, referenceNodes=0;
  const candidates=value=>{const parts=value.split(','), result=[];
    for(const part of parts){const words=part.trim().split(/\s+/);
      if(!words[0] || words.length>2 || (parts.length>1&&words.length!==2)
         || (words.length===2&&!/^(?:\d+w|\d+(?:\.\d+)?x)$/.test(words[1])) || words[0].startsWith('data:'))
        {reason='unparsed_srcset';continue;} result.push(absolute(words[0],location.href));
    } return result;
  };
  const inspect=(style,selector,nodes,base,location)=>{
    if(reason==='reference_limit') return;
    for(const property of style){
      const value=style.getPropertyValue(property);
      if(!/url\s*\(/i.test(value)) continue;
      if(value.includes('\\')){reason='escaped_css_url';continue;}
      const matches=[...value.matchAll(/url\(\s*(["']?)(.*?)\1\s*\)/gi)];
      if(!matches.length){reason='unparsed_css_url';continue;}
      for(const match of matches){
        const url=absolute(match[2],base); if(!refs.has(url)) continue;
        if(property!=='background-image'){reason='unsupported_image_property';continue;}
        if(!/^url\(\s*(["']?)(.*?)\1\s*\)$/i.test(value)){reason='unsupported_image_value';continue;}
        let structural=selector, candidates=nodes;
        if(candidates===null){
          if(!/^[A-Za-z0-9_#.*\s>:,-]+$/.test(selector)){
            reason='unsupported_image_selector';continue;
          }
          structural=selector.replace(/::?(?:before|after)\b/g,'')
            .replace(/:(?:focus-visible|focus-within|hover|active|focus|link|visited)\b/g,'');
          if(structural.includes(':')){reason='unsupported_image_selector';continue;}
          try{candidates=[...document.querySelectorAll(structural)];}
          catch{reason='unsupported_image_selector';continue;}
        }
        if(referenceNodes+candidates.length>128){reason='reference_limit';return;}
        referenceNodes+=candidates.length;
        const places=candidates.map(place);
        refs.get(url).push({kind:'css',selector,structural_selector:structural,property,
          location,places});
      }
    }
  };
  const sheet=stylesheet=>{
    if(seen.has(stylesheet)) return;
    seen.add(stylesheet);
    if(seen.size>32){reason='stylesheet_limit';return;}
    const item={href:stylesheet.href,disabled:stylesheet.disabled,media:stylesheet.media.mediaText,rules:[]};
    sheets.push(item);
    const walk=(rules,depth)=>{
      if(depth>8){reason='css_depth_limit';return;}
      for(const rule of rules){
        if(++count>4000){reason='css_rule_limit';return;}
        item.rules.push(rule.cssText);
        if(!rule.selectorText && [...rule.cssText.matchAll(/url\(\s*(["']?)(.*?)\1\s*\)/gi)]
            .some(m=>refs.has(absolute(m[2],stylesheet.href||location.href)))) reason='unsupported_image_rule';
        if(rule.style && rule.selectorText)
          inspect(rule.style,rule.selectorText,null,stylesheet.href||location.href,{sheet:sheets.indexOf(item),rule:item.rules.length-1});
        else if(rule.style && /url\s*\(/i.test(rule.style.cssText)){
          // A custom property/font/keyframe cannot establish an element's image scope.
          const urls=[...rule.style.cssText.matchAll(/url\(\s*(["']?)(.*?)\1\s*\)/gi)];
          if(urls.some(m=>refs.has(absolute(m[2],stylesheet.href||location.href)))) reason='unsupported_image_rule';
        }
        if(reason==='reference_limit') return;
        if(rule.styleSheet) sheet(rule.styleSheet);
        else if(rule.cssRules) walk(rule.cssRules,depth+1);
      }
    };
    try{walk(stylesheet.cssRules,0);}catch{reason='stylesheet_unreadable';}
  };
  for(const stylesheet of document.styleSheets) sheet(stylesheet);
  for(const node of all){
    if(node.style) inspect(node.style,null,[node],location.href,{inline_path:path(node)});
    if(node.hasAttribute('background'))
      reason='unsupported_native_image_reference';
    const tag=node.tagName.toUpperCase();
    if(tag==='IMG'||tag==='SOURCE'||(tag==='INPUT'&&node.type==='image')||tag==='VIDEO'){
      const attributes=[];
      if((tag==='IMG'||tag==='INPUT')&&node.getAttribute('src')) attributes.push(['src',absolute(node.getAttribute('src'),location.href)]);
      if((tag==='IMG'||tag==='SOURCE')&&node.getAttribute('srcset'))
        for(const url of candidates(node.getAttribute('srcset'))) attributes.push(['srcset',url]);
      if(tag==='VIDEO'&&node.getAttribute('poster')) attributes.push(['poster',absolute(node.getAttribute('poster'),location.href)]);
      for(const [attribute,url] of attributes) if(refs.has(url))
        {if(referenceNodes>=128){reason='reference_limit';break;} referenceNodes++;
         refs.get(url).push({kind:'dom',tag:tag.toLowerCase(),attribute,places:[place(node)]});}
    }else if(['IMAGE','USE','OBJECT','EMBED','IFRAME','LINK'].includes(tag)){
      const urls=['href','xlink:href','src','data'].map(a=>absolute(node.getAttribute(a)||'',location.href));
      if(urls.some(url=>refs.has(url))) reason='unsupported_image_reference';
    }
  }
  const references=[...refs].map(([url,items])=>({url,items}));
  if(references.reduce((n,row)=>n+row.items.reduce((m,item)=>m+item.places.length,0),0)>128) reason='reference_limit';
  const inside=references.some(row=>row.items.some(item=>item.places.some(p=>p.within_roster||p.within_pager||p.contains_scope)));
  const absent=references.some(row=>!row.items.some(item=>item.places.length));
  return {confirmed:!reason&&!inside&&!absent,reason:reason||(inside?'image_inside_widget':absent?'image_reference_missing':null),
    references,css_snapshot:{sheets}};
}'''


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


async def _scope(page, facts, tracker, materials):
    failures = list(tracker.failed_requests)
    document = tracker.document_response
    basic = {'failed_requests': failures, 'script_errors': tracker.script_errors,
             'document_url': getattr(document, 'url', None), 'document_status': getattr(document, 'status', None)}
    if document is None or document.status != 200 or tracker.script_errors:
        return {**basic, 'confirmed': False, 'reason': 'document_or_script_unverified'}
    if not failures:
        return {**basic, 'confirmed': True, 'references': [], 'material_refs': []}
    if tracker.failures != len(failures) or any(kind != 'image' or status not in (404, 410)
                                               for _, kind, status in failures):
        return {**basic, 'confirmed': False, 'reason': 'non_image_or_transient_failure'}
    observed = await page.evaluate(_REFERENCES, {**facts, 'failures': failures})
    if not isinstance(observed, dict) or observed.get('confirmed') is not True:
        return {**basic, 'confirmed': False, 'reason': (observed or {}).get('reason', 'scope_unreadable')}
    snapshot = observed.pop('css_snapshot')
    if len(json.dumps(snapshot).encode()) > 256 * 1024:
        return {**basic, 'confirmed': False, 'reason': 'css_snapshot_limit'}
    key = _digest(snapshot)
    materials[key] = {'kind': 'cssom', 'value': snapshot}
    refs = [key]
    recorded = getattr(page, '_faculty_pager_stylesheets', None)
    if recorded is None or getattr(page, '_faculty_pager_stylesheet_observer_failed', False):
        return {**basic, 'confirmed': False, 'reason': 'stylesheet_observer_missing'}
    total = 0
    for sheet in snapshot['sheets']:
        if not sheet['href']:
            continue
        matches = [r for r in recorded if r.url == sheet['href'] and r.status == 200]
        if not matches:
            return {**basic, 'confirmed': False, 'reason': 'stylesheet_original_missing'}
        try:
            raw = await asyncio.wait_for(matches[-1].body(), 8)
        except Exception:
            return {**basic, 'confirmed': False, 'reason': 'stylesheet_original_unreadable'}
        total += len(raw)
        if total > 256 * 1024:
            return {**basic, 'confirmed': False, 'reason': 'stylesheet_original_limit'}
        original = {'kind': 'stylesheet', 'url': sheet['href'], 'sha256': hashlib.sha256(raw).hexdigest(),
                    'body_base64': base64.b64encode(raw).decode()}
        key = _digest(original)
        materials[key] = original
        refs.append(key)
    if failures != tracker.failed_requests or document is not tracker.document_response:
        return {**basic, 'confirmed': False, 'reason': 'requests_changed'}
    return {**basic, **observed, 'material_refs': refs}


async def terminal_pagers(page, selector):
    materials = {}
    async def scope(page, facts, tracker):
        return await _scope(page, facts, tracker, materials)
    proofs = await _terminal_pagers(page, selector, _scope_pending=True, _failed_image_scope=scope)
    return proofs, materials
