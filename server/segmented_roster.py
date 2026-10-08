"""Segmented article rosters: several people written into one flat article body.

Type (CONTEXT.md, 分段平铺名录): the verified article body has no wrapper per
person.  Each record begins at an element matched by ``record_start_selector``
(a portrait or resume image, or a name heading) and runs, in document order,
until the next start.  Content before the first start is the preamble and
belongs to nobody.  Because the cut is positional, text that shares a ``<p>``
with the next record's image (``电子邮箱：a@x.cn<img …>``) still stays with the
record it follows.

The reconnaissance model contributes only the two CSS selectors.  Everything
here is deterministic: segmentation, record text, emails and names.  A name
that is readable only inside an image comes from local OCR and is admitted
without a person only when the OCR line is confident, the name has a listed
surname, and the segment's own text email spells the full name in pinyin.
Every other OCR name is routed to the local image review page, bound to the
exact OCR line of the exact original image.
"""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable
import uuid
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from server.faculty_extractor import LayoutAwareTokenizer, TITLE_KEYWORDS, find_emails
# The one person-name rule; pdf_roster and image_table_roster import it from here.
from server.name_list import NAME_PRIOR_FLOOR, _HAN_NAME, _person_name as _name_like

# faculty_extractor puts the Opus algorithm layer on sys.path under its flat
# module names; import through the same names so there is one copy of each.
from email_matching import pinyin_readings  # noqa: E402
from iso_mining import _COMPOUND, name_prob  # noqa: E402

KIND = "segmented"
SELECTORS = ("container_selector", "record_start_selector")

# Admission thresholds for names read from images.
AUTO_NAME_CONFIDENCE = 0.9
# The image biography audit matches owners within the first five OCR lines;
# a name found later could not be attributed there either.
NAME_SCAN_LINES = 5
TEXT_NAME_LINES = 3

_SPLIT = re.compile(r"[\s\u3000,，、;；:：|/()（）\[\]【】<>《》\"'“”]+")
_LABELS = {"姓名", "教师", "老师", "导师", "简介", "个人简介", "个人简历", "简历",
           "导师简介", "教师简介"}
# A dotted name is taken whole: an 8-character cut of '迪丽热巴•迪力木拉提' is another name.
_NAME_LABEL = re.compile(r"姓\s*名\s*[:：]?\s*([一-鿿]{1,6}[·•][一-鿿]{1,8}"
                         r"|[一-鿿·•]{2,8})")
# '职务：学院党委书记' names a post; the record's title is on the line after it.
_POST_LINE = re.compile(r"^\s*职\s*务\s*[:：]")
_BARE_NAME_LABEL = re.compile(r"^\s*姓\s*名\s*[:：]?\s*$")
_REVIEWABLE_LINE = re.compile(r"^[\u4e00-\u9fff·•\s]{2,8}$")
_HEAD_WORDS = {"男", "女", "博士", "硕士", "博导", "硕导", "博士生导师", "硕士生导师",
               "高级工程师", "工程师", "高级实验师", "中共党员", "党员", "系主任", "副主任", "主任",
               "院长", "副院长"}


@dataclass
class Segment:
    index: int
    html: str
    text: str
    images: list[str] = field(default_factory=list)
    start_image: str | None = None
    links: list[str] = field(default_factory=list)
    emails: list[str] = field(default_factory=list)


@dataclass
class SegmentedPage:
    url: str
    container_count: int
    segments: list[Segment]
    preamble_text: str = ""
    preamble_emails: list[str] = field(default_factory=list)
    issues: list[dict] = field(default_factory=list)

    @property
    def shared_emails(self) -> set[str]:
        """Addresses that cannot belong to a single record."""
        seen: dict[str, int] = {}
        for segment in self.segments:
            for email in {item.lower() for item in segment.emails}:
                seen[email] = seen.get(email, 0) + 1
        shared = {email for email, count in seen.items() if count > 1}
        return shared | {email.lower() for email in self.preamble_emails}


def branch_valid(branch: dict) -> bool:
    return (branch.get("kind") == KIND
            and all(isinstance(branch.get(key), str) and branch[key].strip() for key in SELECTORS)
            and bool(branch.get("single_page")) != bool(branch.get("next_selector"))
            and "card_selector" not in branch
            and "empty_state_selector" not in branch and "roster_container_selector" not in branch
            and "profile_content_selector" not in branch)


# ── Segmentation ─────────────────────────────────────────────────────────────
def _image_url(tag: Tag, base_url: str) -> str | None:
    srcset = tag.get("srcset") or ""
    raw = (tag.get("data-src") or tag.get("src")
           or (srcset.split(",", 1)[0].strip().split(" ", 1)[0] if srcset else ""))
    if not raw:
        return None
    url = urljoin(base_url, raw)
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.netloc else None


