import asyncio
import json

import httpx
import pytest

from server.browser_batch_tool import run_with_lease


@pytest.mark.asyncio
async def test_lost_lease_blocks_sync_even_if_browser_capture_finished(tmp_path):
    audit_path = tmp_path / "audit.json"
    audit_path.write_text("{}", encoding="utf-8")
    requests = []

    def responder(request):
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(409)

    async def harvest():
        await asyncio.sleep(0.05)
        return {"status": "completed", "delivery_ready": True,
                "unresolved": [], "excel_path": str(tmp_path / "college.xlsx"),
                "audit_path": str(audit_path)}

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(responder), base_url="http://127.0.0.1:8000",
    ) as client:
        result = await run_with_lease(harvest, "task-1", "worker-1", "lease-1",
                                      client=client, interval_seconds=0.01)

    assert requests[0][0] == "/api/queue/renew/task-1"
    assert requests[0][1]["lease_token"] == "lease-1"
    assert result["delivery_ready"] is False
    assert result["excel_path"] is None
    assert result["unresolved"][0]["status"] == "lease_renewal_failed"
    assert json.loads(audit_path.read_text(encoding="utf-8"))["delivery_ready"] is False
