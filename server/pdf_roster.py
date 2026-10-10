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

A PDF whose text layer is a table (表格式名单: a header row with a ``姓名``
cell, one row per person) is read by columns instead: each row is a record,
its name and title are the cells under the ``姓名`` and ``职称`` headers, and a
cell wrapped onto two lines stays in its row.  A table row is not a biography,
so the record's biography stays empty.  Section headings above a header row
(``一、专任教师``, ``二、实验技术人员``) are kept as each row's evidence and
every section is read: no row is dropped by its heading.  The same header
repeated at the top of a continuation page, with no new heading before it,
continues its section.

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

from server.segmented_roster import (_LABELS, _SPLIT, OcrName, Segment, SegmentRecord, _name_like, _unique,
                                     admit_ocr_name, choose_email, heading_shape, text_name)
from server.faculty_extractor import find_emails
from server.name_list import _list_items, _rare_name

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
    cell_id: str | None = None  # image adapter evidence; absent for PDF lines

    @property
    def compact(self) -> str:
        return re.sub(r"\s+", "", self.text)


@dataclass
class PdfRecord(SegmentRecord):
    media_url: str = ""
    sha256: str = ""
    pages: list[int] = field(default_factory=list)
    table_row: dict | None = None

    def provenance(self) -> dict:
        return {**super().provenance(), "media_url": self.media_url, "pdf_sha256": self.sha256,
                "pdf_pages": self.pages, **({"table_row": self.table_row} if self.table_row else {})}


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


# ── Table-form PDF rosters ───────────────────────────────────────────────────
NAME_HEADER = "姓名"
TITLE_HEADER = "职称"
_HEADER_CELL = re.compile(r"^[\u4e00-\u9fff]{2,6}$")
_DIGITS = re.compile(r"^\d{1,4}$")


def _visual_rows(lines: list[LayoutLine]) -> list[list[LayoutLine]]:
    """Lines of one page grouped into rows: boxes that overlap vertically.

    A cell wrapped onto two lines overlaps its row's other cells, so it stays
    in that row; neighbouring rows do not overlap.
    """
    rows: list[list[LayoutLine]] = []
    for line in sorted(lines, key=lambda item: (item.bbox[1], item.bbox[0])):
        top, bottom = line.bbox[1], line.bbox[3]
        joined = [row for row in rows
                  if any(min(bottom, other.bbox[3]) - max(top, other.bbox[1]) > 1.0 for other in row)]
        if not joined:
            rows.append([line])
            continue
        merged = [line] + [member for row in joined for member in row]
        rows = [row for row in rows if not any(row is other for other in joined)] + [merged]
    for row in rows:
        row.sort(key=lambda item: item.bbox[0])
    return sorted(rows, key=lambda row: min(item.bbox[1] for item in row))


def _is_header(row: list[LayoutLine], max_chars=6) -> bool:
    cells = [line.compact for line in row]
    return (len(cells) >= 2 and cells.count(NAME_HEADER) == 1
            and all(re.fullmatch(r"[一-鿿]{2," + str(max_chars) + "}", cell) for cell in cells))


def _center(line: LayoutLine) -> float:
    return (line.bbox[0] + line.bbox[2]) / 2


@dataclass
class TableRow:
    number: int
    page: int
    cells: dict[str, str]
    section: int                # index into TableLayout.sections
    lines: list[LayoutLine]
    cell_roles: dict[str, str] = field(default_factory=dict)
    cell_bbox: tuple[float, float, float, float] | None = None


@dataclass
class TableLayout:
    rows: list[TableRow]
    sections: list[dict]
    issues: list[dict]
    skipped: Counter
    kind: str = "table"


