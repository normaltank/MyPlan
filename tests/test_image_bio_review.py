"""Human OCR decisions remain bound to the exact original and OCR line."""

import json
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from PIL import Image

from server.image_bio_review import (
    apply_review_decisions, collect_review_items, render_review_page,
    save_review_decision, make_review_server, collect_email_conflict_items,
    save_email_decision, resolve_email_conflict,
)


def _audit_fixture(tmp_path: Path) -> tuple[Path, dict]:
    original = tmp_path / "original.png"
    image = Image.new("RGB", (200, 100), "white")
    image.save(original)
    digest = sha256(original.read_bytes()).hexdigest()
    ocr = tmp_path / "original_ocr.json"
    scanned = {"kind": "image", "page_count": 1, "text": "完整导师履历\n待核文字",
               "pages": [{"page": 1, "text": "完整导师履历\n待核文字",
                          "lines": [
                              {"text": "完整导师履历", "confidence": .98,
                               "box": [[10, 5], [150, 5], [150, 20], [10, 20]]},
                              {"text": "待核文字", "confidence": .55,
                               "box": [[10, 40], [150, 40], [150, 60], [10, 60]]}],
                          "source": "local_ocr"}]}
    ocr.write_text(json.dumps(scanned, ensure_ascii=False), encoding="utf-8")
    media_url = "https://faculty.example.edu/cv.png"
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({
        "image_biography_evidence": [{"media_url": media_url, "kind": "image",
                                      "sha256": digest, "original_path": str(original),
                                      "ocr_path": str(ocr)}],
        "unresolved": [{"media_url": media_url, "status": "image_bio_low_confidence",
                        "ocr_path": str(ocr)}],
    }, ensure_ascii=False), encoding="utf-8")
    return audit, scanned


def test_review_page_shows_only_low_confidence_line_and_its_original_crop(tmp_path):
    audit, _ = _audit_fixture(tmp_path)
    items = collect_review_items(audit)
    assert len(items) == 1
    assert items[0]["ocr_text"] == "待核文字"
    assert items[0]["page"] == 1 and items[0]["line_index"] == 1
    crop = Image.open(BytesIO(items[0]["crop_bytes"]))
    assert crop.width < 200 and crop.height < 100
    html = render_review_page(items)
    assert "待核文字" in html and "完整导师履历" not in html
    assert "data:image/png;base64," in html


def test_review_decision_applies_only_to_identical_original_and_ocr_line(tmp_path):
    audit, scanned = _audit_fixture(tmp_path)
    item = collect_review_items(audit)[0]
    decisions = tmp_path / "image_bio_reviews.json"
    save_review_decision(decisions, item, "人工核对文字")

    adjusted, reviewed = apply_review_decisions(
        scanned, item["media_url"], item["source_sha256"], decisions)
    assert adjusted["pages"][0]["lines"][1]["text"] == "人工核对文字"
    assert "人工核对文字" in adjusted["text"]
    assert reviewed == {(1, 1)}
    assert scanned["pages"][0]["lines"][1]["text"] == "待核文字"

    changed_original, checked = apply_review_decisions(
        scanned, item["media_url"], "0" * 64, decisions)
    assert checked == set() and changed_original == scanned
    changed_line = json.loads(json.dumps(scanned, ensure_ascii=False))
    changed_line["pages"][0]["lines"][1]["text"] = "新的 OCR 文字"
    _, checked = apply_review_decisions(
        changed_line, item["media_url"], item["source_sha256"], decisions)
    assert checked == set()


def test_legacy_low_confidence_ocr_is_rescanned_for_crop_without_rewriting_raw_evidence(tmp_path, monkeypatch):
    audit, _ = _audit_fixture(tmp_path)
    payload = json.loads(audit.read_text(encoding="utf-8"))
    ocr_path = Path(payload["image_biography_evidence"][0]["ocr_path"])
    old = json.loads(ocr_path.read_text(encoding="utf-8"))
    del old["pages"][0]["lines"][1]["box"]
    ocr_path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    scanned = json.loads(json.dumps(old, ensure_ascii=False))
    scanned["pages"][0]["lines"][1]["box"] = [[10, 40], [150, 40], [150, 60], [10, 60]]
    calls = []

    def rescan(data, kind):
        calls.append((len(data), kind))
        return scanned

    monkeypatch.setattr("server.image_biography.scan_media", rescan)
    items = collect_review_items(audit)
    assert len(items) == 1 and calls == [(len(Path(payload["image_biography_evidence"][0]
                                                    ["original_path"]).read_bytes()), "image")]
    assert "box" not in json.loads(ocr_path.read_text(encoding="utf-8"))["pages"][0]["lines"][1]


