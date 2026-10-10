"""Hidden Word TOC fields, proved by their document-link and row relationship."""

from __future__ import annotations

import re
from typing import Callable
from urllib.parse import unquote, urlsplit
from bs4 import BeautifulSoup, Tag

MEDIA = {
    "img",
    "picture",
    "svg",
    "canvas",
    "iframe",
    "object",
    "embed",
    "video",
    "audio",
}
LEADER = re.compile(r"[-‐‑‒–—.…·\s]+")
PAGE = re.compile(r"[0-9]+")

# Shared by live verification and live failure explanations. Bindings originate
# in the exact Python-parsed capture, not in selectors proposed by the model.
LIVE_GUARD = r"""
  const tocRoots=(args.wordToc||[]).map(item=>({...item,node:document.querySelector(item.selector),
    anchor:document.querySelector(item.anchor_selector)}));
  const toc=n=>tocRoots.some(item=>{
    const w=item.node;
    if(!w || w.tagName!=='SPAN' || !(w===n || w.contains(n))
       || w.closest('a')?.getAttribute('href')!==item.href
       || [...w.querySelectorAll('*')].some(child=>child.tagName!=='SPAN')) return false;
    const text=(w.textContent||'').trim();
    return item.kind==='page' ? /^[0-9]+$/.test(text) : /^[-‐‑‒–—.…·\s]+$/.test(text);
  });
  const tocContext=n=>tocRoots.some(item=>item.anchor && n.closest('a')===item.anchor);
"""


def toc_anchor(node: Tag) -> Tag | None:
    anchor = node.find_parent("a")
    if anchor is None or anchor.find_parent("p") is None:
        return None
    try:
        parsed = urlsplit(str(anchor.get("href", "")))
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"", "file", "http", "https"}
        or not unquote(parsed.path).lower().endswith((".doc", ".docx"))
        or not re.fullmatch(r"_Toc[0-9]+", parsed.fragment)
    ):
        return None
    return anchor


def decoration_kind(node: Tag, hidden: Callable[[Tag], bool]) -> str | None:
    """A visible heading followed by hidden leaders and a final page field.

    No word length allowance is used. Unknown fields or media invalidate the
    row's metadata proof instead of donating their contents to the profile.
    """
    anchor = toc_anchor(node)
    if anchor is None or anchor.find(list(MEDIA)) or anchor.find("a"):
        return None
    members = []
    for item in anchor.find_all(True):
        if hidden(item) and not any(
            parent is accepted for accepted, _ in members for parent in item.parents
        ):
            if item.name != "span" or any(
                child.name != "span" for child in item.find_all(True)
            ):
                return None
            text = item.get_text("", strip=True)
            kind = (
                "leader"
                if text and LEADER.fullmatch(text)
                else "page"
                if PAGE.fullmatch(text)
                else None
            )
            if kind is None:
                return None
            members.append((item, kind))
    kinds = [kind for _, kind in members]
    if not kinds or kinds[0] != "leader" or kinds[-1] != "page":
        return None
    first_page = kinds.index("page")
    if any(kind != "page" for kind in kinds[first_page:]):
        return None
    # At least a visible heading is needed; pure hidden decoration is not a TOC.
    visible = "".join(
        str(text)
        for text in anchor.strings
        if not any(parent is member for member, _ in members for parent in text.parents)
    )
    if not re.search(r"[A-Za-z\u4e00-\u9fff]", visible):
        return None
    return next((kind for item, kind in members if item is node), None)


def node_selector(node: Tag) -> str:
    """An exact DOM ordinal path; no source text or identifiers enter the selector."""
    parts = []
    for item in [node, *node.parents]:
        if not isinstance(item, Tag) or item.name == "[document]":
            break
        siblings = (
            item.parent.find_all(item.name, recursive=False) if item.parent else [item]
        )
        ordinal = next(
            index for index, sibling in enumerate(siblings, 1) if sibling is item
        )
        parts.append(f"{item.name}:nth-of-type({ordinal})")
    return " > ".join(reversed(parts))


def live_decorations(html: str, selector: str) -> list[dict]:
    """Private source bindings for live facts, never a model-supplied allowlist."""
    from server.profile_content import _inline_hidden

    soup = BeautifulSoup(html, "html.parser")
    roots = soup.select(selector)
    if len(roots) != 1:
        return []
    return [
        {
            "selector": node_selector(node),
            "anchor_selector": node_selector(toc_anchor(node)),
            "href": toc_anchor(node).get("href"),
            "kind": kind,
        }
        for node in roots[0].find_all(True)
        if _inline_hidden(node)
        for kind in [decoration_kind(node, _inline_hidden)]
        if kind is not None
    ]
