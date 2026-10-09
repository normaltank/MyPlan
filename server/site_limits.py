"""Politeness limit shared by every college harvested in this process.

Colleges of one university usually live on one web server (``cs.x.edu.cn``,
``law.x.edu.cn`` ...). Each college keeps its own profile concurrency, and all
colleges still start at once; only the pages opened against the same site at
the same moment are bounded here. ``HARVEST_PER_SITE_CONCURRENCY=0`` disables
the limit.

A site that answers with a rate-limit notice is paused for every process and
then paced (about one page request per second); see ``site_turn`` below.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import re
import sqlite3
import time
import weakref
from pathlib import Path
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


# ── Request pacing and site freezes, shared by every process ────────────────
# The concurrency slot above only bounds pages open at once inside the worker.
# A site counts page requests per second from our IP, across recon sessions
# (each a separate MCP process), the worker and the IDE batch tool, so the pace
# is kept in one small SQLite table every process reserves its turn from.
#
# Adaptive by default (badr 10-09: only one university has ever objected):
# - sites run at full speed until one answers with a rate-limit notice;
# - that freeze is recorded for the whole university site (we cannot tell
#   whether its firewall guards one host or the server); every request to it
#   waits until the freeze ends;
# - from then on its page requests (navigations, clicks) share one pace of
#   ``FROZEN_SITE_INTERVAL``, kept in the table for later runs too;
# - media originals (images, PDFs) are never paced, only held during a freeze.
# ``HARVEST_PER_SITE_INTERVAL_SECONDS`` sets a pace per host for every site
# as well (default 0: off).
DEFAULT_PER_SITE_INTERVAL = 0.0
FROZEN_SITE_INTERVAL = 1.0
_INTERVAL_ENV = "HARVEST_PER_SITE_INTERVAL_SECONDS"
_CLOCK_ENV = "HARVEST_SITE_CLOCK_DB"
_DEFAULT_CLOCK = Path(__file__).resolve().parent / "outputs" / "site-clock.sqlite3"
DEFAULT_FREEZE_SECONDS = 600.0
FREEZE_BOUNDS = (60.0, 3600.0)
# A site that answers with a rate-limit notice instead of the page. Matched only
# on short documents, where the notice is the whole page; this classifies access
# state and never filters teacher text.
_NOTICE_MAX_TEXT = 1500
_RATE_NOTICE = re.compile(
    r"CC\s*攻击|(?:请求|访问)(?:过于|太过|太)?频繁|访问频率|(?:并发|请求)(?:数)?(?:超过|超出)"
    r"|IP.{0,20}(?:冻结|封禁|封锁|拉黑|禁止访问|限制访问)"
    r"|too\s+many\s+requests|rate[\s-]?limit(?:ed)?|temporarily\s+(?:blocked|banned)",
    re.IGNORECASE)
_NOTICE_DURATION = re.compile(r"(\d{1,4})\s*(分钟|秒|minutes?|mins?|seconds?|secs?)", re.IGNORECASE)


class SiteRateLimited(RuntimeError):
    """The site refused us for request rate; its turns are frozen until ``until``."""

    def __init__(self, url: str, until: float, reason: str):
        self.site, self.until, self.reason = site_key(url), until, reason
        super().__init__(f"site_rate_limited {self.site}: {reason}; paused until "
                         f"{time.strftime('%H:%M:%S', time.localtime(until))}")


def per_site_interval() -> float:
    raw = os.environ.get(_INTERVAL_ENV, "").strip()
    if not raw:
        return DEFAULT_PER_SITE_INTERVAL
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return DEFAULT_PER_SITE_INTERVAL


def _host(url: str) -> str | None:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".") or None
    except ValueError:
        return None


def _clock() -> sqlite3.Connection:
    path = Path(os.environ.get(_CLOCK_ENV) or _DEFAULT_CLOCK)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30, isolation_level=None)
    # Pace rows are keyed by host (or by site once shared); freeze rows by site.
    connection.execute("CREATE TABLE IF NOT EXISTS pace (key TEXT PRIMARY KEY, next_at REAL NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS freeze (site TEXT PRIMARY KEY, "
                       "frozen_until REAL NOT NULL, shared INTEGER NOT NULL DEFAULT 1, reason TEXT)")
    return connection


def reserve_site_turn(url: str, *, paced: bool = True, now: float | None = None) -> float:
    """Reserve the next request start for the URL; return seconds to wait.

    Page requests (``paced``) on a site that froze us before share one pace
    across all processes (plus any configured per-host pace). No request,
    paced or not, starts before the site's recorded freeze ends.
    """
    site, host = site_key(url), _host(url)
    if site is None or host is None:
        return 0.0
    interval = per_site_interval() if paced else 0.0
    connection = _clock()
    try:
        connection.execute("BEGIN IMMEDIATE")
        current = time.time() if now is None else now
        row = connection.execute("SELECT frozen_until, shared FROM freeze WHERE site = ?", (site,)).fetchone()
        frozen_until, shared = row or (0.0, 0)
        turn = max(current, frozen_until)
        if paced and shared:
            # A site that froze us before is always paced.
            interval = max(interval, FROZEN_SITE_INTERVAL)
        if interval > 0:
            key = site if shared else host
            pace = connection.execute("SELECT next_at FROM pace WHERE key = ?", (key,)).fetchone()
            turn = max(turn, pace[0] if pace else 0.0)
            connection.execute("INSERT INTO pace (key, next_at) VALUES (?, ?) "
                               "ON CONFLICT(key) DO UPDATE SET next_at = excluded.next_at",
                               (key, turn + interval))
        connection.execute("COMMIT")
        return turn - current
    finally:
        connection.close()


async def site_turn(url: str, *, paced: bool = True) -> None:
    """Wait for the URL's next request turn (call before the request, outside its timeout)."""
    delay = await asyncio.to_thread(reserve_site_turn, url, paced=paced)
    if delay > 0:
        await asyncio.sleep(delay)


