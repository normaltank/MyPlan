"""Deterministic ruled-image tables with immutable cell evidence and human review.

Column/section/index/continuation semantics reuse the PDF table reader (#12).
Whole-image approval is independent of OCR confidence; no model supplies fields.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from io import BytesIO
import hashlib
from importlib.metadata import version
import json
import math
from pathlib import Path
import re

from PIL import Image

from server import review_store
from server.identity_review import digest
from server.pdf_roster import LayoutLine, PdfRecord, TableLayout, TableRow, reconcile_table, table_layout
from server.segmented_roster import SegmentRecord, _name_like
from server.titles import resolve_title

POLICY = "image-table-v1"
REVIEW_FILE = "image_table_reviews.json"


@dataclass
class ImageTableRecord(PdfRecord):
    def provenance(self):
        return {**SegmentRecord.provenance(self), "media_url": self.media_url,
                "image_sha256": self.sha256, "image_pages": self.pages, "table_row": self.table_row}


def ruled_cells(original: bytes):
    """Detect a rectangular grid and join absent interior rules into merged cells."""
    import cv2
    import numpy as np
    image = cv2.imdecode(np.frombuffer(original, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError("image not readable")
    height, width = image.shape
    if width * height > 40_000_000 or min(width, height) < 32:
        raise ValueError("image dimensions unsupported")
    binary = cv2.adaptiveThreshold(image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, 31, 12)
    masks, boxes = {}, {}
    for axis, kernel in (("h", (max(25, width // 20), 1)), ("v", (1, max(25, height // 30)))):
        mask = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, kernel))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        found = [cv2.boundingRect(c) for c in contours]
        boxes[axis] = [b for b in found if b[2 if axis == "h" else 3] >= (width if axis == "h" else height) * .2]
        masks[axis] = mask
    if len(boxes["h"]) < 2:
        raise ValueError("no ruled table")
    left = round(float(np.median([b[0] for b in boxes["h"]])))
    right = round(float(np.median([b[0] + b[2] - 1 for b in boxes["h"]])))
    xs = sorted({round(x + (w - 1) / 2) for x, y, w, h in boxes["v"]
                 if left - 4 <= x <= right + 4})
    ys = sorted({round(y + (h - 1) / 2) for x, y, w, h in boxes["h"]})
    if len(xs) < 2 or len(xs) * len(ys) > 6000:
        raise ValueError("grid unsupported")

    def support(axis, fixed, start, end):
        mask = masks[axis]
        part = (mask[max(0, fixed - 2):fixed + 3, start:end]
                if axis == "h" else mask[start:end, max(0, fixed - 2):fixed + 3])
        if part.size == 0:
            return 0.0
        return float(np.mean(np.any(part != 0, axis=0 if axis == "h" else 1)))

    rows, cols = len(ys) - 1, len(xs) - 1
    parents = list(range(rows * cols))
    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    ambiguous = False
    for row in range(rows):
        for col in range(cols):
            for other, score in (
                ((row, col + 1), support("v", xs[col + 1], ys[row] + 3, ys[row + 1] - 2)),
                ((row + 1, col), support("h", ys[row + 1], xs[col] + 3, xs[col + 1] - 2)),
            ):
                if other[0] >= rows or other[1] >= cols:
                    continue
                if score < .15:
                    parents[find(row * cols + col)] = find(other[0] * cols + other[1])
                elif score < .85:
                    ambiguous = True
    groups = {}
    for row in range(rows):
        for col in range(cols):
            groups.setdefault(find(row * cols + col), []).append((row, col))
    cells = []
    for members in groups.values():
        r0, r1 = min(r for r, c in members), max(r for r, c in members) + 1
        c0, c1 = min(c for r, c in members), max(c for r, c in members) + 1
        if len(members) != (r1 - r0) * (c1 - c0):
            ambiguous = True
        bbox = [xs[c0], ys[r0], xs[c1], ys[r1]]
        edges = [support("h", ys[r0], xs[c0], xs[c1]), support("h", ys[r1], xs[c0], xs[c1]),
                 support("v", xs[c0], ys[r0], ys[r1]), support("v", xs[c1], ys[r0], ys[r1])]
        if min(edges) < .85:
            ambiguous = True
        cells.append({"row": r0, "column": c0, "row_span": r1 - r0, "column_span": c1 - c0,
                      "bbox": bbox, "geometry_score": min(edges), "edge_support": edges})
    return cells, [width, height], ambiguous


def _read_lines(result):
    lines = result[0] if isinstance(result, tuple) else result
    return [{"text": str(item[1]), "confidence": float(item[2]),
             "box": [[float(v) for v in point] for point in item[0]]} for item in lines or []]


def scan_cells(original, *, ocr_engine=None):
    if ocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        ocr_engine = RapidOCR()
    cells, size, ambiguous = ruled_cells(original)
    source_sha = hashlib.sha256(original).hexdigest()
    topology = digest([{k: v for k, v in c.items() if k not in {"geometry_score", "edge_support"}}
                       for c in cells])
    image = Image.open(BytesIO(original)).convert("RGB")
    whole = _read_lines(ocr_engine(original))
    for cell in cells:
        x0, y0, x1, y1 = cell["bbox"]
        margin = max(2, min(4, (x1 - x0) // 10, (y1 - y0) // 10))
        crop_box = [x0 + margin, y0 + margin, x1 - margin, y1 - margin]
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            raise ValueError("cell too small")
        buffer = BytesIO()
        # Tiny screenshot cells need the same readable glyph scale as a full scan.
        cropped = image.crop(crop_box)
        scale = max(1, min(3, 72 // max(1, cropped.height)))
        cropped = cropped.resize((cropped.width * scale, cropped.height * scale))
        cropped.save(buffer, format="PNG")
        crop = buffer.getvalue()
        lines = _read_lines(ocr_engine(crop))
        cell.update(raw_lines=lines, raw_text="\n".join(line["text"] for line in lines),
                    crop_bbox=crop_box, ocr_scale=scale,
                    crop_sha256=hashlib.sha256(crop).hexdigest())
        scores = [line["confidence"] for line in lines]
        valid = all(math.isfinite(score) and 0 <= score <= 1 for score in scores)
        weights = [max(1, len(line["text"])) for line in lines]
        cell["recognition_min"] = min(scores) if scores and valid else None
        cell["recognition_mean"] = (sum(s * w for s, w in zip(scores, weights)) / sum(weights)
                                    if scores and valid else None)
        cell["id"] = digest([POLICY, source_sha, topology, cell["bbox"], cell["raw_lines"]])
        cell["source_sha256"] = source_sha
        cell["policy"] = POLICY
    # Every whole-image text box inside the grid must have exactly one cell owner.
    unassigned = []
    for index, line in enumerate(whole):
        xs, ys = [p[0] for p in line["box"]], [p[1] for p in line["box"]]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        area = max(1, (x1 - x0) * (y1 - y0))
        overlaps = [(max(0, min(x1, c["bbox"][2]) - max(x0, c["bbox"][0])) *
                     max(0, min(y1, c["bbox"][3]) - max(y0, c["bbox"][1])) / area, c)
                    for c in cells]
        owners = [(score, c) for score, c in overlaps if score >= .9]
        if not owners and max((score for score, c in overlaps), default=0) == 0:
            continue  # Outside-table page layout is retained in whole_ocr.
        if len(owners) != 1:
            unassigned.append(index)
        else:
            score, cell = owners[0]
            cell.setdefault("whole_image_lines", []).append(line)
            cell["assignment_score"] = min(cell.get("assignment_score", 1.0), score)
            # Crop OCR must account for the entire line, not silently lose words.
            if re.sub(r"\s+", "", line["text"]) not in re.sub(r"\s+", "", cell["raw_text"]):
                cell["ocr_disagreement"] = True
    for cell in cells:
        # Full-image recognition retains context and glyph scale. Its boxes
        # may supply fields only after unique geometric cell attribution.
        # Crop recognition remains an independent audit, never discarded.
        cell["crop_lines"], cell["crop_text"] = cell["raw_lines"], cell["raw_text"]
        if cell.get("whole_image_lines"):
            lines = sorted(cell["whole_image_lines"], key=lambda line: (min(p[1] for p in line["box"]),
                                                                       min(p[0] for p in line["box"])))
            cell["raw_lines"] = lines
            cell["raw_text"] = "\n".join(line["text"] for line in lines)
            scores = [line["confidence"] for line in lines]
            weights = [max(1, len(line["text"])) for line in lines]
            if all(math.isfinite(score) and 0 <= score <= 1 for score in scores):
                cell["recognition_min"] = min(scores)
                cell["recognition_mean"] = sum(s * w for s, w in zip(scores, weights)) / sum(weights)
            else:
                cell["recognition_min"] = cell["recognition_mean"] = None
        cell["id"] = digest([POLICY, source_sha, topology, cell["bbox"], cell["raw_lines"], cell["crop_lines"]])
    return {"source_sha256": source_sha, "topology_sha256": topology, "policy": POLICY,
            "size": size, "cells": cells, "whole_ocr": whole, "unassigned": unassigned,
            "grid_ambiguous": ambiguous}


def load_reviews(path):
    return review_store.load(path)


def combine_scans(scans):
    """Treat ordered image originals as pages for #12's continuation state."""
    if len(scans) == 1:
        return scans[0]
    first = scans[0]
    base_left = min(c["bbox"][0] for c in first["cells"])
    base_right = max(c["bbox"][2] for c in first["cells"])
    cells, unassigned, whole = [], [], []
    columns = max(c["column"] + c["column_span"] for c in first["cells"])
    ambiguous = any(s["grid_ambiguous"] for s in scans)
    reference = sorted({round((c["bbox"][0] - base_left) / (base_right - base_left), 3)
                        for c in first["cells"]})
    for page, scan in enumerate(scans, 1):
        left = min(c["bbox"][0] for c in scan["cells"])
        right = max(c["bbox"][2] for c in scan["cells"])
        ratios = sorted({round((c["bbox"][0] - left) / (right - left), 3) for c in scan["cells"]})
        if (max(c["column"] + c["column_span"] for c in scan["cells"]) != columns
                or len(ratios) != len(reference)
                or any(abs(a - b) > .025 for a, b in zip(ratios, reference))):
            ambiguous = True
        for original in scan["cells"]:
            cell = {**original, "page": page, "image_height": scan["size"][1]}
            x0, y0, x1, y1 = cell["bbox"]
            cell["layout_bbox"] = [base_left + (x0 - left) / (right - left) * (base_right - base_left),
                                   y0, base_left + (x1 - left) / (right - left) * (base_right - base_left), y1]
            cells.append(cell)
        unassigned.extend({"page": page, "line": index} for index in scan["unassigned"])
        whole.extend({**line, "page": page} for line in scan["whole_ocr"])
    return {"policy": POLICY, "source_sha256": digest([s["source_sha256"] for s in scans]),
            "topology_sha256": digest([s["topology_sha256"] for s in scans]), "size": first["size"],
            "cells": cells, "whole_ocr": whole, "unassigned": unassigned, "grid_ambiguous": ambiguous}


