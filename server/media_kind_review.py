"""Original-bound review of images whose lack of OCR text is ambiguous."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import time

DECISIONS_FILE = "media_kind_reviews.json"
STATUS = "image_bio_ocr_incomplete"


def evidence_id(profile_url: str, media_url: str, source_sha256: str) -> str:
    return hashlib.sha256(json.dumps([profile_url, media_url, source_sha256],
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def load_decisions(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {item["id"]: item for item in payload["decisions"]} if payload.get("schema") == 1 else {}
    except (OSError, ValueError, TypeError, KeyError):
        return {}


def photo_confirmed(path: Path | None, profile_url: str, media_url: str, source_sha256: str) -> bool:
    if path is None:
        return False
    identifier = evidence_id(profile_url, media_url, source_sha256)
    decision = load_decisions(path).get(identifier, {})
    return (decision.get("verdict") == "photo" and decision.get("profile_url") == profile_url
            and decision.get("media_url") == media_url and decision.get("source_sha256") == source_sha256
            and type(decision.get("reviewed_by")) is int and decision["reviewed_by"] > 0)


def collect_items(audit_path: Path) -> list[dict]:
    root = audit_path.resolve().parent
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    flagged = {(item.get("profile_url"), item.get("media_url")) for item in audit.get("unresolved", [])
               if item.get("status") == STATUS and item.get("kind") == "image"}
    items = {}
    saved = load_decisions(root / DECISIONS_FILE)
    for evidence in audit.get("image_biography_evidence", []):
        pair = evidence.get("profile_url"), evidence.get("media_url")
        if pair not in flagged or evidence.get("kind") != "image":
            continue
        path = Path(evidence["original_path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("media review original outside audit")
        original = path.read_bytes()
        digest = hashlib.sha256(original).hexdigest()
        if digest != evidence["sha256"]:
            raise ValueError("media review original changed")
        # Decode the entire original; previews must not hide text outside a crop.
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(original)) as image:
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("multi-frame media cannot be confirmed from one frame")
            preview = BytesIO()
            image.convert("RGB").save(preview, format="PNG")
        identifier = evidence_id(*pair, digest)
        items[identifier] = {"id": identifier, "profile_url": pair[0], "media_url": pair[1],
            "source_sha256": digest, "preview_bytes": preview.getvalue(),
            "saved_verdict": saved.get(identifier, {}).get("verdict", "")}
    if {(item["profile_url"], item["media_url"]) for item in items.values()} != flagged:
        raise ValueError("media review original missing")
    return list(items.values())


def save_decision(path: Path, item: dict, verdict: str, reviewed_by: int) -> None:
    if verdict not in {"photo", "unreadable"} or type(reviewed_by) is not int or reviewed_by <= 0:
        raise ValueError("invalid media kind decision")
    decision = {key: item[key] for key in ("id", "profile_url", "media_url", "source_sha256")}
    if decision["id"] != evidence_id(decision["profile_url"], decision["media_url"], decision["source_sha256"]):
        raise ValueError("invalid media evidence identity")
    decision.update(verdict=verdict, reviewed_by=reviewed_by, reviewed_at=time.time())
    decisions = load_decisions(path)
    decisions[item["id"]] = decision
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", dir=path.parent, suffix=".tmp", encoding="utf-8",
                                         delete=False) as output:
            temporary = Path(output.name)
            json.dump({"schema": 1, "decisions": list(decisions.values())}, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def review_complete(audit_path: Path, items: list[dict]) -> bool:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    flagged = [item for item in audit.get("unresolved", []) if item.get("status") == STATUS]
    if any(item.get("kind") != "image" for item in flagged):
        return False  # a PDF's empty pages cannot be classified as a portrait
    return len({(item["profile_url"], item["media_url"]) for item in items}) == len({
        (item.get("profile_url"), item.get("media_url")) for item in flagged}) and all(
        photo_confirmed(audit_path.parent / DECISIONS_FILE, item["profile_url"], item["media_url"],
                        item["source_sha256"]) for item in items)