def _unique(values) -> list[str]:
    seen, result = set(), []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _slice(container: Tag, lo: int, hi: int) -> Tag:
    """Deep copy of ``container`` keeping only descendants positioned in [lo, hi).

    Positions are indices into ``container.descendants``.  Ancestors of kept
    nodes stay as structural wrappers; everything whose subtree lies wholly
    outside the range is removed.
    """
    clone = copy.copy(container)
    nodes = list(clone.descendants)
    removals = []
    k = 0
    while k < len(nodes):
        node = nodes[k]
        size = sum(1 for _ in node.descendants) if isinstance(node, Tag) else 0
        end = k + size
        if end < lo or k >= hi:
            removals.append(node)
            k = end + 1
            continue
        k += 1
    for node in removals:
        node.extract()
    return clone


def split_segments(html: str, url: str, container_selector: str,
                   record_start_selector: str) -> SegmentedPage:
    soup = BeautifulSoup(html, "html.parser")
    containers = soup.select(container_selector)
    if len(containers) != 1:
        return SegmentedPage(url, len(containers), [], issues=[
            {"url": url, "status": "segment_container_not_unique",
             "container_count": len(containers)}])
    container = containers[0]
    starts = container.select(record_start_selector)
    if not starts:
        return SegmentedPage(url, 1, [], issues=[{"url": url, "status": "segment_start_empty"}])
    start_ids = {id(node) for node in starts}
    nested = [node for node in starts
              if any(id(parent) in start_ids for parent in node.parents)]
    if nested:
        return SegmentedPage(url, 1, [], issues=[{"url": url, "status": "segment_start_nested",
                                                  "nested_starts": len(nested)}])
    positions = {id(node): index for index, node in enumerate(container.descendants)}
    total = len(positions)
    bounds = sorted(positions[id(node)] for node in starts)
    segments = []
    for number, lo in enumerate(bounds, 1):
        hi = bounds[number] if number < len(bounds) else total
        piece = _slice(container, lo, hi)
        text = LayoutAwareTokenizer.get_clean_text(piece)
        images = _unique(filter(None, (_image_url(tag, url) for tag in piece.find_all("img"))))
        first = next((tag for tag in piece.descendants if isinstance(tag, Tag)
                      and tag.name == "img"), None)
        start = starts[[positions[id(node)] for node in starts].index(lo)]
        start_img = start if start.name == "img" else start.find("img")
        links = _unique(urljoin(url, anchor["href"]) for anchor in piece.find_all("a", href=True)
                        if not anchor["href"].strip().lower().startswith(("javascript:", "#", "mailto:")))
        segments.append(Segment(
            index=number, html=str(piece), text=text, images=images,
            start_image=(_image_url(start_img, url) if start_img is not None
                         else (_image_url(first, url) if first is not None else None)),
            links=links, emails=_unique(find_emails(text))))
    preamble = LayoutAwareTokenizer.get_clean_text(_slice(container, 0, bounds[0])) if bounds[0] else ""
    return SegmentedPage(url, 1, segments, preamble_text=preamble,
                         preamble_emails=_unique(find_emails(preamble)))


# ── Names ────────────────────────────────────────────────────────────────────
def _title_in(line: str) -> str | None:
    found = [(line.find(keyword), -len(keyword), keyword)
             for keyword in TITLE_KEYWORDS if keyword in line]
    return min(found)[2] if found else None


def line_name(line: str) -> str | None:
    """The person name a record line begins with, or None."""
    labelled = _NAME_LABEL.search(line)
    if labelled:
        return _name_like(labelled.group(1))
    tokens = [token for token in _SPLIT.split(line.strip()) if token]
    index = 0
    while index < len(tokens) and (tokens[index] in _LABELS or tokens[index] in TITLE_KEYWORDS):
        index += 1
    return _name_like(tokens[index]) if index < len(tokens) else None


def heading_shape(line: str) -> tuple[str, bool, str] | None:
    """(name, labelled, what follows the name) for a line that begins with a name.

    Records on one page share a heading template, so another person's heading
    is recognised by having the same shape as this record's own heading line.
    """
    name = line_name(line)
    if not name:
        return None
    labelled = bool(_NAME_LABEL.search(line))
    tokens = [token.strip(".。") for token in _SPLIT.split(line.strip()) if token.strip(".。")]
    after = tokens[tokens.index(name) + 1:] if name in tokens else []
    if not after:
        follows = "none"
    elif after[0] in {"男", "女"}:
        follows = "gender"
    elif after[0] in TITLE_KEYWORDS or after[0] in _HEAD_WORDS:
        follows = "title"
    else:
        follows = "other"
    return name, labelled, follows