def rank_grid_layout(original: bytes, lines: list[LayoutLine]) -> TableLayout | None:
    """Ruled one-person cells under merged rank headings, not text-line guesses.

    Native cell rectangles keep wrapped names together. Each occupied cell is
    accounted for by the existing name-review gate. Mixed group titles remain
    provenance and cannot become an invented individual title.
    """
    import pymupdf
    from server.titles import TITLE_KEYWORDS

    # The native library's install suggestion is not protocol output.
    quiet = getattr(pymupdf, "no_recommend_layout", None)
    if callable(quiet):
        quiet()
    result = TableLayout([], [], [], Counter(), kind="rank_grid")
    usable = [line for line in lines if not line.rotated and line.source == "pdf_text_layer"
              and line.excluded in (None, "table_section_heading")]
    with pymupdf.open(stream=original, filetype="pdf") as document:
        for page in document:
            candidates = []
            for table in page.find_tables(strategy="lines").tables:
                if table.col_count < 2:
                    continue
                cells = []
                section = None
                valid = True
                for row in table.rows:
                    boxes = list(dict.fromkeys(tuple(box) for box in row.cells if box))
                    occupied = []
                    for box in boxes:
                        owned = [line for line in usable if line.page == page.number+1
                                 and box[0] < _center(line) < box[2]
                                 and box[1] < (line.bbox[1]+line.bbox[3])/2 < box[3]]
                        if owned:
                            occupied.append((box, owned))
                    if not occupied:
                        continue
                    box, owned = occupied[0]
                    merged = (len(occupied) == 1 and abs(box[0]-table.bbox[0]) < 1
                              and abs(box[2]-table.bbox[2]) < 1)
                    if merged:
                        heading = " ".join(line.text for line in sorted(owned, key=lambda line: line.bbox[1]))
                        parts = re.split(r"[/／]", re.sub(r"\s+", "", heading))
                        if not parts or not all(part in TITLE_KEYWORDS for part in parts):
                            valid = False
                            break
                        section = {"heading": heading, "header": [NAME_HEADER], "page": page.number+1,
                                   "rows": 0, "layout": "rank_grid", "heading_bbox": list(box),
                                   "rank_parts": parts}
                        cells.append((section, []))
                    elif section is None:
                        valid = False
                        break
                    else:
                        cells[-1][1].extend(occupied)
                if valid and cells and any(owned for _, owned in cells):
                    candidates.append((table, cells))
            if not candidates:
                return None
            assigned = set()
            for table, sections in candidates:
                # Small raster names inside a ruled cell are still roster content.
                # The page-wide image-area guard cannot prove these cells were read.
                for image in page.get_image_info():
                    raster = pymupdf.Rect(image['bbox'])
                    if raster.intersects(pymupdf.Rect(table.bbox)):
                        result.issues.append({'status': 'pdf_roster_raster_unread', 'page': page.number+1,
                                              'bbox': list(raster), 'layout': 'rank_grid'})
                for section, cells in sections:
                    result.sections.append(section)
                    section_id = len(result.sections)-1
                    for box, owned in cells:
                        text = " ".join(line.text for line in sorted(owned, key=lambda line: (line.bbox[1],line.bbox[0])))
                        values = {NAME_HEADER: text}
                        if len(section['rank_parts']) == 1:
                            values[TITLE_HEADER] = section['rank_parts'][0]
                        section['rows'] += 1
                        result.rows.append(TableRow(len(result.rows)+1, page.number+1, values, section_id, owned,
                                                    cell_bbox=box))
                        assigned.update(id(line) for line in owned)
                    assigned.update(id(line) for line in usable if line.page == page.number+1
                                    and section['heading_bbox'][0] < _center(line) < section['heading_bbox'][2]
                                    and section['heading_bbox'][1] < (line.bbox[1]+line.bbox[3])/2 < section['heading_bbox'][3])
            for line in usable:
                if line.page == page.number+1 and id(line) not in assigned:
                    # A native cell finder is not allowed to silently omit text.
                    result.issues.append({"status": "pdf_roster_table_row_unassigned", "page": line.page,
                                          "cells": 1, "bbox": list(line.bbox)})
    return result if result.rows else None


