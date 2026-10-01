"""Isolated settings for the 2026-10-01 production-chain audit repros.

Synthetic pages only. The A1, A2 and A4 repros were removed once their fixes
(PR #10, PR #8) landed with regression tests in tests/. L1 records a known
limitation as is.
These files are not part of tests/ and are not collected by CI. Run explicitly:
    python -m pytest -q docs/audits/2026-10-01-production-chain-repro
"""
import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
os.environ.setdefault("HARVEST_DISABLE_NATIVE_CHROME_FALLBACK", "1")
os.environ.setdefault("HARVEST_CROSS_CHECK", "0")
_ISOLATED = Path(tempfile.mkdtemp(prefix="myplan-audit-"))
atexit.register(shutil.rmtree, _ISOLATED, ignore_errors=True)
os.environ["HARVEST_TASK_DB"] = str(_ISOLATED / "queue" / "tasks.db")
os.environ["FACULTY_RECON_EVIDENCE_ROOT"] = str(_ISOLATED / "recon-evidence")
