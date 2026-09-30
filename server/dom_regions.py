"""Conservative DOM chrome recognition shared by directory and profile parsing."""
import re

from bs4 import Tag


_CHROME_TAGS = {"script", "style", "noscript", "template", "svg", "iframe", "header", "footer", "nav"}
_CHROME_NAME = re.compile(
    r"^(?:(?:site|page|global|wp|main)[-_])?(?:footer|nav|menu|sidebar|copyright|bottom)$",
    re.I,
)
_HEADER_NAME = re.compile(r"^(?:(?:site|page|global|wp|main)[-_])?header$", re.I)


def is_noise_element(tag: Tag) -> bool:
    """Recognize whole structural names, never arbitrary substrings in authored blocks."""
    if not isinstance(tag, Tag):
        return False
    if tag.name in _CHROME_TAGS:
        return True
    names = [*tag.get("class", []), tag.get("id") or ""]
    if any(_CHROME_NAME.fullmatch(name) for name in names):
        return True
    return any(_HEADER_NAME.fullmatch(name) for name in names)
