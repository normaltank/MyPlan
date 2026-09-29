"""Production runtime paths must not depend on scratch validation files."""

from pathlib import Path

import pytest

from server.agent_recon import ReconError, _config_from_env
from server.browser_batch_tool import _native_chrome_executable


def test_recon_config_requires_a_real_dsh_launcher(monkeypatch, tmp_path):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DSH_BIN", str(tmp_path / "missing-dsh"))
    monkeypatch.setenv("PATH", str(tmp_path))

    with pytest.raises(ReconError, match="DSH_BIN"):
        _config_from_env()


def test_native_chrome_uses_configured_executable(monkeypatch, tmp_path):
    chrome = tmp_path / "chrome"
    chrome.write_text("", encoding="utf-8")
    monkeypatch.setenv("CHROME_PATH", str(chrome))

    assert _native_chrome_executable() == chrome
