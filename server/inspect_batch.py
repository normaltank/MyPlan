import json
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')


with open("server/agent_queue.json", "r", encoding="utf-8") as f:
    data = json.load(f)

batch_tids = data["batch_tasks"]["4f0dee1a"]
print(f"Batch 4f0dee1a has {len(batch_tids)} tasks:")
for idx, tid in enumerate(batch_tids, 1):
    t = data["tasks"][tid]
    print(f"{idx:2d}. [{t['school']}] {t['college']} (Task ID: {tid})")
