"""Retired script-led dispatch stays unavailable (the old runners were removed)."""

import pytest
from fastapi import HTTPException

from server import app as app_module


@pytest.mark.asyncio
async def test_server_script_dispatch_mode_is_rejected():
    with pytest.raises(HTTPException, match="script dispatch is retired"):
        await app_module.set_mode("server")
    assert app_module.DISPATCH_MODE == "server_worker"


def test_retired_script_harvesters_are_gone():
    import importlib.util
    for name in ("server.autonomous_agent", "server.ide_dispatch_runner", "server.run_single_task"):
        assert importlib.util.find_spec(name) is None
