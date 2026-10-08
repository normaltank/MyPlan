"""Exact-evidence human edits for table cells, plus independent whole-image counts."""
import base64
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import time

from PIL import Image

from server.image_table_roster import POLICY, REVIEW_FILE, load_reviews, parse_cells


def items(audit_path):
    audit_path = Path(audit_path).resolve()
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    reviews = load_reviews(audit_path.parent / REVIEW_FILE)
    found = []
    for evidence in audit.get("image_table_evidence", []):
        cells_path = Path(evidence["cells_path"]).resolve()
        if not cells_path.is_relative_to(audit_path.parent) or not cells_path.is_file():
            raise ValueError("table evidence outside attempt")
        sources = evidence.get("originals") or [{"path": evidence["original_path"], "sha256": evidence["sha256"]}]
        originals = []
        for source in sources:
            original_path = Path(source["path"]).resolve()
            if not original_path.is_relative_to(audit_path.parent) or not original_path.is_file():
                raise ValueError("table original outside attempt")
            original = original_path.read_bytes()
            if sha256(original).hexdigest() != source["sha256"]:
                raise ValueError("table original changed")
            originals.append(original)
        raw = cells_path.read_bytes()
        if sha256(raw).hexdigest() != evidence["cells_sha256"]:
            raise ValueError("table OCR changed")
        scanned = json.loads(raw)
        if scanned["source_sha256"] != evidence["sha256"] or scanned["policy"] != POLICY:
            raise ValueError("table policy/source changed")
        parsed = parse_cells(scanned, page_url=evidence["url"], media_url=evidence["media_url"], reviews=reviews)
        found.append({"evidence": evidence, "originals": originals, "scanned": scanned, "parsed": parsed})
    return found


def review_complete(audit_path):
    tables = items(audit_path)
    return bool(tables) and all(not table["parsed"]["unresolved"] for table in tables)


def save(audit_path, identifier, *, verified_text=None, expected_count=None, reviewed_by):
    if type(reviewed_by) is not int or reviewed_by <= 0:
        raise ValueError("authenticated human reviewer required")
    candidate = None
    for table in items(audit_path):
        parsed, scanned = table["parsed"], table["scanned"]
        if expected_count is not None and identifier == parsed["full_review_id"]:
            if type(expected_count) is not int or expected_count < 1 or expected_count != parsed["records_detected"]:
                raise ValueError("count must match all detected people")
            candidate = {"id": identifier, "kind": "full_image", "expected_count": expected_count,
                         "source_sha256": scanned["source_sha256"], "policy": POLICY}
        elif verified_text is not None:
            cell = next((c for c in scanned["cells"] if c["id"] == identifier), None)
            if cell is not None and isinstance(verified_text, str):
                candidate = {"id": identifier, "kind": "cell", "verified_text": verified_text,
                             "source_sha256": cell.get("source_sha256", scanned["source_sha256"]), "policy": POLICY}
    if candidate is None:
        raise ValueError("review item changed or does not exist")
    path = Path(audit_path).parent / REVIEW_FILE
    decisions = load_reviews(path)
    if identifier in decisions and all(decisions[identifier].get(k) == v for k, v in candidate.items()):
        return
    decisions[identifier] = {**candidate, "reviewed_by": reviewed_by, "reviewed_at": time.time()}
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump({"schema": 1, "decisions": list(decisions.values())}, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def render(audit_path, task_id, csrf):
    sections = []
    for table in items(audit_path):
        images = [Image.open(BytesIO(original)) for original in table["originals"]]
        full_images = "".join('<img class="original" alt="完整原图" src="data:'
                              + ("image/png" if image.format == "PNG" else "image/jpeg") + ";base64,"
                              + base64.b64encode(original).decode() + '">'
                              for image, original in zip(images, table["originals"]))
        cells = []
        for cell in table["parsed"]["cells"]:
            bbox = cell["bbox"]
            crop = images[cell.get("page", 1) - 1].crop(tuple(bbox))
            buffer = BytesIO()
            crop.save(buffer, format="PNG")
            preview = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
            cells.append('<article data-cell="' + cell["id"] + '"><h3>格 ' + str(cell["row"] + 1)
                         + ' / ' + str(cell["column"] + 1) + ' · ' + escape(cell["state"]) + '</h3>'
                         + '<img src="' + preview + '" alt="单元格原图"><p>识别最低分：'
                         + escape(str(cell["recognition_min"])) + '；几何支持度：'
                         + escape(str(cell["geometry_score"])) + '</p><textarea>'
                         + escape(cell["effective_text"]) + '</textarea><button class="save-cell">保存人工核对文字</button></article>')
        sections.append('<section>' + full_images + '<p>'
                        '请核对整图人员与格子一一对应；多人格每行填一人，空白格确认后保存空文本。</p>'
                        + '<div class="cells">' + "".join(cells) + '</div>'
                        + '<label>完整原图可见人员数 <input type="number" min="1" class="count"></label>'
                        + '<button class="full" data-id="' + table["parsed"]["full_review_id"]
                        + '">确认完整原图人数</button></section>')
    data = json.dumps({"url": f"/api/task/{task_id}/review/table", "csrf": csrf}).replace("<", "\\u003c")
    template = Path(__file__).with_name("templates") / "image_table_review.html"
    return template.read_text(encoding="utf-8").replace("<!--TABLES-->", "".join(sections)).replace(
        "/*DATA*/", data).replace("<!--TASK-->", escape(task_id))
