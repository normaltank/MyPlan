"""Politeness limit shared by every college harvested in this process.

Colleges of one university usually live on one web server (``cs.x.edu.cn``,
``law.x.edu.cn`` ...). Each college keeps its own profile concurrency, and all
colleges still start at once; only the pages opened against the same site at
the same moment are bounded here. ``HARVEST_PER_SITE_CONCURRENCY=0`` disables
the limit.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import weakref
from typing import Any
from urllib.parse import urlsplit

DEFAULT_PER_SITE_CONCURRENCY = 8
_ENV = "HARVEST_PER_SITE_CONCURRENCY"
# Second-level labels under a two-letter country code (edu.cn, ac.uk, com.hk ...).
_SECOND_LEVEL = {"edu", "ac", "gov", "com", "net", "org"}

_registry: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, tuple[int, asyncio.Semaphore]]]" = (
    weakref.WeakKeyDictionary())


def per_site_limit() -> int:
    raw = os.environ.get(_ENV, "").strip()
    if not raw:
        return DEFAULT_PER_SITE_CONCURRENCY
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_PER_SITE_CONCURRENCY


def site_key(url: str) -> str | None:
    """The registrable site of a URL: ``cs.nju.edu.cn`` -> ``nju.edu.cn``."""
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not host:
        return None
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) >= 3 and labels[-2] in _SECOND_LEVEL and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def site_slot(url: str) -> Any:
    """Async context manager holding one page slot for the URL's site."""
    limit = per_site_limit()
    key = site_key(url)
    if limit <= 0 or key is None:
        return contextlib.nullcontext()
    semaphores = _registry.setdefault(asyncio.get_running_loop(), {})
    current = semaphores.get(key)
    if current is None or current[0] != limit:
        current = (limit, asyncio.Semaphore(limit))
        semaphores[key] = current
    return current[1]