def table_layout(lines: list[LayoutLine], *, allowed_sources=("pdf_text_layer",),
                 max_header_chars=6) -> TableLayout | None:
    """Rows of a table-form roster, or None when the PDF has no name header row."""
    usable = [line for line in lines if not line.rotated and line.source in allowed_sources
              and line.excluded in (None, "page_number", "table_section_heading")]
    pages = sorted({line.page for line in usable})
    page_rows = [(page, _visual_rows([line for line in usable if line.page == page])) for page in pages]
    if not any(_is_header(row, max_header_chars) for _, rows in page_rows for row in rows):
        return None
    result = TableLayout([], [], [], Counter())
    columns: list[tuple[str, float]] | None = None
    pending_heading: str | None = None
    for page, rows in page_rows:
        for row in rows:
            texts = [line.compact for line in row]
            if len(row) == 1 and row[0].excluded == "table_section_heading":
                pending_heading = row[0].text.strip()
                continue
            if _is_header(row, max_header_chars):
                if (columns is not None and pending_heading is None
                        and texts == [name for name, _ in columns]):
                    # The header repeated on a continuation page: the same table goes on.
                    columns = [(line.compact, _center(line)) for line in row]
                    result.skipped["repeated_header"] += 1
                    continue
                columns = [(line.compact, _center(line)) for line in row]
                result.sections.append({"heading": pending_heading, "header": texts, "page": page, "rows": 0})
                pending_heading = None
                continue
            if len(row) == 1 and (_DIGITS.match(texts[0]) or _PAGE_NUMBER.match(row[0].text)):
                # A page number, or an index cell left alone on its line.
                result.skipped["lone_number"] += 1
                continue
            if columns is None:
                if len(row) == 1:
                    pending_heading = row[0].text.strip()
                else:
                    result.skipped["before_table"] += 1
                continue
            cells: dict[str, list[tuple[float, str]]] = {}
            roles = {}
            unaligned = []
            for line in row:
                center = _center(line)
                name, column_center = min(columns, key=lambda column: abs(column[1] - center))
                width = line.bbox[2] - line.bbox[0]
                if abs(column_center - center) > max(width, 24.0):
                    unaligned.append(line)
                    continue
                cells.setdefault(name, []).append((line.bbox[1], line.text.strip()))
                if line.cell_id:
                    roles[line.cell_id] = name
            if NAME_HEADER not in cells:
                if len(row) == 1:
                    # Possibly the heading of the next table; decided by what follows.
                    pending_heading = row[0].text.strip()
                    continue
                result.issues.append({"status": "pdf_roster_table_row_unassigned", "page": page,
                                      "cells": len(row)})
                continue
            if unaligned:
                result.issues.append({"status": "pdf_roster_table_row_unassigned", "page": page,
                                      "cells": len(row), "unaligned_cells": len(unaligned)})
                continue
            if pending_heading is not None:
                # A one-cell line inside a table that no header followed.
                result.issues.append({"status": "pdf_roster_table_row_unassigned", "page": page, "cells": 1})
                pending_heading = None
            result.sections[-1]["rows"] += 1
            result.rows.append(TableRow(len(result.rows) + 1, page,
                                        {name: " ".join(text for _, text in sorted(parts))
                                         for name, parts in cells.items()},
                                        len(result.sections) - 1, row, roles))
    if pending_heading is not None:
        result.issues.append({"status": "pdf_roster_table_row_unassigned", "page": pages[-1], "cells": 1})
    return result


