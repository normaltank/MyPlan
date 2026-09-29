import json
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

with open(r'C:\Users\27451\.gemini\antigravity\brain\5f1bc082-23da-425e-a997-78c28d986d3d\.system_generated\steps\156\output.txt', 'r', encoding='utf-8') as f:
    lines = f.readlines()

json_str = ""
for line in lines:
    if line.startswith('{"totalFetched":'):
        json_str = line.strip()
        break

if not json_str:
    for line in lines:
        if '{"totalFetched":' in line:
            idx = line.find('{"totalFetched":')
            json_str = line[idx:].strip()
            break

data = json.loads(json_str)
items = data['items']
print(f"Total items: {len(items)}")

from collections import defaultdict
by_name = defaultdict(list)
for it in items:
    name = it['name'].replace(' ', '')
    by_name[name].append(it)

print(f"Unique names: {len(by_name)}")
for name, list_it in sorted(by_name.items()):
    best = max(list_it, key=lambda x: x['contentLen'])
    b_len = best['contentLen']
    b_title = best['title']
    b_email = best['email']
    b_url = best['url']
    print(f"{name}: count={len(list_it)}, best_len={b_len}, title={b_title}, email={b_email}, url={b_url}")
