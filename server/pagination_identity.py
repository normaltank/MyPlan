"""Identify a verified forward control without depending on sibling ordinal.

Only detects a changed selector binding; it never chooses or clicks a replacement.
Labels stay hashed in local evidence, and scope follows the actual roster DOM.
"""

from __future__ import annotations

import hashlib
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


def _shape(node: Tag) -> dict:
    return {
        "tag": node.name,
        "id": str(node.get("id", "")),
        "classes": sorted(node.get("class", [])),
    }


def _identity(node: Tag, cards: list[Tag]) -> dict | None:
    if not cards:
        return None
    scope = next(
        (
            ancestor
            for ancestor in [node, *node.parents]
            if isinstance(ancestor, Tag)
            and all(
                card is ancestor or any(parent is ancestor for parent in card.parents)
                for card in cards
            )
        ),
        None,
    )
    if scope is None:
        return None
    scope_path = []
    for parent in node.parents:
        if parent is scope:
            break
        scope_path.append(_shape(parent))
    label = (
        node.get_text(" ", strip=True)
        or node.get("aria-label")
        or node.get("value")
        or ""
    )
    return {
        "tag": node.name,
        "scope": _shape(scope),
        "scope_path": scope_path,
        "control_shape": _shape(node),
        "label_sha256": hashlib.sha256(label.encode()).hexdigest() if label else None,
        "rel_next": "next" in node.get("rel", []),
        "relation_roles": sorted(set(node.get("rel", [])) & {"next", "prev"}),
        "unlabelled_shape": [_shape(node), _shape(node.parent)],
    }


def next_identity(html: str, card_selector: str, next_selector: str) -> dict | None:
    soup = BeautifulSoup(html, "html.parser")
    controls = soup.select(next_selector)
    if len(controls) != 1:
        return None
    cards = soup.select(card_selector)
    identity = _identity(controls[0], cards)
    if identity is not None:
        identity["binding_count"] = sum(
            same_binding(identity, _identity(node, cards))
            for node in soup.find_all(identity["tag"])
        )
    return identity


def same_binding(original: dict, current: dict | None) -> bool:
    """Match the observed DOM role even when its label changes."""
    return (
        current is not None
        and original["tag"] == current["tag"]
        and original["scope"] == current["scope"]
        and original["scope_path"] == current["scope_path"]
        and original["control_shape"] == current["control_shape"]
    )


def same_identity(original: dict, current: dict | None) -> bool:
    if not same_binding(original, current):
        return False
    if original["relation_roles"] != current["relation_roles"]:
        return False
    if original["rel_next"]:
        return current["rel_next"]
    if original["label_sha256"] is not None:
        return original["label_sha256"] == current["label_sha256"]
    return original["unlabelled_shape"] == current["unlabelled_shape"]


def _retained_components(
    soup: BeautifulSoup, cards: list[Tag], original: dict
) -> list[Tag]:
    """Locate the original widget if it still exists, independently of its leaf.

    A link's immediate parent may be its forward slot; the next ancestor is
    the enclosing widget. Keep the full outer path to distinguish peer widgets.
    """
    path = original["scope_path"]
    if not path:
        return []
    index = 1 if len(path) > 1 else 0
    shape = path[index]
    found = []
    for node in soup.find_all(shape["tag"]):
        if _shape(node) != shape:
            continue
        identity = _identity(node, cards)
        if (
            identity is not None
            and identity["scope"] == original["scope"]
            and identity["scope_path"] == path[index + 1 :]
        ):
            found.append(node)
    return found


def displaced_controls(
    html: str, card_selector: str, original: dict, url: str
) -> list[str | None]:
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(card_selector)
    components = _retained_components(soup, cards, original)
    found = []
    for node in soup.find_all(list({original["tag"], "a", "button", "input"})):
        if node.has_attr("disabled"):
            continue
        current = _identity(node, cards)
        if current is None or original["scope"] != current["scope"]:
            continue
        if components and not any(
            parent is widget for widget in components for parent in node.parents
        ):
            continue
        # An unchanged control slot, or an observed forward role in the same
        # roster component, is an unresolved lead even if leaf attributes drift.
        # These leads only block an unproven end; they never authorize a click.
        same_slot = original["scope_path"] == current["scope_path"]
        same_label_path = (
            original["label_sha256"] is not None
            and original["label_sha256"] == current["label_sha256"]
            and [p["tag"] for p in original["scope_path"]]
            == [p["tag"] for p in current["scope_path"]]
        )
        widget_changed = (
            not components
            and bool(original["scope_path"])
            and [p["tag"] for p in original["scope_path"]]
            == [p["tag"] for p in current["scope_path"]]
        )
        if same_slot or current["rel_next"] or same_label_path or widget_changed:
            href = node.get("href", "").strip()
            found.append(
                urljoin(url, href)
                if href and not href.startswith(("javascript:", "#"))
                else None
            )
    return found