def reconcile_table(table: TableLayout, *, page_url: str, media_url: str, sha256: str,
                    title_default: str, name_decisions: dict[str, dict]) -> dict:
    from server.faculty_extractor import FacultyCandidate
    from server import name_review

    source = {"url": page_url, "media_url": media_url}
    unresolved = [{**source, **issue} for issue in table.issues]
    # Every section is read; its heading stays with each row as evidence.
    rows = table.rows
    for section, item in enumerate(table.sections):
        # An index column numbers its section 1..N: a gap is a row the reader missed.
        numbers = [row.cells.get("序号", "") for row in rows if row.section == section]
        if "序号" in item["header"] and sorted(numbers, key=lambda value: (len(value), value)) != [
                str(index) for index in range(1, len(numbers) + 1)]:
            unresolved.append({**source, "status": "pdf_roster_table_index_gap", "section_rows": len(numbers),
                               "page": item["page"]})
    emails_by_row = {row.number: _unique(find_emails(" ".join(row.cells.values()))) for row in rows}
    seen = Counter(email.lower() for emails in emails_by_row.values() for email in {e.lower() for e in emails})
    shared = {email for email, count in seen.items() if count > 1}
    records: list[PdfRecord] = []
    accounted: dict[int, str] = {}
    for row in rows:
        cell = row.cells[NAME_HEADER]
        if table.kind == 'rank_grid':
            tokens = [token for token in _SPLIT.split(cell) if token]
            explicit_members = len(tokens) > 1 and re.search(r'[、，,;；|/]', cell)
            # Reuse the list reader's padded-name boundary: two/three isolated
            # Han glyphs may be one name; four can already contain two people.
            # Unknown full tokens must not be concatenated into a guessed name.
            items = _list_items(cell)
            ambiguous_tokens = len(tokens) > 1 and (len(items) != 1 or len(items[0].split()) > 1)
            if explicit_members or ambiguous_tokens:
                unresolved.append({**source, 'status': 'pdf_roster_table_row_unassigned',
                                   'page': row.page, 'cells': 1, 'reason': 'ambiguous_members_in_grid_cell',
                                   'bbox': list(row.cell_bbox)})
                accounted[row.number] = 'unresolved'
                continue
        title = row.cells.get(TITLE_HEADER, "").strip() or title_default
        evidence = {"section": table.sections[row.section]["heading"], "cells": row.cells, "page": row.page,
                    "bboxes": [[round(value, 2) for value in line.bbox] for line in row.lines]}
        if any(line.cell_id for line in row.lines):
            evidence["cell_ids"] = [line.cell_id for line in row.lines if line.cell_id]
        if table.kind == 'rank_grid':
            evidence.update(layout='rank_grid', grid_cell=list(row.cell_bbox))
        item = {**source, "segment": row.number, "pages": [row.page]}
        # The column header says 姓名, so a rare surname there is a name too
        # (badr 2026-10-09); an unreadable cell still goes to a person.
        compact = re.sub(r'\s+', '', cell)
        name = ((_name_like(compact) or (_rare_name(cell) if table.kind == 'table' else None))
            if re.fullmatch(r"[\u4e00-\u9fff·•\s]+", cell) else None)
        basis = "pdf_rank_grid_cell" if table.kind == "rank_grid" else "pdf_table_name_column"
        if name is None:
            # The column says this is a name; a person confirms what it reads.
            context = " | ".join(f"{key}：{value}" for key, value in row.cells.items())
            reason = 'pdf_table_name_cell'
            if table.kind == 'rank_grid':
                # This grid has no literal name header. Rare/unknown values
                # require a decision bound to the exact original and cell.
                reason = 'pdf_rank_grid_name_cell'
                context += ' | ' + json.dumps({'pdf_sha256': sha256,
                    'page': row.page, 'cell': list(row.cell_bbox)}, sort_keys=True)
            candidate = FacultyCandidate(name=cell, url=page_url, title=title,
                                         card_context=context, name_review_reason=reason)
            review = name_review.pending_item(page_url, candidate)
            decision = name_decisions.get(review["id"])
            if decision is None:
                unresolved.append({**review, "media_url": media_url, "segment": row.number, "page": row.page})
                accounted[row.number] = "unresolved"
                continue
            if decision["verdict"] != "person":
                accounted[row.number] = "not_person"
                continue
            name, basis = decision["verified_name"], "human_reviewed_name"
        segment = Segment(index=row.number, html="", text="", emails=emails_by_row[row.number])
        record = PdfRecord(page_url, segment, name, title, name_source="pdf_table", name_basis=basis,
                           media_url=media_url, sha256=sha256, pages=[row.page], table_row=evidence)
        email, email_basis, issue = choose_email(segment, name, shared)
        if issue:
            unresolved.append({**item, **issue})
        record.email, record.email_basis = email, email_basis
        records.append(record)
        accounted[row.number] = "admitted"
    names = Counter(record.name for record in records)
    for duplicate in sorted(name for name, count in names.items() if count > 1):
        unresolved.append({**source, "status": "segment_duplicate_name", "occurrences": names[duplicate],
                           "segments": [record.segment.index for record in records if record.name == duplicate]})
        for record in records:
            if record.name == duplicate:
                accounted[record.segment.index] = "unresolved"
    records = [record for record in records if names[record.name] == 1]
    if not records and not unresolved:
        unresolved.append({**source, "status": "pdf_roster_records_not_found"})
    return {"records": records, "unresolved": unresolved, "accounted": accounted, "sections": table.sections}