def text_name(segment: Segment) -> tuple[str | None, str, int]:
    """Return (name, title, extra heading lines) read from the record's own text."""
    lines = [line.strip() for line in segment.text.splitlines() if line.strip()]
    name, title, shape = None, "教师", None
    extra = 0
    for index, line in enumerate(lines[:TEXT_NAME_LINES]):
        shape = heading_shape(line)
        if shape:
            name = shape[0]
            heading = [text for text in lines if not _POST_LINE.match(text)][:2]
            title = (_title_in(line) or next((found for found in map(_title_in, heading) if found), None)
                     or "教师")
            rest = lines[index + 1:]
            # '陈厚、尤再进、董平……' is a list of people, not one record's heading.
            from server.name_list import people_chains
            extra += sum(len(chain.names) + len(chain.rare) - 1 for chain in people_chains(line))
            break
    if name and (shape[1] or shape[2] != "other"):
        for line in rest:
            other = heading_shape(line)
            if (other and other[0] != name and other[1:] == shape[1:]
                    and (other[2] != "none" or len(line) <= 4)):
                extra += 1
    return name, title, extra


def full_name_email(email: str, name: str) -> bool:
    """True when the email local part spells surname and full given name."""
    local = re.sub(r"[\d._\-]+", "", email.split("@", 1)[0].lower())
    if not local or not _HAN_NAME.match(name):
        return False
    surname_chars = 2 if name[:2] in _COMPOUND and len(name) >= 3 else 1
    for syllables in pinyin_readings(name):
        surname = "".join(syllables[:surname_chars])
        given = "".join(syllables[surname_chars:])
        if not given:
            continue
        variants = {surname + given, given + surname, surname[:1] + given}
        if local in variants or any(len(variant) >= 4 and local.startswith(variant)
                                    for variant in (surname + given, given + surname)):
            return True
    return False


# ── Emails ───────────────────────────────────────────────────────────────────
def choose_email(segment: Segment, name: str | None, shared: set[str]) -> tuple[str, str | None, dict | None]:
    """Positional email for one record: (email, basis, issue)."""
    pool = [email for email in segment.emails if email.lower() not in shared]
    if not pool:
        return "", None, None
    if len(pool) == 1:
        affinity = bool(name) and full_name_email(pool[0], name)
        return pool[0], "segment_position" + ("+full_name_pinyin" if affinity else ""), None
    affine = [email for email in pool if name and full_name_email(email, name)]
    if len(affine) == 1:
        return affine[0], "segment_position+full_name_pinyin", None
    return "", None, {"status": "segment_email_ambiguous", "segment": segment.index,
                      "email_count": len(pool)}


# ── OCR names ────────────────────────────────────────────────────────────────
@dataclass
class OcrName:
    name: str | None
    page: int
    line_index: int
    confidence: float
    reviewed: bool


def ocr_name(scanned: dict, reviewed: set[tuple[int, int]]) -> OcrName | None:
    """Locate the record's name line among the first OCR lines of an image."""
    pages = scanned.get("pages") or []
    if not pages:
        return None
    page = pages[0]
    lines = page.get("lines") or []
    window = lines[:NAME_SCAN_LINES]
    fallback = None
    for index, line in enumerate(window):
        text = line.get("text", "")
        target = index
        if _BARE_NAME_LABEL.match(text) and index + 1 < len(lines):
            target = index + 1
            text = lines[target].get("text", "")
        name = line_name(text)
        confidence = float(lines[target].get("confidence", 0))
        if name:
            return OcrName(name, page["page"], target, confidence,
                           (page["page"], target) in reviewed)
        if fallback is None and _REVIEWABLE_LINE.match(text.strip()):
            fallback = OcrName(None, page["page"], target, confidence,
                               (page["page"], target) in reviewed)
    return fallback


def admit_ocr_name(found: OcrName | None, emails: list[str]) -> tuple[str | None, str]:
    """Return (name, decision): decision is a basis when admitted, else a status."""
    if found is None:
        return None, "segment_name_unreadable"
    if found.name is None:
        return None, "segment_name_needs_review"
    if found.reviewed:
        return found.name, "human_reviewed_ocr_line"
    if (found.confidence >= AUTO_NAME_CONFIDENCE and name_prob(found.name) >= NAME_PRIOR_FLOOR
            and any(full_name_email(email, found.name) for email in emails)):
        return found.name, "confident_ocr+full_name_pinyin_email"
    return None, "segment_name_needs_review"


