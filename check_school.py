import sys
import requests
from bs4 import BeautifulSoup
import urllib3
urllib3.disable_warnings()
sys.stdout.reconfigure(encoding='utf-8')

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
r = requests.get('https://www.gszy.edu.cn/', headers=headers, verify=False)
soup = BeautifulSoup(r.text, 'html.parser')

for a in soup.find_all('a'):
    text = a.get_text().strip()
    href = a.get('href', '')
    if text and ('机构' in text or '教学' in text or '院' in text or '系' in text or '部门' in text):
        print(f"{text}: {href}")
