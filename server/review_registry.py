"""The college-level human review kinds, registered once.

Each kind keeps its own evidence, decision file and rules in its own module; this
table only says which audit issues it settles. The review page, the retry gate and
the resumed attempt all walk this table, so adding a kind is one entry here.
University-level review (university_identity_review) gates publishing, not a
college retry, and is deliberately not a kind.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Callable

from . import html_email_review, identity_review, image_bio_review, image_table_review, media_kind_review, name_review
from .image_table_roster import REVIEW_FILE as IMAGE_TABLE_REVIEW_FILE


@dataclass(frozen=True)
class ReviewKind:
    name: str
    statuses: frozenset[str]
    decision_files: tuple[str, ...]
    collect: Callable[[Path], list[dict]]
    # complete(audit_path, audit, items): every issue of this kind has a decision that still applies;
    # an issue without a reviewable item is never complete.
    complete: Callable[[Path, dict, list[dict]], bool]
    # An issue a person can settle on the review page (default: its status belongs to the kind).
    shown: Callable[[dict], bool] | None = None
    # The retry must wait for this kind (default: some issue is shown).
    triggered: Callable[[dict], bool] | None = None
    # Evidence predates the kind; only a deterministic rerun can produce it, so nothing to decide now.
    rerun_only: Callable[[dict], bool] = lambda audit: False

    def is_shown(self, issue: dict) -> bool:
        return self.shown(issue) if self.shown else issue.get("status") in self.statuses

    def is_triggered(self, audit: dict) -> bool:
        if self.triggered:
            return self.triggered(audit)
        return any(self.is_shown(issue) for issue in audit.get("unresolved", []))


def _has_status(statuses):
    return lambda audit: any(issue.get("status") in statuses for issue in audit.get("unresolved", []))


def _nonempty_and(check):
    return lambda path, audit, items: bool(items) and check(path, items)


KINDS: tuple[ReviewKind, ...] = (
    ReviewKind(
        "media_kind", frozenset({media_kind_review.STATUS}), (media_kind_review.DECISIONS_FILE,),
        media_kind_review.collect_items, lambda path, audit, items: media_kind_review.review_complete(path, items),
        shown=lambda issue: issue.get("status") == media_kind_review.STATUS and issue.get("kind") == "image",
        # A PDF's empty pages cannot be classified as a portrait, so they keep the retry blocked.
        triggered=_has_status({media_kind_review.STATUS})),
    ReviewKind(
        "image_bio_text", image_bio_review.TEXT_STATUSES, (image_bio_review.TEXT_DECISIONS_FILE,),
        image_bio_review.collect_review_items,
        lambda path, audit, items: image_bio_review.text_review_complete(path, items)),
    ReviewKind(
        "image_bio_email", frozenset({image_bio_review.EMAIL_STATUS}), (image_bio_review.EMAIL_DECISIONS_FILE,),
        image_bio_review.collect_email_conflict_items, image_bio_review.email_review_complete),
    ReviewKind(
        "name", frozenset({name_review.STATUS}), (name_review.DECISIONS_FILE,),
        name_review.collect_items, _nonempty_and(name_review.review_complete)),
    ReviewKind(
        "identity", frozenset({identity_review.STATUS}), (identity_review.DECISIONS_FILE,),
        identity_review.collect_items, _nonempty_and(identity_review.review_complete)),
    ReviewKind(
        "html_email", html_email_review.STATUSES, (html_email_review.DECISIONS_FILE,),
        html_email_review.collect_items, html_email_review.review_complete,
        # Only a teacher-bound conflict has a decision to make.
        triggered=html_email_review.has_pending, rerun_only=html_email_review.evidence_missing),
    ReviewKind(
        "image_table", image_table_review.STATUSES, (IMAGE_TABLE_REVIEW_FILE,),
        image_table_review.items,
        lambda path, audit, items: image_table_review.review_complete(path, items),
        triggered=lambda audit: bool(audit.get("image_table_evidence"))
        or _has_status(image_table_review.STATUSES)(audit)),
)

STATUSES = frozenset().union(*(kind.statuses for kind in KINDS))
DECISION_FILES = tuple(name for kind in KINDS for name in kind.decision_files)


def _audit(audit_path: Path) -> dict:
    return json.loads(Path(audit_path).read_text(encoding="utf-8"))


def has_review(audit_path: Path) -> bool:
    """Some unresolved issue can be settled on the review page."""
    return any(kind.is_shown(issue) for issue in _audit(audit_path).get("unresolved", []) for kind in KINDS)


BY_NAME = {kind.name: kind for kind in KINDS}


def collect(audit_path: Path, *names: str) -> dict[str, list[dict]]:
    """Review items of the named kinds (all kinds when none are named), keyed by kind name."""
    return {name: BY_NAME[name].collect(audit_path) for name in (names or BY_NAME)}


def review_complete(audit_path: Path) -> bool:
    """Some kind waits on people, and every such kind has decisions that still apply.

    Issues no kind owns do not hold the retry: the rerun's own audit decides them.
    """
    audit = _audit(audit_path)
    waiting = [kind for kind in KINDS if kind.is_triggered(audit)]
    if not waiting:
        return False
    for kind in waiting:
        if kind.rerun_only(audit):
            continue
        if not kind.complete(audit_path, audit, kind.collect(audit_path)):
            return False
    return True
