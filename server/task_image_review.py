"""Find image review evidence belonging to one queued college task."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import shutil
import time

from . import review_registry
from .image_bio_review import (EMAIL_DECISIONS_FILE, TEXT_DECISIONS_FILE, collect_email_conflict_items,
                               collect_review_items)


def task_review_audit(output_dir: str | Path, task) -> Path | None:
    root = (Path(output_dir) / 'tasks' / task.task_id).resolve()
    if not root.is_dir() or task.task_type != 'harvest':
        return None
    candidates = []
    for path in root.glob('attempt-*/*_audit.json'):
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        try:
            number = int(path.parent.name.removeprefix('attempt-'))
            audit = json.loads(resolved.read_text(encoding='utf-8'))
            if (number < 1 or audit.get('school') != task.school
                    or audit.get('college') != task.college
                    or Path(audit.get('audit_path', '')).resolve() != resolved):
                continue
        except (ValueError, OSError, TypeError):
            continue
        candidates.append((resolved.stat().st_mtime_ns, number, resolved))
    if not candidates:
        return None
    return max(candidates)[2]


def review_items(audit_path: Path) -> tuple[list[dict], list[dict]]:
    return collect_review_items(audit_path), collect_email_conflict_items(audit_path)


def prepare_review_resume(audit_path: Path, task_id: str) -> None:
    root = audit_path.parent.parent.resolve()
    if root.name != task_id or not audit_path.resolve().is_relative_to(root):
        raise ValueError('review audit must belong to the task')
    marker = root / 'review-resume.json'
    staging = root / 'review-resume.tmp'
    staging.write_text(json.dumps({'task_id': task_id, 'audit_path': str(audit_path.resolve()),
                                   'prepared_at': time.time()}), encoding='utf-8')
    staging.replace(marker)


def carry_review_decisions(task_root: str | Path, target_attempt: str | Path) -> None:
    """Place exact-evidence decisions where the resumed deterministic harvest reads them."""
    root = Path(task_root).resolve()
    target = Path(target_attempt).resolve()
    if not target.is_relative_to(root):
        raise ValueError('attempt outside task')
    try:
        marker = json.loads((root / 'review-resume.json').read_text(encoding='utf-8'))
        source_audit = Path(marker['audit_path']).resolve()
        if marker['task_id'] != root.name or not source_audit.is_relative_to(root):
            raise ValueError('review resume belongs to another task')
        if not source_audit.is_file():
            raise ValueError('review audit missing')
    except FileNotFoundError:
        return
    source = source_audit.parent
    target.mkdir(parents=True, exist_ok=True)
    for filename in review_registry.DECISION_FILES:
        path = source / filename
        if path.is_file() and path.resolve().is_relative_to(root) and path.resolve() != (target / filename).resolve():
            shutil.copyfile(path, target / filename)


def legacy_review_audits(scratch_root: str | Path) -> list[tuple[str, Path, dict]]:
    """Register local historical review files without accepting browser paths."""
    root = Path(scratch_root).resolve()
    if not root.is_dir():
        return []
    result = []
    for path in root.rglob('*_audit.json'):
        resolved = path.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            continue
        try:
            audit = json.loads(resolved.read_text(encoding='utf-8'))
            saved_review = False
            for filename in (TEXT_DECISIONS_FILE, EMAIL_DECISIONS_FILE):
                decision_path = resolved.parent / filename
                if decision_path.is_file():
                    decisions = json.loads(decision_path.read_text(encoding='utf-8'))
                    saved_review |= decisions.get('schema') == 1 and bool(decisions.get('decisions'))
            if (Path(audit.get('audit_path', '')).resolve() != resolved
                    or not (review_registry.has_review(resolved) or saved_review)):
                continue
        except (OSError, ValueError, TypeError, AttributeError):
            continue
        key = sha256(str(resolved).encode('utf-8')).hexdigest()[:24]
        result.append((key, resolved, audit))
    return sorted(result, key=lambda row: row[1].stat().st_mtime, reverse=True)