def site_is_paced(url: str) -> bool:
    """Whether page requests to the URL are paced (configured pace or an earlier freeze)."""
    site = site_key(url)
    if per_site_interval() > 0 or site is None:
        return per_site_interval() > 0
    connection = _clock()
    try:
        return connection.execute("SELECT 1 FROM freeze WHERE site = ? AND shared = 1",
                                  (site,)).fetchone() is not None
    finally:
        connection.close()


def wait_site_turn(url: str) -> None:
    delay = reserve_site_turn(url)
    if delay > 0:
        time.sleep(delay)


def freeze_site(url: str, seconds: float, reason: str) -> float:
    """Stop every process from starting requests on the URL's site for ``seconds``.

    The site's hosts share one pace from now on, including in later runs.
    """
    site = site_key(url)
    until = time.time() + min(max(seconds, FREEZE_BOUNDS[0]), FREEZE_BOUNDS[1])
    if site is None:
        return until
    connection = _clock()
    try:
        connection.execute("INSERT INTO freeze (site, frozen_until, shared, reason) VALUES (?, ?, 1, ?) "
                           "ON CONFLICT(site) DO UPDATE SET frozen_until = MAX(frozen_until, "
                           "excluded.frozen_until), shared = 1, reason = excluded.reason", (site, until, reason))
        row = connection.execute("SELECT frozen_until FROM freeze WHERE site = ?", (site,)).fetchone()
        return row[0] if row else until
    finally:
        connection.close()


def _visible_text(html: str) -> str | None:
    if len(html) > 40 * _NOTICE_MAX_TEXT:
        return None
    text = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1\s*>|<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;|\s+", " ", text).strip()
    return text if len(text) <= _NOTICE_MAX_TEXT else None


def rate_limit_notice(html: str) -> str | None:
    """The notice text when a document is a rate-limit / IP-freeze page, else None."""
    text = _visible_text(html or "")
    return text if text and _RATE_NOTICE.search(text) else None


def _notice_seconds(text: str) -> float:
    match = _NOTICE_DURATION.search(text)
    if not match:
        return DEFAULT_FREEZE_SECONDS
    value, unit = int(match.group(1)), match.group(2).lower()
    return float(value * 60 if unit.startswith(("分", "min")) else value)


def _retry_after(headers: dict[str, str] | None) -> float:
    try:
        return float((headers or {}).get("retry-after", ""))
    except ValueError:
        return DEFAULT_FREEZE_SECONDS


def check_document(url: str, html: str) -> None:
    """Raise SiteRateLimited (and freeze the site) when the page is a rate-limit notice."""
    notice = rate_limit_notice(html)
    if notice:
        until = freeze_site(url, _notice_seconds(notice), "rate_limit_notice")
        raise SiteRateLimited(url, until, f"rate-limit notice: {notice[:120]}")


def check_response(url: str, status: int | None, headers: dict[str, str] | None = None) -> None:
    """Raise SiteRateLimited for HTTP 429, or 503 carrying Retry-After."""
    headers = {key.lower(): value for key, value in (headers or {}).items()}
    if status == 429 or (status == 503 and "retry-after" in headers):
        until = freeze_site(url, _retry_after(headers), f"HTTP {status}")
        raise SiteRateLimited(url, until, f"HTTP {status}")