# ── Reconciliation used by the harvest workflow ──────────────────────────────
@dataclass
class SegmentRecord:
    page_url: str
    segment: Segment
    name: str | None
    title: str
    name_source: str          # "text" | "ocr"
    name_basis: str | None = None
    email: str = ""
    email_basis: str | None = None
    source_kind: str = "university_faculty_roster"

    def provenance(self) -> dict:
        return {"segment": self.segment.index, "name_source": self.name_source,
                "name_basis": self.name_basis, "email_basis": self.email_basis}


@dataclass
class SegmentedReconciliation:
    records: list[SegmentRecord]
    pending_ocr: list[SegmentRecord]
    observation: dict
    unresolved: list[dict]
    shared_emails: set[str]


def _blank(segment: Segment) -> bool:
    """An empty spacer between records (a '<tr><td>&nbsp;</td></tr>' row) holds no person."""
    return not (re.sub(r"\s", "", segment.text) or segment.images or segment.start_image
                or segment.links or segment.emails)


def reconcile_page(html: str, url: str, branch: dict) -> SegmentedReconciliation:
    page = split_segments(html, url, branch["container_selector"], branch["record_start_selector"])
    unresolved = list(page.issues)
    records, pending = [], []
    shared = page.shared_emails
    blank = 0
    for segment in page.segments:
        if _blank(segment):
            blank += 1
            continue
        name, title, extra = text_name(segment)
        if extra:
            unresolved.append({"url": url, "status": "segment_multiple_names",
                               "segment": segment.index, "extra_name_lines": extra})
            continue
        if name:
            record = SegmentRecord(url, segment, name, title, "text", "segment_text")
        elif segment.start_image or segment.images:
            record = SegmentRecord(url, segment, None, "教师", "ocr")
        else:
            unresolved.append({"url": url, "status": "segment_name_missing", "segment": segment.index})
            continue
        email, basis, issue = choose_email(segment, name, shared)
        if issue:
            unresolved.append({"url": url, **issue})
        record.email, record.email_basis = email, basis
        (records if name else pending).append(record)
    names = [record.name for record in records]
    for duplicate in sorted({name for name in names if names.count(name) > 1}):
        unresolved.append({"url": url, "status": "segment_duplicate_name", "occurrences": names.count(duplicate)})
    observation = {"url": url, "kind": KIND, "container_selector": branch["container_selector"],
                   "record_start_selector": branch["record_start_selector"],
                   "visible_segments": len(page.segments) - blank, "blank_segments": blank,
                   "text_named": len(records),
                   "image_named_pending": len(pending), "preamble_emails": len(page.preamble_emails),
                   "unaccounted_segments": [item["segment"] for item in unresolved if "segment" in item]}
    return SegmentedReconciliation(records, pending, observation, unresolved, shared)


def verification_summary(html: str, url: str, container_selector: str,
                         record_start_selector: str, limit: int = 24) -> dict:
    """Structure-only evidence for the reconnaissance model (no names or emails)."""
    page = split_segments(html, url, container_selector, record_start_selector)
    items = []
    counts = {"text_named": 0, "image_only": 0, "unnamed_without_image": 0,
              "multiple_name_lines": 0, "multiple_emails": 0, "with_links": 0, "blank": 0}
    for segment in page.segments:
        if _blank(segment):
            counts["blank"] += 1
            continue
        name, _title, extra = text_name(segment)
        counts["text_named"] += bool(name)
        counts["image_only"] += (not name) and bool(segment.images)
        counts["unnamed_without_image"] += (not name) and not segment.images
        counts["multiple_name_lines"] += bool(extra)
        counts["multiple_emails"] += len(segment.emails) > 1
        counts["with_links"] += bool(segment.links)
        items.append({"index": segment.index, "text_chars": len(re.sub(r"\s", "", segment.text)),
                      "images": len(segment.images), "emails": len(segment.emails),
                      "links": len(segment.links), "text_name": bool(name), "extra_name_lines": extra})
    structure_ok = (page.container_count == 1 and bool(page.segments) and not page.issues
                    and counts["unnamed_without_image"] == 0 and counts["multiple_name_lines"] == 0)
    shown = items[:limit]
    return {"url": url, "container_selector": container_selector,
            "record_start_selector": record_start_selector,
            "container_count": page.container_count, "segment_count": len(page.segments),
            **counts, "preamble_chars": len(re.sub(r"\s", "", page.preamble_text)),
            "preamble_emails": len(page.preamble_emails),
            "issues": [item["status"] for item in page.issues], "structure_ok": structure_ok,
            "segments": {"offset": 0, "end": len(shown), "total": len(items),
                         "nextOffset": len(shown) if len(shown) < len(items) else None,
                         "items": shown}}


