"""Original-bound review of what a profile image is.

Two verdicts settle an image: "photo" (a portrait with no resume text, for images whose lack of OCR
text is ambiguous) and "irrelevant" (artwork, calligraphy, a signature or decoration: no information
about the teacher). Either one drops the image's text from that teacher; "unreadable" keeps it blocked.
The irrelevant verdict can also be given from an OCR line card of the same image (image_bio_review).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from server import review_store

DECISIONS_FILE = "media_kind_reviews.json"
STATUS = "image_bio_ocr_incomplete"
PHOTO, IRRELEVANT, UNREADABLE = "photo", "irrelevant", "unreadable"
SETTLED = frozenset({PHOTO, IRRELEVANT})


def evidence_id(profile_url: str, media_url: str, source_sha256: str) -> str:
    return hashlib.sha256(json.dumps([profile_url, media_url, source_sha256],
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def load_decisions(path: Path) -> dict:
    return review_store.load(path)


def confirmed_verdict(path: Path | None, profile_url: str, media_url: str, source_sha256: str) -> str | None:
    """The settling verdict saved for exactly this profile page, image URL and original bytes."""
    if path is None:
        return None
    identifier = evidence_id(profile_url, media_url, source_sha256)
    decision = load_decisions(path).get(identifier, {})
    valid = (decision.get("verdict") in SETTLED and decision.get("profile_url") == profile_url
            and decision.get("media_url") == media_url and decision.get("source_sha256") == source_sha256
            and type(decision.get("reviewed_by")) is int and decision["reviewed_by"] > 0)
    return decision["verdict"] if valid else None


def photo_confirmed(path: Path | None, profile_url: str, media_url: str, source_sha256: str) -> bool:
    return confirmed_verdict(path, profile_url, media_url, source_sha256) == PHOTO


def irrelevant_confirmed(path: Path | None, profile_url: str, media_url: str, source_sha256: str) -> bool:
    return confirmed_verdict(path, profile_url, media_url, source_sha256) == IRRELEVANT


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
    """item names one verified original: profile_url, media_url, source_sha256 (its id is recomputed)."""
    if verdict not in {PHOTO, IRRELEVANT, UNREADABLE} or type(reviewed_by) is not int or reviewed_by <= 0:
        raise ValueError("invalid media kind decision")
    if not all(isinstance(item.get(key), str) and item[key] for key in ("profile_url", "media_url", "source_sha256")):
        raise ValueError("invalid media evidence identity")
    decision = {key: item[key] for key in ("profile_url", "media_url", "source_sha256")}
    decision = {"id": evidence_id(*decision.values()), **decision}
    decision.update(verdict=verdict, reviewed_by=reviewed_by, reviewed_at=time.time())
    review_store.put(path, decision)


def review_complete(audit_path: Path, items: list[dict]) -> bool:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    flagged = [item for item in audit.get("unresolved", []) if item.get("status") == STATUS]
    if any(item.get("kind") != "image" for item in flagged):
        return False  # a PDF's empty pages cannot be classified as a portrait
    return len({(item["profile_url"], item["media_url"]) for item in items}) == len({
        (item.get("profile_url"), item.get("media_url")) for item in flagged}) and all(
        confirmed_verdict(audit_path.parent / DECISIONS_FILE, item["profile_url"], item["media_url"],
                          item["source_sha256"]) is not None for item in items)
