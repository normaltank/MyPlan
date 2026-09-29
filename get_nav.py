import requests, json, sys
sys.stdout.reconfigure(encoding='utf-8')
headers = {'User-Agent': 'Mozilla/5.0'}
s = requests.Session()
s.get('https://www.gszy.edu.cn/reception', headers=headers, verify=False)
nav = s.post('https://www.gszy.edu.cn/reception/ajaxGetNavigator', headers=headers, verify=False).json()
for item in nav:
    print('==', item.get('title'), '==')
    for sub in item.get('subList', []):
        print(f"  {sub.get('title')}: {sub.get('url')}")
