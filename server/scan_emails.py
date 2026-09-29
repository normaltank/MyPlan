import json
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')

with open(r'C:\Users\27451\.gemini\antigravity\brain\5f1bc082-23da-425e-a997-78c28d986d3d\.system_generated\steps\156\output.txt', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line.startswith('{"totalFetched":'):
            data = json.loads(line)
            break

items = data['items']
print(f"Total fetched: {len(items)}")

# Check emails
email_records = []
for it in items:
    content = it['content']
    matches = re.findall(r'[\w.+-]+@[\w-]+\.[\w.-]+', content)
    anti_spam = re.findall(r'[\w.+-]+(?:\s*\(at\)\s*|\s*\[at\]\s*|\s*#\s*)[\w-]+\.[\w.-]+', content)
    all_emails = set(matches + [a.replace('(at)', '@').replace('[at]', '@').replace('#', '@').replace(' ', '') for a in anti_spam])
    
    real_emails = [e for e in all_emails if not e.endswith('.png') and not e.endswith('.jpg') and not e.endswith('.js')]
    if real_emails:
        email_records.append((it['id'], it['name'], real_emails))
        print(f"Found email: id={it['id']}, name={it['name']}, email={real_emails}")
    else:
        for k in ['邮箱', 'Email', 'E-mail', 'mail']:
            if k in content:
                idx = content.find(k)
                snip = content[max(0, idx-10):min(len(content), idx+40)].replace('\n', ' ')
                print(f"  Note: id={it['id']} name={it['name']} has keyword '{k}': {snip}")

print(f"\nTotal items with email: {len(email_records)}")
