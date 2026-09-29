"""Retired script-led entry points must leave college tasks for IDE Subagents."""

import pytest
from fastapi import HTTPException

from server.ide_dispatch_runner import check_and_execute_queue
from server.run_single_task import run_single_task
from server import app as app_module


@pytest.mark.asyncio
async def test_old_queue_runner_cannot_claim_college_tasks():
    with pytest.raises(RuntimeError, match="retired"):
        await check_and_execute_queue()


@pytest.mark.asyncio
async def test_old_single_task_runner_cannot_claim_college_tasks():
    with pytest.raises(RuntimeError, match="retired"):
        await run_single_task("sample-task")


@pytest.mark.asyncio
async def test_server_script_dispatch_mode_is_rejected():
    with pytest.raises(HTTPException, match="script dispatch is retired"):
        await app_module.set_mode("server")
    assert app_module.DISPATCH_MODE == "server_worker"
