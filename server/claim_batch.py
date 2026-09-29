import urllib.request
import json

def claim_all():
    with open('pending_tasks.json', 'r', encoding='utf-8') as f:
        tasks = json.load(f)['tasks']
    claimed = []
    for t in tasks:
        task_id = t['task_id']
        url = f"http://127.0.0.1:8000/api/queue/claim/{task_id}"
        req = urllib.request.Request(
            url,
            data=json.dumps({"worker_id": "agent_host_swarm"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                claimed.append(res)
                print(f"Claimed: {t['school']} - {t['college']} (id: {task_id})")
        except Exception as e:
            print(f"Failed to claim {task_id}: {e}")
            
    with open('claimed_tasks.json', 'w', encoding='utf-8') as f:
        json.dump(claimed, f, ensure_ascii=False, indent=2)
    print(f"Total successfully claimed: {len(claimed)}")

if __name__ == "__main__":
    claim_all()