# ── OCR naming with the shared OCR cache and the review decisions ────────────
async def name_image_segments(
    pending: list[SegmentRecord],
    *,
    fetch_asset: Callable[[str], Awaitable[bytes]],
    evidence_dir: str | Path,
    cache_dir: str | Path | None = None,
    review_decisions_path: str | Path | None = None,
    shared_emails: dict[str, set[str]] | None = None,
    ocr_engine: Any = None,
) -> dict:
    """Admit OCR names for image-only records; unresolved items drive review."""
    from server.image_bio_review import apply_review_decisions
    from server.image_biography import _OCR_CACHE_VERSION, scan_media

    out = Path(evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    admitted, unresolved, evidence = [], [], []
    engine = ocr_engine
    for record in pending:
        segment = record.segment
        media_url = segment.start_image or segment.images[0]
        source = {"url": record.page_url, "segment": segment.index, "media_url": media_url}
        try:
            original = await fetch_asset(media_url)
            if len(original) > 50_000_000:
                raise ValueError("media exceeds 50 MB review limit")
            digest = hashlib.sha256(original).hexdigest()
            scanned = None
            shared_path = (Path(cache_dir) / digest[:2] / f"{digest}_image_v{_OCR_CACHE_VERSION}.json"
                           if cache_dir is not None else None)
            if shared_path is not None and shared_path.is_file():
                try:
                    cached = json.loads(shared_path.read_text(encoding="utf-8"))
                    if (cached.get("ocr_cache_version") == _OCR_CACHE_VERSION
                            and cached.get("source_sha256") == digest and cached.get("kind") == "image"):
                        scanned = cached
                except (OSError, ValueError, TypeError):
                    scanned = None
            if scanned is None:
                if engine is None:
                    from rapidocr_onnxruntime import RapidOCR
                    engine = RapidOCR()
                scanned = await asyncio.to_thread(scan_media, original, "image", ocr_engine=engine)
                scanned["ocr_cache_version"] = _OCR_CACHE_VERSION
                scanned["source_sha256"] = digest
                if shared_path is not None:
                    shared_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = shared_path.with_name(f".{shared_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
                    try:
                        temporary.write_text(json.dumps(scanned, ensure_ascii=False), encoding="utf-8")
                        temporary.replace(shared_path)
                    finally:
                        temporary.unlink(missing_ok=True)
        except Exception as exc:
            unresolved.append({**source, "status": "segment_name_asset_failed",
                               "error": f"{type(exc).__name__}: {exc}"})
            continue
        stem = hashlib.sha256((record.page_url + "|" + media_url).encode("utf-8")).hexdigest()[:20]
        extension = {"jpeg": "jpg", "tiff": "tif"}.get(scanned.get("image_format", ""),
                                                       scanned.get("image_format") or "img")
        original_path = out / f"{stem}.{extension}"
        ocr_path = out / f"{stem}_ocr.json"
        original_path.write_bytes(original)
        ocr_path.write_text(json.dumps(scanned, ensure_ascii=False, indent=2), encoding="utf-8")
        item = {"profile_url": record.page_url, "segment": segment.index, "media_url": media_url,
                "kind": "image", "sha256": digest, "original_path": str(original_path),
                "ocr_path": str(ocr_path), "purpose": "segment_name"}
        evidence.append(item)
        adjusted, reviewed = apply_review_decisions(scanned, media_url, digest, review_decisions_path)
        found = ocr_name(adjusted, reviewed)
        pool = [email for email in segment.emails
                if email.lower() not in (shared_emails or {}).get(record.page_url, set())]
        name, decision = admit_ocr_name(found, pool)
        if name is None:
            issue = {**source, "status": decision, "ocr_path": str(ocr_path)}
            if found is not None:
                issue.update(page=found.page, line_index=found.line_index)
                item.update(name_page=found.page, name_line_index=found.line_index)
            unresolved.append(issue)
            continue
        item.update(name_page=found.page, name_line_index=found.line_index, name_basis=decision)
        record.name, record.name_basis = name, decision
        email, basis, issue = choose_email(segment, name, (shared_emails or {}).get(record.page_url, set()))
        record.email, record.email_basis = email, basis
        admitted.append(record)
    return {"admitted": admitted, "unresolved": unresolved, "evidence": evidence}