def test_loopback_review_server_saves_confirmed_decision(tmp_path):
    import threading
    import urllib.error
    import urllib.request

    audit, _ = _audit_fixture(tmp_path)
    server = make_review_server(audit, port=0)
    assert server.server_address[0] == "127.0.0.1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/") as response:
            assert response.status == 200 and "待核文字" in response.read().decode("utf-8")
        foreign_host = urllib.request.Request(base + "/", headers={"Host": "other.example"})
        try:
            urllib.request.urlopen(foreign_host)
            assert False, "a foreign host must not read the review page"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        item = collect_review_items(audit)[0]
        request = urllib.request.Request(
            base + "/decision", method="POST",
            data=json.dumps({"id": item["id"], "verified_text": "待核文字"},
                            ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 204
        cross_site = urllib.request.Request(
            base + "/decision", method="POST", data=request.data,
            headers={"Content-Type": "application/json", "Origin": "https://other.example"})
        try:
            urllib.request.urlopen(cross_site)
            assert False, "a foreign page must not be able to confirm OCR"
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        decisions = json.loads((tmp_path / "image_bio_reviews.json").read_text(encoding="utf-8"))
        assert decisions["decisions"][0]["source_sha256"] == item["source_sha256"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_email_choice_is_bound_to_original_and_reviewed_ocr(tmp_path):
    audit_path, scanned = _audit_fixture(tmp_path)
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    evidence = payload["image_biography_evidence"][0]
    evidence["assigned_name"] = "张富有"
    scanned["pages"][0]["lines"] = [
        {"text": "张富有 教授", "confidence": .98,
         "box": [[10, 5], [150, 5], [150, 20], [10, 20]]},
        {"text": "邮箱 123456@qq.com", "confidence": .98,
         "box": [[10, 40], [150, 40], [150, 55], [10, 55]]},
        {"text": "邮箱 234567@163.com", "confidence": .98,
         "box": [[10, 60], [150, 60], [150, 75], [10, 75]]},
    ]
    scanned["pages"][0]["text"] = scanned["text"] = "\n".join(
        line["text"] for line in scanned["pages"][0]["lines"])
    Path(evidence["ocr_path"]).write_text(json.dumps(scanned, ensure_ascii=False), encoding="utf-8")
    payload["unresolved"] = [{"url": "https://faculty.example.edu/profile",
                               "name": "张富有", "status": "image_bio_email_conflict"}]
    payload["image_biography_evidence"][0]["profile_url"] = "https://faculty.example.edu/profile"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "faculty_records.json").write_text(json.dumps([{
        "主页": "https://faculty.example.edu/profile", "姓名": "张富有", "邮箱": ""}],
        ensure_ascii=False), encoding="utf-8")
    payload["records_path"] = str(tmp_path / "faculty_records.json")
    audit_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    items = collect_email_conflict_items(audit_path)
    assert len(items) == 1
    item = items[0]
    assert item["choices"] == ["123456@qq.com", "234567@163.com"]
    assert len(item["crops"]) == 2
    store = tmp_path / "image_bio_email_reviews.json"
    save_email_decision(store, item, "234567@163.com")
    assert resolve_email_conflict(store, item["id"], item["choices"]) == "234567@163.com"
    assert resolve_email_conflict(store, item["id"], ["123456@qq.com"]) is None
    assert resolve_email_conflict(store, "different-evidence", item["choices"]) is None

    import threading
    import urllib.request
    server = make_review_server(audit_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with urllib.request.urlopen(base + "/") as response:
            page = response.read().decode("utf-8")
            assert "邮箱归属复核" in page and "234567@163.com" in page
        request = urllib.request.Request(
            base + "/email-decision", method="POST",
            data=json.dumps({"id": item["id"], "selected_email": "123456@qq.com"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            assert response.status == 204
        assert resolve_email_conflict(store, item["id"], item["choices"]) == "123456@qq.com"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