def parse_cells(scanned, *, page_url, media_url, reviews=None):
    """Both row tables and grouped-name cells; all sections remain in evidence."""
    from server import name_review
    from server.faculty_extractor import FacultyCandidate
    reviews = reviews or {}
    cells, issues, layout_lines, reviewed_cells = [], [], [], set()
    source = {"url": page_url, "media_url": media_url, "source_sha256": scanned["source_sha256"]}
    if scanned["grid_ambiguous"]:
        issues.append({**source, "status": "table_grid_ambiguous", "cell_ids": []})
    if scanned["unassigned"]:
        issues.append({**source, "status": "table_text_unassigned", "line_indices": scanned["unassigned"]})
    for original in scanned["cells"]:
        cell = dict(original)
        saved = reviews.get(cell["id"], {})
        verified = (saved.get("source_sha256") == cell.get("source_sha256", scanned["source_sha256"])
                    and saved.get("policy") == POLICY and saved.get("id") == cell["id"]
                    and isinstance(saved.get("verified_text"), str) and type(saved.get("reviewed_by")) is int
                    and saved["reviewed_by"] > 0)
        cell["effective_text"] = saved["verified_text"] if verified else cell["raw_text"]
        cell["field_role"] = None
        cell["header_mapping_score"] = None
        cell["state"] = "reviewed" if verified else "accepted"
        if verified:
            reviewed_cells.add(cell["id"])
        elif (cell.get("recognition_min") is None or cell["recognition_min"] < .9
              or cell.get("ocr_disagreement")):
            # Blank cells are reviewed too; absent text is not proved-empty data.
            code = "table_cell_unreadable" if not cell["raw_text"] else "table_cell_low_confidence"
            cell["state"] = "unresolved"
            issues.append({**source, "status": code, "cell_ids": [cell["id"]]})
        cells.append(cell)
        line = LayoutLine(cell.get("page", 1), cell["effective_text"],
                          tuple(cell.get("layout_bbox", cell["bbox"])), 12, False, "local_ocr",
                          cell.get("image_height", scanned["size"][1]), confidence=cell["recognition_min"],
                          reviewed=verified, cell_id=cell["id"])
        total_columns = max(c["column"] + c["column_span"] for c in scanned["cells"])
        if cell["column_span"] == total_columns and total_columns > 1:
            line.excluded = "table_section_heading"
        layout_lines.append(line)
    table = table_layout(layout_lines, allowed_sources=("local_ocr",), max_header_chars=12)
    grouped = False
    if table is None:
        header_names = [c for c in cells if re.sub(r"\s+", "", c["effective_text"]) == "姓名"]
        rank_headers = [c for c in cells if re.sub(r"\s+", "", c["effective_text"]) in {"职称/学位", "职称／学位"}]
        if len(header_names) == len(rank_headers) == 1 and header_names[0]["row"] == rank_headers[0]["row"]:
            grouped = True
            table = TableLayout([], [], [], Counter())
            for c in cells:
                if c["column"] != header_names[0]["column"] or c["row"] <= header_names[0]["row"]:
                    continue
                label = next((label for label in cells if label["column"] == rank_headers[0]["column"]
                              and label["row"] <= c["row"] < label["row"] + label["row_span"]), None)
                if label is None:
                    issues.append({**source, "status": "table_header_unresolved", "cell_ids": [c["id"]]})
                    continue
                raw_title = label["effective_text"]
                label["field_role"], label["header_mapping_score"] = "职称/学位", 1
                c["field_role"], c["header_mapping_score"] = "姓名", 1
                title = resolve_title(re.split(r"[/／]", raw_title, maxsplit=1)[0].strip())
                table.sections.append({"heading": raw_title, "header": ["姓名", "职称"], "page": 1, "rows": 0})
                # Human separators are explicit. Raw OCR may join names and must not be guessed apart.
                names = [s.strip() for s in re.split(r"[\n、,，;；]+", c["effective_text"]) if s.strip()]
                if c["id"] not in reviewed_cells:
                    names = [token for part in names for token in part.split()]
                for name in names:
                    ambiguous_name = (not re.fullmatch(r"[一-鿿·•]{2,4}", name)
                                      or any(_name_like(name[:i]) and _name_like(name[i:])
                                             for i in range(2, len(name) - 1)))
                    if c["id"] not in reviewed_cells and ambiguous_name:
                        issues.append({**source, "status": "table_name_segmentation_ambiguous", "cell_ids": [c["id"]]})
                        continue
                    line = LayoutLine(c.get("page", 1), name, tuple(c["bbox"]), 12, False, "local_ocr",
                                      scanned["size"][1], confidence=c["recognition_min"],
                                      reviewed=c["id"] in reviewed_cells, cell_id=c["id"])
                    label_line = LayoutLine(c.get("page", 1), raw_title, tuple(label["bbox"]),
                                            12, False, "local_ocr", scanned["size"][1], cell_id=label["id"])
                    table.rows.append(TableRow(len(table.rows) + 1, 1, {"姓名": name, "职称": title},
                                               len(table.sections) - 1, [line, label_line]))
                    table.sections[-1]["rows"] += 1
    if table is None:
        issues.append({**source, "status": "table_header_unresolved", "cell_ids": [c["id"] for c in cells]})
        found = {"records": [], "accounted": {}, "sections": []}
    else:
        decisions = {}
        retained = []
        for row in table.rows:
            if not grouped and sum(role == "姓名" for role in row.cell_roles.values()) > 1:
                issues.append({**source, "status": "table_row_unassigned",
                               "cell_ids": list(row.cell_roles), "page": row.page})
            for cell in cells:
                if cell["id"] in row.cell_roles:
                    cell["field_role"] = row.cell_roles[cell["id"]]
                    cell["header_mapping_score"] = 1
            row_cells = [c for c in cells if c["id"] in [line.cell_id for line in row.lines]]
            if (row_cells and all(c["id"] in reviewed_cells and not c["raw_text"].strip()
                                  and not c["effective_text"].strip() for c in row_cells)):
                table.sections[row.section]["rows"] -= 1
                table.skipped["human_confirmed_blank_row"] += 1
                continue
            retained.append(row)
            name_cells = [c for c in cells if c["id"] in reviewed_cells
                          and c["id"] in [line.cell_id for line in row.lines]
                          and (grouped or c["effective_text"] == row.cells["姓名"])]
            if name_cells and row.cells["姓名"].strip():
                candidate = FacultyCandidate(name=row.cells["姓名"], url=page_url,
                    title=row.cells.get("职称", "教师"),
                    card_context=" | ".join(f"{k}：{v}" for k, v in row.cells.items()),
                    name_review_reason="pdf_table_name_cell")
                item = name_review.pending_item(page_url, candidate)
                decisions[item["id"]] = {"verdict": "person", "verified_name": row.cells["姓名"]}
        table.rows = retained
        found = reconcile_table(table, page_url=page_url, media_url=media_url,
                                sha256=scanned["source_sha256"], title_default="教师", name_decisions=decisions)
        for issue in found["unresolved"]:
            codes = {"pdf_roster_table_index_gap": "table_index_gap",
                     "pdf_roster_table_row_unassigned": "table_row_unassigned",
                     "rare_surname_needs_review": "table_name_unreadable",
                     "segment_duplicate_name": "table_duplicate_name",
                     "segment_email_ambiguous": "table_email_ambiguous"}
            issues.append({**issue, "status": codes.get(issue["status"], "table_record_unresolved"),
                           "origin_status": issue["status"], "adapter": "image_table"})
    records = [ImageTableRecord(**vars(record)) for record in found["records"]]
    for record in records:
        record.name_source = "image_table"
        if not record.table_row.get("cell_ids"):
            record.table_row["cell_ids"] = [c["id"] for c in cells
                                           if c["bbox"] in record.table_row["bboxes"]]
        owner = next((c for c in cells if c["id"] in record.table_row["cell_ids"]), None)
        if owner:
            record.sha256 = owner.get("source_sha256", scanned["source_sha256"])
    detected = len(table.rows) if table else 0
    approval_id = digest([POLICY, scanned["source_sha256"], scanned["topology_sha256"], scanned["cells"],
                          [[c["id"], c["effective_text"], c["state"] == "reviewed"] for c in cells]])
    approval = reviews.get(approval_id, {})
    if (approval.get("kind") != "full_image" or approval.get("expected_count") != detected
            or approval.get("source_sha256") != scanned["source_sha256"]
            or approval.get("policy") != POLICY or type(approval.get("reviewed_by")) is not int
            or approval["reviewed_by"] <= 0):
        issues.append({**source, "status": "table_full_image_review_missing", "cell_ids": [], "id": approval_id,
                       "records_detected": detected})
    return {"records": records, "unresolved": issues, "cells": cells, "sections": found["sections"],
            "full_review_id": approval_id, "records_detected": detected,
            "records_admitted": len(records), "grouped": grouped}


