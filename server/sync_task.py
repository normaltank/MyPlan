import json
import os
import sys
import httpx

sys.stdout.reconfigure(encoding='utf-8')

import build_dataset as b
records = b.unique_teachers

total = len(records)
with_email = sum(1 for r in records if (r.get("邮箱") or "").strip())
no_email = total - with_email

stats = {
    "total": total,
    "with_email": with_email,
    "no_email": no_email
}

# Fetch current task lease info
task_id = "5f79671d43da4be1894c6727341df296"
r = httpx.get(f"http://127.0.0.1:8000/api/task/{task_id}")
task_data = r.json()
worker_id = task_data.get("worker_id")
lease_token = task_data.get("lease_token")

payload = {
    "records": records,
    "stats": stats,
    "download_url": "/download/甘肃中医药大学_护理学院.xlsx",
    "error": None,
    "progress": 100,
    "worker_id": worker_id,
    "lease_token": lease_token
}

sync_res = httpx.post(
    f"http://127.0.0.1:8000/api/task/{task_id}/sync",
    json=payload,
    timeout=30.0
)

print(f"Sync status: {sync_res.status_code}")
print("Response text:", sync_res.text[:300])
