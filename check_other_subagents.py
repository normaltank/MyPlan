import os, json, glob, sys
sys.stdout.reconfigure(encoding='utf-8')
brain = r'C:\Users\27451\.gemini\antigravity\brain'
for d in ['7ac954b5-64a3-4e75-8c51-a921762da6d3', '68366d88-1671-4d69-a94c-bb0e76794b0f', '20b8d8d4-7aa5-4e8b-b9d8-d2941c72b084', '2a1ae8e3-c4a8-4390-94b4-86ff1d1e7b67']:
    tpath = os.path.join(brain, d, '.system_generated', 'logs', 'transcript.jsonl')
    if os.path.exists(tpath):
        lines = []
        with open(tpath, 'r', encoding='utf-8') as f:
            for line in f:
                if 'target' in line or 'school' in line or 'college' in line or 'http' in line:
                    lines.append(line)
        print(f"=== {d} (total matching lines: {len(lines)}) ===")
        for l in lines[-3:]:
            try:
                data = json.loads(l)
                print(" ", data.get('type'), str(data.get('content'))[:200])
            except:
                pass
