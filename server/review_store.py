"""Reading and atomically writing review decision files: {"schema": 1, "decisions": [...]}.

Only file handling lives here; what a decision holds and when it still applies stay
with each review kind.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


def rows(path) -> list:
    """The saved decisions, or none when the file is missing, unreadable or another schema."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return list(payload["decisions"]) if payload.get("schema") == 1 else []
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return []


def load(path) -> dict:
    """Decisions by id; a malformed file counts as no decisions."""
    try:
        return {row["id"]: row for row in rows(path)}
    except (KeyError, TypeError):
        return {}


def put(path, decision: dict) -> None:
    """Replace the decision with the same id, keeping every other saved row."""
    path = Path(path).resolve()
    kept = [row for row in rows(path) if not (isinstance(row, dict) and row.get("id") == decision["id"])]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, suffix=".tmp",
                                         delete=False) as output:
            temporary = Path(output.name)
            json.dump({"schema": 1, "decisions": [*kept, decision]}, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
