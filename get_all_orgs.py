import requests, json, sys
sys.stdout.reconfigure(encoding='utf-8')
headers = {'User-Agent': 'Mozilla/5.0'}
s = requests.Session()
s.get('https://www.gszy.edu.cn/reception', headers=headers, verify=False)
for org_id in [4, 10, 2, 3, 5, 6, 8, 9]:
    r = s.post('https://www.gszy.edu.cn/reception/org/list/ajaxOrgList', data={'orgTypeId': str(org_id), 'title': ''}, headers=headers, verify=False).json()
    print(f'=== orgTypeId {org_id} ===')
    for item in r.get('data', []):
        print(f"  {item.get('title')}: {item.get('url')}")
