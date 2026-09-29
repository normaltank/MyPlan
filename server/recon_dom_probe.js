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
  Object.defineProperty(window, '__facultyRecon', {configurable: true, value: Object.freeze({
    navigation(selector = 'body', offset = 0, limit = 64) {
      const root = document.querySelector(selector);
      if (!root) return {error: 'container_empty', url: location.href};
      const seen = new Set();
      const links = [...root.querySelectorAll('a[href]')].filter(a => {
        if (!/^https?:/.test(a.href) || seen.has(a.href)) return false;
        seen.add(a.href); return true;
      }).map(a => ({url: a.href, label: (a.textContent || a.getAttribute('title') || '').trim().slice(0, 32)}));
      return page(links, offset, limit, 64);
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
    pagination(selector = 'body', offset = 0, limit = 12) {
      const root = document.querySelector(selector);
      if (!root) return {error: 'container_empty', url: location.href};
      const candidates = [...root.querySelectorAll('a,button')].filter(control => {
        if (control.disabled || control.getAttribute('aria-disabled') === 'true') return false;
        if (control.relList?.contains('next')) return true;
        for (let node = control; node && node !== root; node = node.parentElement) {
          const tokens = [...node.classList, node.id || ''].join(' ');
          if (/(?:^|[-_\s])(?:next|nextpage|pagination|pager|pages)(?:$|[-_\s])/i.test(tokens)
              || (node.getAttribute('aria-label') || '').toLowerCase().includes('pagination')) return true;
        }
        return false;
      }).filter(n => n.getAttribute('aria-current') !== 'page').map(n => ({selector: path(n),
        url: n.href || null, label: (n.textContent || '').trim().slice(0, 16)}));
      return page(candidates, offset, limit);
    },
    check(cardSelector, nextSelector = null) {
      const cards = [...document.querySelectorAll(cardSelector)];
      const next = nextSelector ? [...document.querySelectorAll(nextSelector)] : [];
      return {url: location.href, cardCount: cards.length,
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
