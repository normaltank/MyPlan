import os
import sys
import json
import time
import argparse
import asyncio
import httpx
import pandas as pd

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_SERVER_DIR)
for p in (_SERVER_DIR, _ROOT_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from autonomous_agent import AutonomousBrowserAgent
from agent_queue import AgentTask

BASE_API = "http://127.0.0.1:8000"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

async def run_single_task(task_id: str):
    raise RuntimeError(
        "Legacy script-only execution is retired. Claim this college task in the IDE "
        "and follow faculty-browser-harvest/SKILL.md."
    )
    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Fetch task details
        r = await client.get(f"{BASE_API}/api/task/{task_id}")
        if r.status_code != 200:
            print(f"Error fetching task {task_id}: {r.status_code}")
            return
        t_data = r.json()
        school = t_data["school"]
        college = t_data["college"]
        url = t_data.get("url")

        # 2. Claim task
        claim_r = await client.post(f"{BASE_API}/api/queue/claim/{task_id}")
        t_claimed = claim_r.json().get("task", {}) if claim_r.status_code == 200 else {}
        worker_id = t_claimed.get("worker_id", "Antigravity (IDE Agent)")
        lease_token = t_claimed.get("lease_token")
        print(f"[*] Claimed task {task_id}: {school} - {college} ({url or 'Auto search'})")

        local_task = AgentTask(
            task_id=task_id,
            school=school,
            college=college,
            url=url,
            mode=t_data.get("mode", "batch"),
            batch_id=t_data.get("batch_id")
        )

        # 3. Execute with AutonomousBrowserAgent
        agent = AutonomousBrowserAgent()
        try:
            await agent.execute_task(local_task, OUTPUT_DIR)
            print(f"[SUCCESS] {school} - {college}: Total={local_task.stats.get('total', 0)}, WithEmail={local_task.stats.get('with_email', 0)}")
            print(f"Excel File: {local_task.download_url}")

            # 4. Sync result
            sync_payload = {
                "records": local_task.records,
                "stats": local_task.stats,
                "download_url": local_task.download_url,
                "progress": 100,
                "worker_id": worker_id,
                "lease_token": lease_token
            }
            await client.post(f"{BASE_API}/api/task/{task_id}/sync", json=sync_payload)
            print(f"[*] Task {task_id} synced back to server successfully.")

        except Exception as e:
            print(f"[FAILED] Task {task_id} failed: {e}")
            sync_payload = {
                "records": [],
                "stats": {},
                "download_url": "",
                "error": str(e),
                "progress": 0,
                "worker_id": worker_id,
                "lease_token": lease_token
            }
            await client.post(f"{BASE_API}/api/task/{task_id}/sync", json=sync_payload)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True, help="Task ID to process")
    args = parser.parse_args()

    asyncio.run(run_single_task(args.task_id))
