"""Cell confidence, #12 table semantics, grouped names and exact review binding."""
from io import BytesIO

from PIL import Image, ImageDraw

from server.image_table_roster import POLICY, combine_scans, parse_cells, ruled_cells
from server.identity_review import digest


def scanned_rows(rows):
    cells = []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            bbox = [c * 100, r * 40, (c + 1) * 100, (r + 1) * 40]
            cell = {"row": r, "column": c, "row_span": 1, "column_span": 1, "bbox": bbox,
                    "raw_text": text, "raw_lines": [{"text": text, "confidence": .99}],
                    "recognition_min": .99, "geometry_score": 1}
            cell["id"] = digest(["fixture", bbox, text])
            cells.append(cell)
    return {"policy": POLICY, "source_sha256": "fixture-sha", "topology_sha256": "grid-sha",
            "size": [len(rows[0]) * 100, len(rows) * 40], "cells": cells,
            "grid_ambiguous": False, "unassigned": [], "whole_ocr": []}


def read(scanned, reviews=None):
    return parse_cells(scanned, page_url="https://college.edu/roster", media_url="https://college.edu/table.png",
                       reviews=reviews)


def confirm(result, scanned, reviews=None):
    reviews = dict(reviews or {})
    reviews[result["full_review_id"]] = {"id": result["full_review_id"], "kind": "full_image",
        "source_sha256": scanned["source_sha256"], "policy": POLICY,
        "expected_count": result["records_detected"], "reviewed_by": 1}
    return reviews


def test_column_reader_keeps_every_section_titles_and_empty_biography():
    scanned = scanned_rows([["序号", "姓名", "职称"], ["1", "张伟", "教授"],
                            ["2", "李明", "高级实验师"]])
    result = read(scanned)
    assert [record.name for record in result["records"]] == ["张伟", "李明"]
    assert result["records"][1].title == "高级实验师"
    assert all(record.segment.text == "" for record in result["records"])
    assert {u["status"] for u in result["unresolved"]} == {"table_full_image_review_missing"}
    approved = read(scanned, confirm(result, scanned))
    assert not approved["unresolved"]
    assert all(record.provenance()["table_row"]["cell_ids"] for record in approved["records"])
    scanned["cells"][6]["raw_text"] = "3"
    assert any(u["status"] == "table_index_gap" for u in read(scanned)["unresolved"])


def test_grouped_names_do_not_guess_merged_ocr_and_human_changes_revoke_full_approval():
    scanned = scanned_rows([["职称/学位", "姓名"], ["副教授/博士", "张伟李明"]])
    result = read(scanned)
    name_cell = scanned["cells"][-1]
    reviews = {name_cell["id"]: {"id": name_cell["id"], "source_sha256": scanned["source_sha256"],
               "policy": POLICY, "reviewed_by": 1, "verified_text": "张伟\n李明"}}
    result = read(scanned, reviews)
    assert [record.name for record in result["records"]] == ["张伟", "李明"]
    assert all(record.title == "副教授" for record in result["records"])
    assert result["sections"][0]["heading"] == "副教授/博士"
    full = confirm(result, scanned, reviews)
    assert not read(scanned, full)["unresolved"]
    full[name_cell["id"]]["verified_text"] = "张伟\n王明"
    assert any(u["status"] == "table_full_image_review_missing" for u in read(scanned, full)["unresolved"])
    # A source hash mismatch cannot make corrected teacher text effective.
    full[name_cell["id"]]["source_sha256"] = "other"
    assert "王明" not in [r.name for r in read(scanned, full)["records"]]


def test_missing_cell_text_and_bad_geometry_are_not_empty_success():
    scanned = scanned_rows([["姓名", "职称"], ["张伟", "教授"]])
    scanned["cells"][-1].update(raw_text="", recognition_min=None)
    scanned["grid_ambiguous"] = True
    issues = {u["status"] for u in read(scanned)["unresolved"]}
    assert {"table_grid_ambiguous", "table_cell_unreadable", "table_full_image_review_missing"} <= issues


def test_group_degree_is_not_an_academic_title():
    scanned = scanned_rows([["职称/学位", "姓名"], ["博士", "张伟"]])
    result = read(scanned)
    assert result["records"][0].title == "教师"
    assert result["records"][0].table_row["section"] == "博士"


def test_ruled_grid_records_cells_and_detects_broken_topology():
    image = Image.new("RGB", (400, 220), "white")
    draw = ImageDraw.Draw(image)
    for y in (10, 70, 130, 210):
        draw.line((10, y, 390, y), fill="black", width=2)
    for x in (10, 150, 390):
        draw.line((x, 10, x, 210), fill="black", width=2)
    out = BytesIO()
    image.save(out, format="PNG")
    cells, size, ambiguous = ruled_cells(out.getvalue())
    assert size == [400, 220] and len(cells) == 6 and not ambiguous


def test_images_continue_column_headers_and_section_index_across_pages():
    first = scanned_rows([["序号", "姓名", "职称"], ["1", "张伟", "教授"]])
    second = scanned_rows([["2", "李明", "高级实验师"], ["3", "王强", "讲师"]])
    for page, scan in enumerate((first, second)):
        scan["source_sha256"] += str(page)
        for cell in scan["cells"]:
            cell["source_sha256"] = scan["source_sha256"]
    combined = combine_scans([first, second])
    result = read(combined)
    assert [record.name for record in result["records"]] == ["张伟", "李明", "王强"]
    assert result["records"][1].pages == [2]
    assert result["records"][1].sha256 == second["source_sha256"]
    assert all(record.segment.text == "" for record in result["records"])
    assert not any(u["status"] == "table_index_gap" for u in result["unresolved"])


def test_full_width_section_heading_is_evidence_and_never_excludes_rows():
    scanned = scanned_rows([["序号", "姓名", "职称"], ["1", "张伟", "教授"],
                            ["二、实验技术人员", "", ""], ["序号", "姓名", "职称"],
                            ["1", "李明", "高级实验师"]])
    scanned["cells"] = [c for c in scanned["cells"] if c["row"] != 2 or c["column"] == 0]
    heading = next(c for c in scanned["cells"] if c["row"] == 2)
    heading.update(column_span=3, bbox=[0, 80, 300, 120])
    result = read(scanned)
    assert [r.name for r in result["records"]] == ["张伟", "李明"]
    assert result["records"][1].table_row["section"] == "二、实验技术人员"
