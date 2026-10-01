"""Historical skill scripts cannot reintroduce HTTP harvesting or field rewriting."""
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPTS = Path(__file__).resolve().parents[1] / ".agents/skills/faculty-browser-harvest/scripts"


@pytest.mark.parametrize("filename", ["batch_browser_extractor.py", "harvest_template.py", "dom_dehydrator.py"])
@pytest.mark.parametrize("entry", ["import", "cli"])
def test_retired_script_refuses_import_and_command_line(filename, entry, tmp_path):
    path = SCRIPTS / filename
    command = ([sys.executable, "-c", "import runpy,sys; runpy.run_path(sys.argv[1])", str(path)]
               if entry == "import" else [sys.executable, str(path), "--help"])
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert "retired" in (result.stdout + result.stderr).lower()
    assert "server.browser_batch_tool" in result.stdout + result.stderr
    assert not list(tmp_path.iterdir())
