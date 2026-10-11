"""Test-suite isolation.

The browser batch tool falls back to launching a real Chrome over CDP when a
page cannot be fetched through the supplied browser context. Fixture-backed
tests have no live pages, so that fallback used to open visible Chrome windows
and issue real requests to production sites (e.g. the 甘肃 hospital fixture
hitting www.lzsdyyy.cn doctor pages). Tests must stay hermetic and offline.

The suite also runs on the machine that hosts the live queue. Components that
fall back to the default queue (Web app import, worker self-check, extractor
wrapper cache) and MCP subprocesses that write browser evidence are pointed at
a throw-away directory here, before any server module is imported. The values
are assigned, not defaulted: an operator shell that exports the production
HARVEST_TASK_DB must not leak it into the suite.
"""
import atexit
import os
import shutil
import tempfile
from pathlib import Path
import pytest

os.environ.setdefault("HARVEST_DISABLE_NATIVE_CHROME_FALLBACK", "1")
# The post-harvest navigation census opens the college's live menu pages;
# tests that need it enable it explicitly with a fake census.
os.environ.setdefault("HARVEST_CROSS_CHECK", "0")

_ISOLATED = Path(tempfile.mkdtemp(prefix="myplan-tests-"))
atexit.register(shutil.rmtree, _ISOLATED, ignore_errors=True)
os.environ["HARVEST_TASK_DB"] = str(_ISOLATED / "queue" / "tasks.db")
os.environ["FACULTY_RECON_EVIDENCE_ROOT"] = str(_ISOLATED / "recon-evidence")
# The shared site clock (freezes recorded after a rate-limit notice) lives here,
# and no operator-configured pace leaks into fixture runs.
os.environ["HARVEST_SITE_CLOCK_DB"] = str(_ISOLATED / "site-clock.sqlite3")
os.environ["HARVEST_PER_SITE_INTERVAL_SECONDS"] = "0"


@pytest.fixture(autouse=True)
def stipulated_source_boundary(request, monkeypatch):
    if request.node.get_closest_marker('verified_source_boundary') is None:
        return
    from source_contract_fixture import install_component_source_boundary, upgrade
    for name in ('PLAN', 'BRANCH'):
        if hasattr(request.module, name):
            upgrade(getattr(request.module, name))
    install_component_source_boundary(monkeypatch)
