import json
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

with open('tcos_raw_teachers.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

with open('all_teachers_raw.txt', 'w', encoding='utf-8') as out:
    for i, d in enumerate(data):
        out.write("="*60 + "\n")
        out.write(f"INDEX {i+1}: {d['name']} | Category: {d['category']} | URL: {d['url']}\n")
        out.write("="*60 + "\n")
        out.write(d['raw_text'].strip() + "\n\n")
