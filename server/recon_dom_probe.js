/* Runs inside the real browser. Return structure, never full HTML or biographies. */
(() => {
  const path = element => {
    if (!element || element.nodeType !== 1) return null;
    const parts = [];
    for (let node = element; node && node !== document.documentElement; node = node.parentElement) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        const id = '#' + CSS.escape(node.id);
        if (document.querySelectorAll(id).length === 1) return [id, ...parts].join(' > ');
      }
      const classes = [...node.classList].slice(0, 3);
      if (classes.length) part += classes.map(c => '.' + CSS.escape(c)).join('');
      const siblings = [...(node.parentElement?.children || [])].filter(n => n.tagName === node.tagName);
      if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
      parts.unshift(part);
    }
    return parts.join(' > ');
  };
  const shape = node => ({tag: node.tagName.toLowerCase(), classes: [...node.classList].slice(0, 3),
    children: [...node.children].slice(0, 6).map(n => n.tagName.toLowerCase()),
    anchors: node.querySelectorAll('a[href]').length, images: node.querySelectorAll('img').length});
  const page = (items, offset, limit, maxLimit = 24) => {
    offset = Math.max(0, Math.trunc(Number(offset) || 0));
    limit = Math.min(maxLimit, Math.max(1, Math.trunc(Number(limit) || 8)));
    const end = Math.min(items.length, offset + limit);
    return {url: location.href, total: items.length, offset, end,
      nextOffset: end < items.length ? end : null, items: items.slice(offset, end)};
  };
  const collapse = text => (text || '').replace(/\s+/g, ' ').trim();
  Object.defineProperty(window, '__facultyRecon', {configurable: true, value: Object.freeze({
    navigation(selector = 'body', offset = 0, limit = 64, query = '') {
      const root = document.querySelector(selector);
      if (!root) return {error: 'container_empty', url: location.href};
      const seen = new Set();
      const words = String(query || '').split(/[|,，\s]+/).map(w => w.trim().toLowerCase()).filter(Boolean);
      const links = [...root.querySelectorAll('a[href]')].filter(a => {
        if (!/^https?:/.test(a.href) || seen.has(a.href)) return false;
        seen.add(a.href); return true;
      }).map(a => ({url: a.href, label: (a.textContent || a.getAttribute('title') || '').trim().slice(0, 32)}))
        .filter(link => !words.length || words.some(w => link.label.toLowerCase().includes(w)
          || decodeURIComponent(link.url).toLowerCase().includes(w)));
      const result = page(links, offset, limit, 64);
      if (words.length) result.query = words;
      return result;
    },
    summary() {
      // Answers "what is this page" in the same call as navigation. Text lines
      // are deduplicated per session by the Python tool, so repeated site
      // chrome (menus, footers) is sent once.
      const lines = (document.body.innerText || '').split('\n').map(collapse).filter(Boolean)
        .map(line => line.slice(0, 80)).slice(0, 400);
      const headings = [...document.querySelectorAll('h1, h2, h3')].map(h => collapse(h.textContent).slice(0, 40))
        .filter(Boolean).slice(0, 4);
      const containers = [];
      for (const selector of ['#vsb_content', '.v_news_content', '.wp_articlecontent', 'article', 'main']) {
        const node = document.querySelector(selector);
        if (!node) continue;
        containers.push({selector, count: document.querySelectorAll(selector).length,
          text_chars: collapse(node.innerText).length, images: node.querySelectorAll('img').length,
          links: node.querySelectorAll('a[href]').length, tables: node.querySelectorAll('table').length});
      }
      return {url: location.href, title: collapse(document.title).slice(0, 80), headings, lines,
        images: document.images.length, tables: document.querySelectorAll('table').length,
        iframes: document.querySelectorAll('iframe, embed, object').length, containers};
    },
    structure(selector = 'body', offset = 0, limit = 8) {
      const root = document.querySelector(selector);
      if (!root) return {error: 'container_empty', url: location.href};
      const groups = [...root.querySelectorAll('*')].filter(n => n.children.length >= 3).map(n => {
        const counts = new Map();
        for (const child of n.children) {
          const signature = JSON.stringify(shape(child));
          counts.set(signature, (counts.get(signature) || 0) + 1);
        }
        const [signature, count] = [...counts].sort((a, b) => b[1] - a[1])[0];
        return {container: path(n), childCount: n.children.length, repeatedCount: count,
          shape: JSON.parse(signature)};
      }).filter(g => g.repeatedCount >= 3);
      groups.sort((a, b) => b.repeatedCount - a.repeatedCount);
      return page(groups, offset, limit);
    },
    isPaginationControl(control, root = document.body, rosterNodes = null) {
      // A slide component's arrow moves its track, not the adjacent roster.
      // Require the initialized component tree; a class called "next" alone
      // must never suppress an actual roster pagination control.
      const slider = control.parentElement;
      if (control.matches('button.slick-next,button.slick-prev')
          && slider?.matches('.slick-slider.slick-initialized')) {
        const track = slider.querySelector(':scope > .slick-list > .slick-track');
        if (track && track.children.length >= 2
            && [...track.children].every(n => n.matches('.slick-slide'))
            && (!rosterNodes || !rosterNodes.some(n => slider.contains(n)))) return false;
      }
      if (control.relList?.contains('next')) return true;
      for (let node = control; node && node !== root; node = node.parentElement) {
        const tokens = [...node.classList, node.id || ''].join(' ');
        if (/(?:^|[-_\s])(?:next|nextpage|pagination|pager|pages)(?:$|[-_\s])/i.test(tokens)
            || (node.getAttribute('aria-label') || '').toLowerCase().includes('pagination')) return true;
      }
      return false;
    },
    pagination(selector = 'body', offset = 0, limit = 12, nativeDisabledOnly = false, rosterSelector = null) {
      const root = document.querySelector(selector);
      if (!root) return {error: 'container_empty', url: location.href};
      const rosterNodes = rosterSelector ? [...document.querySelectorAll(rosterSelector)] : null;
      const candidates = [...root.querySelectorAll(nativeDisabledOnly ? 'a,button,input' : 'a,button')].filter(control => {
        if (control.matches(':disabled') || (!nativeDisabledOnly && control.getAttribute('aria-disabled') === 'true')) return false;
        return this.isPaginationControl(control, root, rosterNodes);
      }).filter(n => nativeDisabledOnly || n.getAttribute('aria-current') !== 'page').map(n => ({selector: path(n),
        url: n.href || null, label: (n.textContent || '').trim().slice(0, 16)}));
      return page(candidates, offset, limit);
    },
    profileCandidates(selector = null, limit = 6) {
      // Nested containers that could hold a complete profile: unique CSS
      // selectors, sizes and counts only. With a rejected selector, look inside
      // it and at its nearest ancestors; otherwise rank the page's text blocks.
      let base = null;
      if (selector) {
        try { const found = document.querySelectorAll(selector); if (found.length === 1) base = found[0]; }
        catch (e) { base = null; }
      }
      const chrome = 'header, footer, nav, script, style, noscript, template';
      const blocks = 'div, section, article, main, td';
      const own = n => {
        const text = collapse(n.innerText).length;
        const linked = [...n.querySelectorAll('a')].reduce((sum, a) => sum + collapse(a.innerText).length, 0);
        return {text, plain: Math.max(0, text - linked)};
      };
      let pool = base ? [base, ...base.querySelectorAll(blocks)] : [...document.querySelectorAll(blocks)];
      if (base) for (let p = base.parentElement, k = 0; p && p !== document.body && k < 3; p = p.parentElement, k++) pool.push(p);
      pool = pool.filter(n => n !== document.body && !n.matches(chrome) && !n.closest('header, footer, nav'));
      const facts = new Map(pool.map(n => [n, own(n)]));
      const top = Math.max(0, ...[...facts.values()].map(f => f.plain));
      const depth = n => { let d = 0; for (let p = n; p; p = p.parentElement) d++; return d; };
      const kept = pool.filter(n => top > 0 && facts.get(n).plain >= top * 0.4);
      // A wrapper whose only rendered child carries the same text and images is
      // the same content: one candidate that answers to either node's selectors.
      const only = n => {
        const kids = [...n.children].filter(c => !c.matches('script, style, noscript, template, br'));
        return kids.length === 1 ? kids[0] : null;
      };
      const same = (a, b) => facts.get(a).text === facts.get(b).text
        && a.querySelectorAll('img').length === b.querySelectorAll('img').length;
      const groupOf = new Map(kept.map(n => [n, [n]]));
      for (const n of [...kept].sort((a, b) => depth(b) - depth(a))) {
        const child = only(n);
        if (child && groupOf.has(child) && same(n, child)) {
          const merged = [...groupOf.get(child), ...groupOf.get(n)];
          for (const member of merged) groupOf.set(member, merged);
        }
      }
      const unique = s => { try { return document.querySelectorAll(s).length === 1; } catch (e) { return false; } };
      const selectorsOf = group => {
        const classes = [], ids = [];
        for (const n of group) {
          const tag = n.tagName.toLowerCase();
          for (const c of n.classList) { const s = tag + '.' + CSS.escape(c); if (unique(s)) classes.push(s); }
          if (n.id && unique('#' + CSS.escape(n.id))) ids.push('#' + CSS.escape(n.id));
        }
        return [...new Set([...classes, ...ids, path(group[0])])].filter(s => s && !/[㐀-鿿]/.test(s));
      };
      const groups = [...new Set(groupOf.values())].filter(group => !group.includes(base))
        .sort((a, b) => depth(b[0]) - depth(a[0]));
      const items = groups.slice(0, Math.max(1, Math.min(10, Number(limit) || 6))).map(group => {
        const n = group[0];
        return {selectors: selectorsOf(group).slice(0, 4), text_chars: facts.get(n).text,
          text_share: top ? Math.round(facts.get(n).plain / top * 100) / 100 : 0,
          images: n.querySelectorAll('img').length, links: n.querySelectorAll('a[href]').length,
          tables: n.querySelectorAll('table').length,
          inside_rejected: Boolean(base && base.contains(n)),
          wraps_rejected: Boolean(base && group[group.length - 1].contains(base))};
      }).filter(item => item.selectors.length);
      return {url: location.href, items};
    },
    check(cardSelector, nextSelector = null) {
      const cards = [...document.querySelectorAll(cardSelector)];
      const visible = node => {
        if (!node.getClientRects().length) return false;
        for (let n = node; n; n = n.parentElement) {
          const style = getComputedStyle(n);
          if (n.hidden || n.getAttribute('aria-hidden') === 'true' || style.display === 'none'
              || style.visibility === 'hidden' || style.visibility === 'collapse'
              || style.contentVisibility === 'hidden' || Number(style.opacity) === 0) return false;
        }
        return true;
      };
      const visibleCards = cards.filter(visible);
      const next = nextSelector ? [...document.querySelectorAll(nextSelector)] : [];
      return {url: location.href, cardCount: cards.length, visibleCardCount: visibleCards.length,
        hiddenCardCount: cards.length - visibleCards.length,
        sample: cards.length ? shape(cards[0]) : null,
        linkedSamples: [...new Set(cards.map(n => n.matches('a[href]') ? n.href : n.querySelector('a[href]')?.href)
          .filter(url => {if(!url || !/^https?:/.test(url))return false;
            const target=new URL(url);target.hash='';const current=new URL(location.href);current.hash='';
            return target.href!==current.href;}))].slice(0, 2),
        nextCount: next.length, nextUrl: next.length === 1 ? next[0].href || null : null,
        nextDisabled: next.length === 1 ? Boolean(next[0].disabled || next[0].getAttribute('aria-disabled') === 'true') : null};
    }
  })});
})();
