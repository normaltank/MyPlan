"""Original-bound review of non-biography photos versus profile text assets."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time

from server import review_store
from server.image_bio_review import OWNER_STATUS, LOW_CONFIDENCE_STATUS

DECISIONS_FILE = "media_kind_reviews.json"
STATUS = "image_bio_ocr_incomplete"
STATUSES = frozenset({STATUS, OWNER_STATUS, LOW_CONFIDENCE_STATUS})


def evidence_id(profile_url: str, media_url: str, source_sha256: str) -> str:
    return hashlib.sha256(json.dumps([profile_url, media_url, source_sha256],
        separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def load_decisions(path: Path) -> dict:
    return review_store.load(path)


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
               if item.get("status") in STATUSES and item.get("kind") == "image"}
    items = {}
    saved = load_decisions(root / DECISIONS_FILE)
    for evidence in audit.get("image_biography_evidence", []):
        pair = evidence.get("profile_url"), evidence.get("media_url")
        if pair not in flagged or evidence.get("kind") != "image":
            continue
        if not all(isinstance(url, str) and url.startswith(('http://', 'https://')) for url in pair):
            raise ValueError('media review source identity missing')
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
    review_store.put(path, decision)


def review_complete(audit_path: Path, items: list[dict]) -> bool:
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    flagged = [item for item in audit.get("unresolved", []) if item.get("status") in STATUSES
               and (item.get('kind') == 'image' or item.get('status') == STATUS)]
    if any(item.get("kind") != "image" for item in flagged):
        return False  # a PDF's empty pages cannot be classified as a portrait
    pairs = {(item['profile_url'], item['media_url']) for item in items}
    if pairs != {(item.get('profile_url'), item.get('media_url')) for item in flagged}:
        return False
    photos = approved_photos(audit_path, audit)
    remaining = [issue for issue in flagged if (issue.get('profile_url'), issue.get('media_url')) not in photos]
    if any(issue.get('status') == STATUS for issue in remaining):
        return False
    if not remaining:
        return True
    # A real biography may instead be settled by correcting its OCR headings.
    from server.image_bio_review import collect_review_items, text_review_complete
    return text_review_complete(audit_path, collect_review_items(audit_path))


def approved_photos(audit_path: Path, audit: dict) -> set[tuple[str, str]]:
    approved = set()
    root = audit_path.resolve().parent
    for evidence in audit.get('image_biography_evidence', []):
        profile, media, digest = (evidence.get(key) for key in ('profile_url', 'media_url', 'sha256'))
        if evidence.get('kind') != 'image' or not photo_confirmed(root / DECISIONS_FILE, profile, media, digest):
            continue
        original = Path(evidence['original_path']).resolve()
        if not original.is_relative_to(root) or hashlib.sha256(original.read_bytes()).hexdigest() != digest:
            raise ValueError('photo decision original changed or outside audit')
        approved.add((profile, media))
    return approved