async def read_image_table_rosters(sources, *, fetch_asset, evidence_dir, ocr_engine=None, reviews_path=None):
    root = Path(evidence_dir)
    root.mkdir(parents=True, exist_ok=True)
    records, issues, evidence, observations = [], [], [], {}
    reviews = load_reviews(reviews_path) if reviews_path else {}
    for source in sources:
        originals, scans, resources, seen = [], [], [], set()
        for media_url in source["media_urls"]:
            original_path = None
            try:
                original = await fetch_asset(media_url)
                sha = hashlib.sha256(original).hexdigest()
                original_path = root / (digest([source["url"], media_url, sha])[:24] + ".bin")
                original_path.write_bytes(original)
                resource = {"media_url": media_url, "path": str(original_path.resolve()), "sha256": sha}
                resources.append(resource)
                if sha in seen:
                    continue
                signature = digest([POLICY, hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                                    version("rapidocr_onnxruntime")])
                cache_path = root.parent / "image_table_cache" / (digest([sha, signature]) + ".json")
                scanned = None
                if ocr_engine is None and cache_path.is_file():
                    try:
                        cached = json.loads(cache_path.read_text(encoding="utf-8"))
                        if (cached["source_sha256"] == sha and cached["signature"] == signature
                                and cached["sha256"] == digest(cached["scanned"])):
                            scanned = cached["scanned"]
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
                if scanned is None:
                    scanned = await asyncio.to_thread(scan_cells, original, ocr_engine=ocr_engine)
                    if ocr_engine is None:
                        cache_path.parent.mkdir(parents=True, exist_ok=True)
                        cache_path.write_text(json.dumps({"source_sha256": sha, "signature": signature,
                            "sha256": digest(scanned), "scanned": scanned}, ensure_ascii=False), encoding="utf-8")
                originals.append({"media_url": media_url, "path": str(original_path.resolve()), "sha256": sha})
                scans.append(scanned)
                seen.add(sha)
            except (OSError, ValueError, RuntimeError) as exc:
                issues.append({"url": source["url"], "media_url": media_url,
                               "status": "table_grid_ambiguous" if original_path else "table_asset_failed",
                               "error_type": type(exc).__name__,
                               **({"original_path": str(original_path.resolve()), "source_sha256": sha}
                                  if original_path else {})})
        if not scans:
            continue
        scanned = combine_scans(scans)
        stem = digest([source["url"], scanned["source_sha256"]])[:24]
        cells_path = root / (stem + ".json")
        cells_path.write_text(json.dumps(scanned, ensure_ascii=False, indent=2), encoding="utf-8")
        parsed = parse_cells(scanned, page_url=source["url"], media_url=originals[0]["media_url"], reviews=reviews)
        for record in parsed["records"]:
            record.source_kind = source["source_kind"]
            record.source_evidence = source.get('source_evidence')
            record.media_url = originals[record.pages[0] - 1]["media_url"]
        records.extend(parsed["records"])
        issues.extend(parsed["unresolved"])
        evidence.append({"url": source["url"], "media_url": originals[0]["media_url"],
                         "original_path": originals[0]["path"], "originals": originals,
                         "resources": resources,
                         "cells_path": str(cells_path.resolve()), "sha256": scanned["source_sha256"],
                         "cells_sha256": hashlib.sha256(cells_path.read_bytes()).hexdigest(),
                         "full_review_id": parsed["full_review_id"]})
        observations.setdefault(source["url"], []).append(
            {"records_detected": parsed["records_detected"], "records_admitted": parsed["records_admitted"],
             "images_captured": len(resources), "unique_originals": len(originals),
             "images_expected": len(source["media_urls"]),
             "sections": parsed["sections"], "complete": not parsed["unresolved"]
             and len(resources) == len(source["media_urls"])})
    return {"records": records, "unresolved": issues, "evidence": evidence, "observations": observations}
