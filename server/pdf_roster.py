"""Multi-person PDF rosters: several people written into one browser-observed PDF.

Type (CONTEXT.md, 多人 PDF 名录): a verified roster card holds only a PDF
(a PDF.js viewer, an ``<object>``/``<embed>`` or a download link) and no
person.  The PDF itself is the roster.  This module reads the original the
browser session fetched, page by page, and cuts it into one record per person
with the same rules as a segmented article (server/segmented_roster.py):

* text layers are read as they are; scanned pages use the existing local OCR
  (server/image_biography.scan_media), so every line keeps its page, its box
  and, for OCR, its confidence;
* page numbers, rotated watermarks and running headers/footers repeated on
  several pages are layout, not biography, and are kept out of record text;
* a record starts at a line that begins with a person name in the PDF's
  dominant heading template (a ``姓名：`` label, a larger or bold line, or a
  name followed by gender or title) and runs, across page breaks, until the
  next start.  Lines before the first start belong to nobody.

Nothing is guessed.  A record whose own text holds another heading, a heading
the reader could not name, an OCR name that is not confident and not
cross-checked, an OCR line under the review floor, two records with one name,
or only addresses shared with other records stays unresolved with its page
and line evidence; the PDF's other records are still reconciled.  No model is
called here and no field is rewritten.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import statistics
from typing import Any, Awaitable, Callable
import uuid

from server.segmented_roster import (_LABELS, _SPLIT, OcrName, Segment, SegmentRecord, _unique, admit_ocr_name,
                                     choose_email, heading_shape, text_name)
from server.faculty_extractor import find_emails

KIND = "pdf_roster"
# Same floor as the image biography audit and its review page.
LINE_REVIEW_CONFIDENCE = 0.8
EMPHASIS_RATIO = 1.15
# A header or footer sits in the outer band of the page.
MARGIN_BAND = 0.08
# A raster this large on a text-layer page may hold text nobody read.
UNREAD_RASTER_SHARE = 0.25
HEADING_CHARS = 12

_PAGE_NUMBER = re.compile(
    r"^\s*(?:第\s*\d{1,4}\s*页(?:\s*[,，/／]?\s*共\s*\d{1,4}\s*页)?|[-—–]?\s*\d{1,4}\s*[-—–]?"
    r"|\d{1,4}\s*[/／]\s*\d{1,4}|page\s*\d{1,4}(?:\s*of\s*\d{1,4})?)\s*$", re.I)


@dataclass
class LayoutLine:
    page: int
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    bold: bool
    source: str                 # "pdf_text_layer" | "local_ocr"
    page_height: float
    line_index: int | None = None   # index into the OCR page lines
    confidence: float | None = None
    reviewed: bool = False
    rotated: bool = False
    excluded: str | None = None
    rel: float = 1.0            # size relative to the page's body text

    @property
    def compact(self) -> str:
        return re.sub(r"\s+", "", self.text)


@dataclass
class PdfRecord(SegmentRecord):
    media_url: str = ""
    sha256: str = ""
    pages: list[int] = field(default_factory=list)

    def provenance(self) -> dict:
        return {**super().provenance(), "media_url": self.media_url, "pdf_sha256": self.sha256,
                "pdf_pages": self.pages}


def layout_lines(original: bytes, scanned: dict, reviewed: set[tuple[int, int]]) -> tuple[list[LayoutLine], list[dict]]:
    """Lines in reading order with their position; page issues for unread rasters."""
    import pymupdf

    document = pymupdf.open(stream=original, filetype="pdf")
    lines: list[LayoutLine] = []
    issues: list[dict] = []
    for page_scan in scanned["pages"]:
        number = page_scan["page"]
        page = document[number - 1]
        if page_scan.get("source") == "pdf_text_layer":
            area = page.rect.width * page.rect.height
            unread = [info for info in page.get_image_info()
                      if area and UNREAD_RASTER_SHARE * area
                      <= (info["bbox"][2] - info["bbox"][0]) * (info["bbox"][3] - info["bbox"][1])]
            if unread:
                issues.append({"page": number, "status": "pdf_roster_raster_unread", "images": len(unread)})
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    spans = [span for span in line["spans"] if span["text"].strip()]
                    text = "".join(span["text"] for span in line["spans"]).strip()
                    if not spans or not text:
                        continue
                    direction = line.get("dir", (1.0, 0.0))
                    lines.append(LayoutLine(
                        page=number, text=text, bbox=tuple(line["bbox"]),
                        size=max(span["size"] for span in spans),
                        bold=all(span["flags"] & 16 or "bold" in span["font"].lower() for span in spans),
                        source="pdf_text_layer", page_height=page.rect.height,
                        rotated=abs(direction[1]) > 0.01))
            continue
        # scan_media renders scanned pages at 2x before OCR.
        height = page.rect.height * 2
        for index, item in enumerate(page_scan.get("lines") or []):
            text = str(item.get("text", "")).strip()
            box = item.get("box") or []
            if not text:
                continue
            try:
                xs = [float(point[0]) for point in box]
                ys = [float(point[1]) for point in box]
                bbox = (min(xs), min(ys), max(xs), max(ys))
            except (TypeError, ValueError, IndexError):
                bbox = (0.0, 0.0, 0.0, 0.0)
            lines.append(LayoutLine(
                page=number, text=text, bbox=bbox, size=bbox[3] - bbox[1], bold=False,
                source="local_ocr", page_height=height, line_index=index,
                confidence=float(item.get("confidence", 0)), reviewed=(number, index) in reviewed))
    return lines, issues


def _exclude_layout_noise(lines: list[LayoutLine]) -> None:
    pages = {line.page for line in lines}
    in_margin: dict[str, list[LayoutLine]] = {}
    for line in lines:
        if line.rotated:
            line.excluded = "rotated_watermark"
        elif _PAGE_NUMBER.match(line.text):
            line.excluded = "page_number"
        elif line.page_height and (line.bbox[3] <= line.page_height * MARGIN_BAND
                                   or line.bbox[1] >= line.page_height * (1 - MARGIN_BAND)):
            in_margin.setdefault(line.compact, []).append(line)
    if len(pages) < 2:
        return
    for repeated in in_margin.values():
        if len({line.page for line in repeated}) >= 2:
            for line in repeated:
                line.excluded = "running_header_footer"


def _template(line: LayoutLine) -> tuple | None:
    """The heading template a line follows, or None when it cannot start a record."""
    shape = heading_shape(line.text)
    if not shape:
        return None
    _name, labelled, follows = shape
    emphasized = line.bold or line.rel >= EMPHASIS_RATIO
    if not (labelled or emphasized or follows in {"gender", "title"}):
        return None
    return labelled, follows, emphasized


def _looks_like_heading(line: LayoutLine, template: tuple, heading_size: float, heading_bold: bool) -> bool:
    labelled, follows, emphasized = template
    if labelled and re.match(r"\s*姓\s*名", line.text):
        return True
    tokens = [token for token in _SPLIT.split(line.text.strip()) if token]
    if follows == "gender" and len(tokens) >= 2 and tokens[1] in {"男", "女"} and len(tokens[0]) <= 8:
        return True
    return bool(emphasized and line.bold == heading_bold and heading_size > 0
                and abs(line.rel - heading_size) <= heading_size * 0.08
                and len(line.compact) <= HEADING_CHARS and line.compact not in _LABELS
                and not line.compact.endswith((":", "：")))


@dataclass
class PdfSegmentation:
    records: list[list[LayoutLine]]
    preamble: list[LayoutLine]
    template: tuple | None
    unrecognized_headings: dict[int, int]      # record number (0: preamble) -> lines


def segment_lines(lines: list[LayoutLine]) -> PdfSegmentation:
    _exclude_layout_noise(lines)
    kept = [line for line in lines if not line.excluded]
    # Type size is compared within its own page: text-layer points and OCR
    # box pixels are different scales.
    for page in {line.page for line in kept}:
        sizes = [line.size for line in kept if line.page == page and line.size > 0]
        body = statistics.median(sizes) if sizes else 0.0
        for line in kept:
            if line.page == page:
                line.rel = line.size / body if body else 1.0
    templates = [(_template(line), line) for line in kept]
    counts = Counter(template for template, _ in templates if template)
    if not counts:
        return PdfSegmentation([], kept, None, {})
    dominant = max(counts, key=lambda template: (counts[template], template[2], template[0]))
    starts = {id(line) for template, line in templates if template == dominant}
    records: list[list[LayoutLine]] = []
    preamble: list[LayoutLine] = []
    for line in kept:
        if id(line) in starts:
            records.append([line])
        elif records:
            records[-1].append(line)
        else:
            preamble.append(line)
    # A start the reader could not name (an OCR-garbled name, a transliterated
    # one) must not fold another person into the record before it.
    heading_size = statistics.median(record[0].rel for record in records)
    heading_bold = all(record[0].bold for record in records)
    unrecognized: dict[int, int] = {}
    for number, members in [(0, preamble)] + [(n, record[1:]) for n, record in enumerate(records, 1)]:
        count = sum(_looks_like_heading(line, dominant, heading_size, heading_bold) for line in members)
        if count:
            unrecognized[number] = count
    return PdfSegmentation(records, preamble, dominant, unrecognized)


def _record_text(lines: list[LayoutLine]) -> str:
    return "\n".join(line.text for line in lines)


def reconcile_pdf(original: bytes, scanned: dict, reviewed: set[tuple[int, int]], *, page_url: str,
                  media_url: str, sha256: str, title_default: str = "教师") -> dict:
    """Records, review items and structure-only observation for one PDF original."""
    lines, page_issues = layout_lines(original, scanned, reviewed)
    split = segment_lines(lines)
    source = {"url": page_url, "media_url": media_url}
    unresolved = [{**source, **issue} for issue in page_issues]
    empty_pages = [page["page"] for page in scanned["pages"] if not page.get("text", "").strip()]
    if empty_pages:
        unresolved.append({**source, "status": "pdf_roster_page_unreadable", "pages": empty_pages})
    if 0 in split.unrecognized_headings:
        unresolved.append({**source, "status": "pdf_roster_heading_unrecognized", "segment": 0,
                           "lines": split.unrecognized_headings[0]})
    if not split.records:
        unresolved.append({**source, "status": "pdf_roster_records_not_found",
                           "page_count": scanned["page_count"]})
    segments = []
    for number, record_lines in enumerate(split.records, 1):
        text = _record_text(record_lines)
        segments.append(Segment(index=number, html="", text=text, emails=_unique(find_emails(text))))
    preamble_emails = {email.lower() for email in find_emails(_record_text(split.preamble))}
    seen: Counter = Counter(email.lower() for segment in segments for email in {e.lower() for e in segment.emails})
    shared = {email for email, count in seen.items() if count > 1} | preamble_emails

    records: list[PdfRecord] = []
    name_reviews: list[dict] = []
    accounted: dict[int, str] = {}
    for segment, record_lines in zip(segments, split.records):
        head = record_lines[0]
        pages = sorted({line.page for line in record_lines})
        item = {**source, "segment": segment.index, "pages": pages}
        if segment.index in split.unrecognized_headings:
            unresolved.append({**item, "status": "pdf_roster_heading_unrecognized",
                               "lines": split.unrecognized_headings[segment.index]})
            accounted[segment.index] = "unresolved"
            continue
        name, title, extra = text_name(segment)
        if extra or name != heading_shape(head.text)[0]:
            unresolved.append({**item, "status": "pdf_roster_multiple_names", "extra_name_lines": extra})
            accounted[segment.index] = "unresolved"
            continue
        held = False
        low = [line for line in record_lines if line.source == "local_ocr" and not line.reviewed
               and (line.confidence or 0) < LINE_REVIEW_CONFIDENCE]
        if low:
            # The review page lists every uncertain line of this original.
            unresolved.append({**item, "status": "image_bio_low_confidence", "lines": len(low)})
            held = True
        basis = "pdf_text_layer"
        if head.source == "local_ocr" and not any(line is head for line in low):
            pool = [email for email in segment.emails if email.lower() not in shared]
            found = OcrName(name, head.page, head.line_index, head.confidence or 0.0, head.reviewed)
            admitted, decision = admit_ocr_name(found, pool)
            if admitted is None:
                unresolved.append({**item, "status": decision, "page": head.page,
                                   "line_index": head.line_index})
                name_reviews.append({"segment": segment.index, "name_page": head.page,
                                     "name_line_index": head.line_index})
                held = True
            basis = decision
        if held:
            accounted[segment.index] = "unresolved"
            continue
        record = PdfRecord(page_url, segment, name, title or title_default,
                           name_source="pdf_" + head.source.removeprefix("pdf_"), name_basis=basis,
                           media_url=media_url, sha256=sha256, pages=pages)
        email, email_basis, issue = choose_email(segment, name, shared)
        if issue:
            unresolved.append({**item, **issue})
        elif not email and segment.emails:
            unresolved.append({**item, "status": "pdf_roster_shared_email",
                               "email_count": len(segment.emails)})
        record.email, record.email_basis = email, email_basis
        records.append(record)
        accounted[segment.index] = "admitted"
    names = Counter(record.name for record in records)
    for duplicate in sorted(name for name, count in names.items() if count > 1):
        unresolved.append({**source, "status": "segment_duplicate_name", "occurrences": names[duplicate],
                           "segments": [record.segment.index for record in records if record.name == duplicate]})
        for record in records:
            if record.name == duplicate:
                accounted[record.segment.index] = "unresolved"
    records = [record for record in records if names[record.name] == 1]
    excluded = Counter(line.excluded for line in lines if line.excluded)
    observation = {"url": page_url, "kind": KIND, "media_url": media_url, "sha256": sha256,
                   "page_count": scanned["page_count"],
                   "pages_with_text_layer": scanned.get("pages_with_text_layer", 0),
                   "pages_ocrd": scanned.get("pages_ocrd", 0),
                   "records_detected": len(split.records), "records_admitted": len(records),
                   "unaccounted_records": sorted(index for index, state in accounted.items()
                                                 if state != "admitted"),
                   "preamble_lines": len(split.preamble), "preamble_emails": len(preamble_emails),
                   "excluded_lines": dict(excluded),
                   "heading_template": (None if split.template is None else
                                        {"labelled": split.template[0], "follows": split.template[1],
                                         "emphasized": split.template[2]})}
    layout = [{"page": line.page, "bbox": [round(value, 2) for value in line.bbox], "size": round(line.size, 2),
               "bold": line.bold, "source": line.source, "line_index": line.line_index,
               "confidence": line.confidence, "excluded": line.excluded, "text": line.text,
               "record": next((number for number, record in enumerate(split.records, 1)
                               if any(member is line for member in record)), None)}
              for line in lines]
    return {"records": records, "unresolved": unresolved, "observation": observation,
            "layout": layout, "name_reviews": name_reviews}


async def read_pdf_rosters(
    sources: list[dict],
    *,
    fetch_asset: Callable[[str], Awaitable[bytes]],
    evidence_dir: str | Path,
    cache_dir: str | Path | None = None,
    review_decisions_path: str | Path | None = None,
    ocr_engine: Any = None,
) -> dict:
    """Fetch each DOM-observed PDF in the browser session and reconcile its people.

    ``sources`` are ``{"url", "media_urls", "source_kind"}`` for roster pages
    whose verified cards hold only PDFs.  A page is ``read`` when every PDF on
    it was fetched and parsed; its records are then the page's candidates.
    """
    from server.image_bio_review import apply_review_decisions
    from server.image_biography import _OCR_CACHE_VERSION, scan_media

    out = Path(evidence_dir)
    out.mkdir(parents=True, exist_ok=True)
    engine = ocr_engine

    def ocr(image: bytes):
        # Loaded only when a page has no usable text layer.
        nonlocal engine
        if engine is None:
            from rapidocr_onnxruntime import RapidOCR
            engine = RapidOCR()
        return engine(image)

    records: list[PdfRecord] = []
    unresolved: list[dict] = []
    evidence: list[dict] = []
    name_evidence: list[dict] = []
    observations: dict[str, list[dict]] = {}
    read_pages: set[str] = set()
    seen_digests: dict[str, str] = {}
    for source in sources:
        page_url = source["url"]
        page_ok = True
        for media_url in source["media_urls"]:
            item = {"url": page_url, "media_url": media_url}
            try:
                original = await fetch_asset(media_url)
                if len(original) > 50_000_000:
                    raise ValueError("media exceeds 50 MB review limit")
            except Exception as exc:
                unresolved.append({**item, "status": "pdf_roster_asset_failed",
                                   "error": f"{type(exc).__name__}: {exc}"})
                page_ok = False
                continue
            if not original[:1024].lstrip().startswith(b"%PDF-"):
                # An error page or a login form served at the PDF address.
                unresolved.append({**item, "status": "pdf_roster_not_pdf", "bytes": len(original),
                                   "sha256": hashlib.sha256(original).hexdigest()})
                page_ok = False
                continue
            digest = hashlib.sha256(original).hexdigest()
            if digest in seen_digests:
                # The same original observed again (another page, viewer and link):
                # one set of people, counted once.
                observations.setdefault(page_url, []).append(
                    {"url": page_url, "kind": KIND, "media_url": media_url, "sha256": digest,
                     "duplicate_of": seen_digests[digest]})
                continue
            seen_digests[digest] = media_url
            shared_path = (Path(cache_dir) / digest[:2] / f"{digest}_pdf_v{_OCR_CACHE_VERSION}.json"
                           if cache_dir is not None else None)
            scanned = None
            if shared_path is not None and shared_path.is_file():
                try:
                    cached = json.loads(shared_path.read_text(encoding="utf-8"))
                    if (cached.get("ocr_cache_version") == _OCR_CACHE_VERSION
                            and cached.get("source_sha256") == digest and cached.get("kind") == "pdf"
                            and isinstance(cached.get("pages"), list)):
                        scanned = cached
                except (OSError, ValueError, TypeError, AttributeError):
                    scanned = None
            reused = scanned is not None
            try:
                if scanned is None:
                    scanned = await asyncio.to_thread(scan_media, original, "pdf", ocr_engine=ocr)
                    scanned["ocr_cache_version"] = _OCR_CACHE_VERSION
                    scanned["source_sha256"] = digest
                    if shared_path is not None:
                        shared_path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = shared_path.with_name(
                            f".{shared_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
                        try:
                            temporary.write_text(json.dumps(scanned, ensure_ascii=False), encoding="utf-8")
                            temporary.replace(shared_path)
                        finally:
                            temporary.unlink(missing_ok=True)
                stem = hashlib.sha256((page_url + "|" + media_url).encode("utf-8")).hexdigest()[:20]
                original_path, ocr_path = out / f"{stem}.pdf", out / f"{stem}_ocr.json"
                layout_path = out / f"{stem}_layout.json"
                original_path.write_bytes(original)
                ocr_path.write_text(json.dumps(scanned, ensure_ascii=False, indent=2), encoding="utf-8")
                adjusted, reviewed = apply_review_decisions(scanned, media_url, digest, review_decisions_path)
                result = await asyncio.to_thread(
                    reconcile_pdf, original, adjusted, reviewed, page_url=page_url,
                    media_url=media_url, sha256=digest)
            except Exception as exc:
                unresolved.append({**item, "status": "pdf_roster_unreadable",
                                   "error": f"{type(exc).__name__}: {exc}"})
                page_ok = False
                continue
            layout_path.write_text(json.dumps(result["layout"], ensure_ascii=False, indent=2), encoding="utf-8")
            for record in result["records"]:
                record.source_kind = source.get("source_kind") or record.source_kind
            records.extend(result["records"])
            unresolved.extend({**issue, "ocr_path": str(ocr_path)} if issue.get("status") in {
                "image_bio_low_confidence", "segment_name_needs_review"} else issue
                for issue in result["unresolved"])
            record_evidence = {"profile_url": page_url, "media_url": media_url, "kind": "pdf",
                               "purpose": KIND, "sha256": digest, "original_path": str(original_path),
                               "ocr_path": str(ocr_path), "layout_path": str(layout_path),
                               "page_count": scanned["page_count"], "pages_ocrd": scanned["pages_ocrd"],
                               "read_methods": {str(page["page"]): page.get("source") for page in scanned["pages"]},
                               "ocr_reused": reused, "human_reviewed_lines": len(reviewed),
                               "records": [{"segment": record.segment.index, "name": record.name,
                                            "pages": record.pages, "name_basis": record.name_basis}
                                           for record in result["records"]]}
            evidence.append(record_evidence)
            name_evidence.extend({"profile_url": page_url, "media_url": media_url, "kind": "pdf",
                                  "purpose": KIND, "sha256": digest, "original_path": str(original_path),
                                  "ocr_path": str(ocr_path), **review}
                                 for review in result["name_reviews"])
            observations.setdefault(page_url, []).append(result["observation"])
        if page_ok:
            read_pages.add(page_url)
    return {"records": records, "unresolved": unresolved, "evidence": evidence,
            "name_evidence": name_evidence, "observations": observations, "read_pages": read_pages}