def reconcile_pdf(original: bytes, scanned: dict, reviewed: set[tuple[int, int]], *, page_url: str,
                  media_url: str, sha256: str, title_default: str = "教师",
                  name_decisions: dict[str, dict] | None = None) -> dict:
    """Records, review items and structure-only observation for one PDF original."""
    lines, page_issues = layout_lines(original, scanned, reviewed)
    table = table_layout(lines)
    if table is None:
        table = rank_grid_layout(original, lines)
    if table is not None:
        found = reconcile_table(table, page_url=page_url, media_url=media_url, sha256=sha256,
                                title_default=title_default, name_decisions=name_decisions or {})
        unresolved = [{"url": page_url, "media_url": media_url, **issue} for issue in page_issues]
        unresolved += found["unresolved"]
        observation = {"url": page_url, "kind": KIND, "layout": table.kind, "media_url": media_url,
                       "sha256": sha256, "page_count": scanned["page_count"],
                       "pages_with_text_layer": scanned.get("pages_with_text_layer", 0),
                       "pages_ocrd": scanned.get("pages_ocrd", 0),
                       "records_detected": len(table.rows), "records_admitted": len(found["records"]),
                       "unaccounted_records": sorted(index for index, state in found["accounted"].items()
                                                     if state == "unresolved"),
                       "not_person_rows": sorted(index for index, state in found["accounted"].items()
                                                 if state == "not_person"),
                       "sections": found["sections"], "skipped_lines": dict(table.skipped)}
        row_of = {id(line): row.number for row in table.rows for line in row.lines}
        layout = [{"page": line.page, "bbox": [round(value, 2) for value in line.bbox],
                   "size": round(line.size, 2), "bold": line.bold, "source": line.source,
                   "line_index": line.line_index, "confidence": line.confidence, "excluded": line.excluded,
                   "text": line.text, "record": row_of.get(id(line))} for line in lines]
        return {"records": found["records"], "unresolved": unresolved, "observation": observation,
                "layout": layout, "name_reviews": []}
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
    name_decisions_path: str | Path | None = None,
    ocr_engine: Any = None,
) -> dict:
    """Fetch each DOM-observed PDF in the browser session and reconcile its people.

    ``sources`` are ``{"url", "media_urls", "source_kind"}`` for roster pages
    whose verified cards hold only PDFs.  A page is ``read`` when every PDF on
    it was fetched and parsed; its records are then the page's candidates.
    """
    from server.image_bio_review import apply_review_decisions
    from server.image_biography import _OCR_CACHE_VERSION, scan_media
    from server.name_review import load_decisions

    name_decisions = load_decisions(name_decisions_path)

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
                    media_url=media_url, sha256=digest, name_decisions=name_decisions)
            except Exception as exc:
                unresolved.append({**item, "status": "pdf_roster_unreadable",
                                   "error": f"{type(exc).__name__}: {exc}"})
                page_ok = False
                continue
            layout_path.write_text(json.dumps(result["layout"], ensure_ascii=False, indent=2), encoding="utf-8")
            for record in result["records"]:
                record.source_kind = source.get("source_kind") or record.source_kind
                record.source_evidence = source.get('source_evidence')
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
