import requests
import urllib3
import re
import sys

sys.stdout.reconfigure(encoding='utf-8')
urllib3.disable_warnings()

r = requests.get('https://www.gszy.edu.cn/', verify=False)
links = set(re.findall(r'href=[\'"]([^\'"]+)[\'"]', r.text))
for link in sorted(links):
    if any(k in link for k in ['search', 'find', 'query', 'list', 'detail', 'org', 'school', 'special']):
        print(link)
