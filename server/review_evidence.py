"""Immutable reviewed sources and one narrow native-counter equivalence proof.

Raw hashes never change meaning. A certificate connects two different captured
objects only when their native selected HTML differs solely in the node written
by the article's adjacent VSB _showDynClicks controller.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
from urllib.parse import urljoin, urlsplit
import uuid
import copy
from html.parser import HTMLParser

from bs4 import BeautifulSoup, Tag

from server.profile_content import scope_profile_html

STORE = "review-evidence"
COUNTER_BASIS = "native_wbnews_visit_counter_v1"
CONTROLLER_SOURCE_SHA256 = (
    "8b8f631f1a7ff7cfb3957e7a55caf89ded99ec6bb9655e6f2a8095cfeaf018c6"
)


def sha(value: str | bytes) -> str:
    return hashlib.sha256(
        value.encode("utf8") if isinstance(value, str) else value
    ).hexdigest()


def _json(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def _put(root: Path, data: bytes, suffix: str) -> str:
    digest = sha(data)
    directory = root / STORE / "objects"
    if not directory.resolve().is_relative_to(root):
        raise ValueError("review object directory outside task")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (digest + suffix)
    if not path.resolve().is_relative_to(root):
        raise ValueError("review object outside task")
    if path.exists():
        if sha(path.read_bytes()) != digest:
            raise ValueError("review evidence object has been altered")
        return digest
    pending = directory / (digest + "." + uuid.uuid4().hex + ".tmp")
    try:
        pending.write_bytes(data)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
    return digest


def _read(root: Path, digest: str, suffix: str) -> bytes:
    if not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("invalid review object hash")
    path = (root / STORE / "objects" / (digest + suffix)).resolve()
    if not path.is_relative_to(root):
        raise ValueError("review object outside task")
    data = path.read_bytes()
    if sha(data) != digest:
        raise ValueError("review evidence object has been altered")
    return data


@dataclass(frozen=True)
class ArchivedSource:
    root: Path
    manifest: dict
    audit: dict

    def source_html(self, url: str) -> str:
        # Snapshot writers on Windows use CRLF; match the original readers'
        # universal-newline text while retaining the exact file bytes/hash.
        return (
            _read(self.root, self.original_hash(url), ".html")
            .decode("utf8")
            .replace("\r\n", "\n")
            .replace("\r", "\n")
        )

    def original_hash(self, url: str) -> str:
        return self.manifest["captures"][url]["sha256"]

    def bounds(self, url: str) -> tuple[str | None, str | None]:
        fields = [
            s
            for s in self.audit.get("coverage", {}).get("field_sources", [])
            if s.get("profile_url") == url and s.get("profile_content_selector")
        ]
        if not fields:
            fields = [
                s
                for s in self.audit.get("missing_profile_media_evidence", [])
                if s.get("profile_url") == url
            ]
        pairs = {
            (s.get("profile_content_selector"), s.get("profile_content_start_selector"))
            for s in fields
        }
        if len(pairs) > 1:
            raise ValueError("review source has conflicting boundaries")
        return next(iter(pairs), (None, None))

    def decisions(self, filename: str) -> dict:
        digest = self.manifest["decisions"].get(filename)
        if not digest:
            return {}
        payload = json.loads(_read(self.root, digest, ".json"))
        if payload.get("schema") != 1:
            return {}
        return {row["id"]: row for row in payload["decisions"]}


def register_source(
    task_root: str | Path,
    audit_path: str | Path,
    task_id: str,
    *,
    source_root: str | Path | None = None,
    expected_audit_sha256: str | None = None,
) -> str:
    """Seal an actual reviewed source, including an explicitly relocated backup.

    A relocated input keeps its original audit and decision bytes. Its declared
    original audit must belong to this task, and snapshot paths are resolved only
    beneath the explicitly supplied source directory, never from stale pointers.
    """
    root, path = Path(task_root).resolve(), Path(audit_path).resolve()
    if root.name != task_id or root.parent.name != "tasks":
        raise ValueError("review archive belongs to another task")
    imported = source_root is not None or not path.is_relative_to(root)
    if imported and (source_root is None or expected_audit_sha256 is None):
        raise ValueError("external review source requires an explicit directory and pinned audit hash")
    if not imported and (path.parent.parent != root or not re.fullmatch(r"attempt-[1-9]\d*", path.parent.name)):
        raise ValueError("review audit is not in this task's actual attempt")
    raw = path.read_bytes()
    if expected_audit_sha256 is not None and sha(raw) != expected_audit_sha256:
        raise ValueError("review audit differs from the pinned original")
    audit = json.loads(raw)
    declared_path = str(audit.get("audit_path", path if source_root is None else ""))
    # Imported Windows paths still need lexical task/attempt checks on POSIX,
    # but real local paths must retain the host's spelling when resolved.
    declared_audit = PureWindowsPath(declared_path)
    if (declared_audit.parent.parent.name != task_id
            or declared_audit.parent.parent.parent.name != "tasks"
            or not re.fullmatch(r"attempt-[1-9]\d*", declared_audit.parent.name)):
        raise ValueError("review audit belongs to another task")
    if not imported and Path(declared_path).resolve() != path:
        raise ValueError("review audit's declared original differs from its actual source")
    local = Path(source_root).resolve() if source_root else path.parent
    if path.parent != local:
        raise ValueError("review source directory differs from audit")
    captures = {}
    for snapshot in audit.get("snapshots", []):
        if snapshot.get("kind") not in {"profile", "directory"}:
            continue
        original = PureWindowsPath(str(snapshot["path"]))
        if len(original.parts) < 2:
            raise ValueError("review snapshot has no local reference")
        if original.parent.parent != declared_audit.parent:
            raise ValueError("review snapshot belongs to another original attempt")
        candidate = (local / original.parts[-2] / original.parts[-1]).resolve()
        if not candidate.is_relative_to(local):
            raise ValueError("review snapshot outside source")
        data = candidate.read_bytes()
        capture = {"sha256": _put(root, data, ".html"), "kind": snapshot["kind"]}
        previous = captures.get(snapshot["url"])
        if previous and previous != capture:
            raise ValueError("ambiguous reviewed page capture")
        captures[snapshot["url"]] = capture
    from server.review_registry import DECISION_FILES

    decisions = {}
    for name in DECISION_FILES:
        decision = local / name
        if not decision.is_file():
            continue
        if not decision.resolve().is_relative_to(local):
            raise ValueError("review decision outside source directory")
        data = decision.read_bytes()
        if imported:
            payload = json.loads(data)
            rows = payload.get("decisions")
            if (payload.get("schema") != 1 or not isinstance(rows, list)
                    or any(not isinstance(row, dict) or not isinstance(row.get("id"), str) for row in rows)
                    or len({row["id"] for row in rows}) != len(rows)):
                raise ValueError("imported review decision schema is invalid")
        decisions[name] = _put(root, data, ".json")
    manifest = {
        "schema": 1,
        "task_id": task_id,
        "audit_sha256": _put(root, raw, ".json"),
        "captures": captures,
        "decisions": decisions,
    }
    if imported:
        try:
            _validate_import(ArchivedSource(root, manifest, audit), task_id)
        except (KeyError, TypeError) as error:
            raise ValueError("imported review source is incomplete") from error
    identifier = sha(_json(manifest))
    directory = root / STORE / "sources"
    if not directory.resolve().is_relative_to(root):
        raise ValueError("review source directory outside task")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (identifier + ".json")
    if not destination.resolve().is_relative_to(root):
        raise ValueError("review source manifest outside task")
    pending = directory / (identifier + "." + uuid.uuid4().hex + ".tmp")
    try:
        pending.write_text(_json(manifest), encoding="utf8")
        os.replace(pending, destination)
    finally:
        pending.unlink(missing_ok=True)
    return identifier


def _validate_import(bundle: ArchivedSource, task_id: str) -> None:
    """A backup is eligible only with actual originals for its native review IDs."""
    from server import html_email_review, identity_review, missing_media_review

    def original(url: str, digest: str, bounds: tuple) -> None:
        html = bundle.source_html(url)  # Also verifies the exact archived file bytes.
        if digest != bundle.original_hash(url):
            selected_source(html, url, bounds, digest)

    for item in bundle.audit.get("missing_profile_media_evidence", []):
        if item.get("id") != missing_media_review.identity(item):
            raise ValueError("imported media review ID differs from its native item")
        original(item["profile_url"], item["selected_html_sha256"], source_bounds(item))
    for item in bundle.audit.get("identity_review_items", []):
        if item.get("id") != identity_review.item_id(item):
            raise ValueError("imported identity review ID differs from its native item")
        for observation in item["observations"]:
            original(observation["url"], observation["source_sha256"], bundle.bounds(observation["url"]))
    for item in bundle.audit.get("html_email_review_items", []):
        if item.get("id") != html_email_review.item_id(item):
            raise ValueError("imported email review ID differs from its native item")
        original(item["url"], item["source_sha256"], bundle.bounds(item["url"]))
    for filename in bundle.manifest["decisions"]:
        if any(row.get("task_id", task_id) != task_id for row in bundle.decisions(filename).values()):
            raise ValueError("imported human decision belongs to another task")


def archived_sources(task_root: str | Path) -> list[ArchivedSource]:
    root = Path(task_root).resolve()
    sources = []
    for path in sorted((root / STORE / "sources").glob("*.json")):
        try:
            if not path.resolve().is_relative_to(root):
                continue
            manifest = json.loads(path.read_text(encoding="utf8"))
            if (
                manifest.get("schema") != 1
                or manifest.get("task_id") != root.name
                or sha(_json(manifest)) != path.stem
            ):
                continue
            audit = json.loads(_read(root, manifest["audit_sha256"], ".json"))
            sources.append(ArchivedSource(root, manifest, audit))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sources


def selected_source(
    html: str, url: str, bounds: tuple[str | None, str | None], expected: str
) -> tuple[str, str]:
    """Locate exactly the hash already declared by this consumer, not a guess."""
    if bounds[0]:
        scoped, evidence, issue = scope_profile_html(
            html, bounds[0], url, start_selector=bounds[1]
        )
        if issue or not evidence:
            raise ValueError("review source boundary failed")
        selected = BeautifulSoup(scoped, "html.parser").body.decode_contents()
        choices = [("selected", selected), ("wrapped", scoped)]
    else:
        choices = []
    choices.append(("original", html))
    for representation, value in choices:
        if sha(value) == expected:
            return representation, value
    raise ValueError("review source hash is not backed by its original")


class _FragmentParser(HTMLParser):
    """Find one balanced literal subtree; never repair or serialize its HTML."""

    VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, source: str, start: int):
        super().__init__(convert_charrefs=False)
        self.source, self.start, self.end = source, start, None
        self.lines = [0, *[match.end() for match in re.finditer("\n", source)]]
        self.stack = []

    def position(self):
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if self.end is not None or not (self.stack or self.position() == self.start):
            return
        if tag in self.VOID:
            if not self.stack:
                self.end = self.position() + len(self.get_starttag_text())
        else:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        if not self.stack and self.position() == self.start:
            self.end = self.position() + len(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.end is not None or not self.stack:
            return
        if self.stack.pop() != tag:
            raise ValueError("native selected fragment is not explicitly balanced")
        if not self.stack:
            closing = re.match(r"</" + re.escape(tag) + r"\s*>", self.source[self.position():], re.IGNORECASE)
            if closing is None:
                raise ValueError("native selected closing tag is unsupported")
            self.end = self.position() + closing.end()


def native_selected_source(html: str, bounds: tuple) -> tuple[str, list[int]]:
    """Map the already validated selector to offsets in its unchanged capture text."""
    if bounds[1] is not None:
        raise ValueError("native start-boundary equivalence is not implemented")
    if not bounds[0]:
        return html, [0, len(html)]
    soup = BeautifulSoup(html, "html.parser")
    matches = soup.select(bounds[0])
    if len(matches) != 1 or matches[0].sourceline is None or matches[0].sourcepos is None:
        raise ValueError("native selected fragment has no unique source location")
    node = matches[0]
    lines = [0, *[match.end() for match in re.finditer("\n", html)]]
    start = lines[node.sourceline - 1] + node.sourcepos
    parser = _FragmentParser(html, start)
    parser.feed(html)
    parser.close()
    if parser.end is None:
        raise ValueError("native selected fragment is incomplete")
    fragment = html[start:parser.end]
    if str(BeautifulSoup(fragment, "html.parser").find(True)) != str(node):
        raise ValueError("native fragment does not map to the selected node")
    return fragment, [start, parser.end]


def _counter_free(selected: str, original: str, url: str) -> tuple[str, list[dict]]:
    raw = BeautifulSoup(original, "html.parser")
    references = [
        urljoin(url, n["src"])
        for n in raw.select("script[src]")
        if urlsplit(urljoin(url, n["src"])).path == "/system/resource/js/dynclicks.js"
    ]
    if not references:
        raise ValueError("native counter controller resource absent")
    soup = BeautifulSoup(selected, "html.parser")
    widgets = []
    native = selected
    article = Path(urlsplit(url).path).stem
    if not article.isdecimal():
        raise ValueError("counter article is not bound to the source URL")
    for node in soup.find_all("span", id=re.compile(r"^dynclicks_wbnews_\d+_\d+$")):
        match = re.fullmatch(r"dynclicks_wbnews_(\d+)_(\d{1,3})", node["id"])
        previous = node.previous_sibling
        while (
            previous is not None
            and not isinstance(previous, Tag)
            and not str(previous).strip()
        ):
            previous = previous.previous_sibling
        controller = (
            re.fullmatch(
                r'\s*_showDynClicks\("wbnews",\s*(\d+),\s*(\d+)\)\s*;?\s*',
                previous.get_text(),
            )
            if isinstance(previous, Tag) and previous.name == "script"
            else None
        )
        if (
            not match
            or match[1] != article
            or not controller
            or controller[2] != article
            or set(node.attrs) != {"id", "name"}
            or node.get("name") != node["id"]
            or node.find(True)
            or not re.fullmatch(r"\d+", node.get_text())
        ):
            raise ValueError("native counter node is not a proved generated number")
        chain = []
        current = node
        while isinstance(current, Tag) and current.name != "[document]":
            siblings = (
                current.parent.find_all(current.name, recursive=False)
                if current.parent
                else [current]
            )
            chain.append(
                (
                    current.name,
                    next(
                        i + 1
                        for i, sibling in enumerate(siblings)
                        if sibling is current
                    ),
                )
            )
            current = current.parent
        # Remove only the literal proved span. Serializing the whole DOM here
        # would also normalize unrelated attribute order, quotes and entities.
        matches = [
            m for m in re.finditer(r"<span\b[^>]*>\d+</span\s*>", native, re.IGNORECASE)
            if str(BeautifulSoup(m.group(), "html.parser").span) == str(node)
        ]
        if len(matches) != 1:
            raise ValueError("native counter literal is ambiguous or unavailable")
        literal = matches[0]
        widgets.append(
            {
                "owner": controller[1],
                "article": article,
                "controller_sha256": sha(previous.get_text()),
                "resource": references,
                "node_path": [list(part) for part in reversed(chain)],
                "old_node_sha256": sha(literal.group()),
            }
        )
        native = native[:literal.start()] + native[literal.end():]
    if not widgets:
        raise ValueError("no supported native counter difference")
    return native, widgets


def equivalence(
    before: str,
    after: str,
    url: str,
    old_bounds: tuple,
    new_bounds: tuple,
    old_hash: str,
    new_hash: str,
) -> dict | None:
    """Recompute a narrow raw→raw proof; any other native DOM difference fails."""
    try:
        if old_bounds != new_bounds:
            return None
        old_repr, _ = selected_source(before, url, old_bounds, old_hash)
        new_repr, _ = selected_source(after, url, new_bounds, new_hash)
        if old_repr != new_repr or old_hash == new_hash:
            return None
        old_selected, old_span = native_selected_source(before, old_bounds)
        new_selected, new_span = native_selected_source(after, new_bounds)
        old_canonical, old_widgets = _counter_free(old_selected, before, url)
        new_canonical, new_widgets = _counter_free(new_selected, after, url)
        shape = lambda ws: [
            {k: v for k, v in w.items() if k != "old_node_sha256"} for w in ws
        ]
        if old_canonical != new_canonical or shape(old_widgets) != shape(new_widgets):
            return None
        return {
            "schema": 2,
            "basis": COUNTER_BASIS,
            "source_url": url,
            "bounds": list(old_bounds),
            "representation": old_repr,
            "old_bound_source_sha256": old_hash,
            "new_bound_source_sha256": new_hash,
            "old_raw_sha256": sha(old_selected),
            "new_raw_sha256": sha(new_selected),
            "old_native_text_span": old_span,
            "new_native_text_span": new_span,
            "old_original_sha256": sha(before),
            "new_original_sha256": sha(after),
            "canonical_sha256": sha(old_canonical),
            "old_widgets": old_widgets,
            "new_widgets": new_widgets,
            "controller_semantics_source_sha256": CONTROLLER_SOURCE_SHA256,
        }
    except (ValueError, TypeError, KeyError, AttributeError):
        return None


def old_review_items(root: Path, key: str):
    for bundle in archived_sources(root):
        for item in bundle.audit.get(key, []):
            yield bundle, item


def source_bounds(evidence: dict | None) -> tuple:
    evidence = evidence or {}
    return evidence.get("profile_content_selector"), evidence.get(
        "profile_content_start_selector"
    )


def current_source(audit_path: Path, url: str) -> tuple[str, dict]:
    root = audit_path.resolve().parent
    audit = json.loads(audit_path.read_text(encoding="utf8"))
    for snapshot in audit.get("snapshots", []):
        if snapshot.get("url") != url:
            continue
        path = Path(snapshot["path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("current review source outside audit")
        evidence = next(
            (
                s
                for s in audit.get("coverage", {}).get("field_sources", [])
                if s.get("profile_url") == url
            ),
            {},
        )
        return path.read_text(encoding="utf8"), evidence
    raise ValueError("current review source unavailable")


def retain_current(root: Path, html: str) -> str:
    return _put(root, html.encode("utf8"), ".html")


def _identity_title_reparse(before: str, after: str, old_value: str, new_value: str) -> dict | None:
    """Prove one legacy title mention was truncated from the same declared field."""
    from server.detail_mecc import declared_dom_title_fields
    from server.titles import select_title

    old_fields = declared_dom_title_fields(BeautifulSoup(before, "lxml"), preserve_content=True)
    new_fields = declared_dom_title_fields(BeautifulSoup(after, "lxml"), preserve_content=True)
    if old_fields != new_fields or len(new_fields) != 1:
        return None
    field = new_fields[0]
    selected = select_title(explicit_fields=new_fields)
    if (field.ambiguous or len(field.mentions) != 1 or selected.conflicts
            or old_value != field.mentions[0] or new_value != selected.title):
        return None
    return {"field": "职称", "basis": "same_declared_field_legacy_mention_to_complete_value",
            "old_value": old_value, "new_value": new_value,
            "raw_value": field.raw_value, "mentions": list(field.mentions),
            "source_lines": list(field.source_lines), "parser": "declared_dom_title_fields"}


def identity_equivalence(
    bundle: ArchivedSource,
    previous: dict,
    current: dict,
    source_html: dict[str, str],
    source_evidence: dict[str, dict],
) -> list[dict] | None:
    """Same facts, with only certified counter text or a source-proved title repair."""
    from server.identity_review import CONTENT_FIELDS
    from server.detail_mecc import LayoutAwareTokenizer
    from server.faculty_identity import field_value

    if previous.get("name") != current.get("name") or {
        o["url"] for o in previous["observations"]
    } != {o["url"] for o in current["observations"]}:
        return None
    old = {o["url"]: o for o in previous["observations"]}
    proofs = []
    if len(old) != len(previous["observations"]):
        return None
    try:
        for observation in current["observations"]:
            url = observation["url"]
            before = old[url]
            if any(
                field_value(before["record"], f)
                != field_value(observation["record"], f)
                for f in CONTENT_FIELDS
                if f not in {"简介", "职称"}
            ):
                return None
            old_title = field_value(before["record"], "职称")
            new_title = field_value(observation["record"], "职称")
            title_changed = old_title != new_title
            if before["source_sha256"] == observation["source_sha256"] and field_value(
                before["record"], "简介"
            ) == field_value(observation["record"], "简介") and not title_changed:
                continue
            original, fresh = bundle.source_html(url), source_html[url]
            old_bounds, new_bounds = (
                bundle.bounds(url),
                source_bounds(source_evidence.get(url)),
            )
            proof = equivalence(
                original,
                fresh,
                url,
                old_bounds,
                new_bounds,
                before["source_sha256"],
                observation["source_sha256"],
            )
            if proof is None:
                return None
            _, old_selected = selected_source(
                original, url, old_bounds, before["source_sha256"]
            )
            _, new_selected = selected_source(
                fresh, url, new_bounds, observation["source_sha256"]
            )
            if title_changed:
                reparse = _identity_title_reparse(old_selected, new_selected, old_title, new_title)
                if reparse is None:
                    return None
                proof["field_reparses"] = [reparse]
            read = lambda html: LayoutAwareTokenizer.get_clean_text(
                BeautifulSoup(html, "lxml").body, preserve_content=True
            )
            if read(old_selected) != field_value(before["record"], "简介") or read(
                new_selected
            ) != field_value(observation["record"], "简介"):
                return None
            old_canonical, _ = _counter_free(old_selected, original, url)
            new_canonical, _ = _counter_free(new_selected, fresh, url)
            if read(old_canonical) != read(new_canonical):
                return None
            proof.update(
                old_original_file_sha256=bundle.original_hash(url),
                new_original_file_sha256=retain_current(bundle.root, fresh),
            )
            proofs.append(proof)
        return proofs if proofs else None
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def email_equivalence(
    bundle: ArchivedSource,
    previous: dict,
    current: dict,
    source_html: str,
    evidence: dict,
) -> dict | None:
    """Entire review item equality after re-reading only certified counter context."""
    from server import html_email_review

    if previous.get("name") != current.get("name") or previous.get(
        "url"
    ) != current.get("url"):
        return None
    try:
        url = current["url"]
        original = bundle.source_html(url)
        old_bounds, new_bounds = bundle.bounds(url), source_bounds(evidence)
        proof = equivalence(
            original,
            source_html,
            url,
            old_bounds,
            new_bounds,
            previous["source_sha256"],
            current["source_sha256"],
        )
        if proof is None:
            return None
        normalized = []
        for item, html, bounds in [
            (previous, original, old_bounds),
            (current, source_html, new_bounds),
        ]:
            _, selected = selected_source(html, url, bounds, item["source_sha256"])
            canonical, _ = _counter_free(selected, html, url)
            value = copy.deepcopy(item)
            value.pop("id", None)
            value["source_sha256"] = sha(canonical)
            for option in value["candidates"]:
                # Original item contexts must first match the actual raw source.
                if option["page_context"] != html_email_review._contexts(
                    html_email_review._page_text(selected), option["email"]
                ):
                    return None
                option["page_context"] = html_email_review._contexts(
                    html_email_review._page_text(canonical), option["email"]
                )
            normalized.append(value)
        if normalized[0] != normalized[1]:
            return None
        proof.update(
            old_original_file_sha256=bundle.original_hash(url),
            new_original_file_sha256=retain_current(bundle.root, source_html),
        )
        return proof
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
