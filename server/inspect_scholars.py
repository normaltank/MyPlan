import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

with open('server/saggas_dehydrated.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
end = int(sys.argv[2]) if len(sys.argv) > 2 else len(data)

for s in data[start:end]:
    print(f"[{s['index']:02d}] {s['name']} | {s['title']} | Email: {s['email']}")
    raw = s['raw_text']
    print(f"    Raw ({len(raw)} chars): {raw[:300]}...\n")
