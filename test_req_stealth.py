import requests, urllib3
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

urllib3.disable_warnings()

with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=['--disable-blink-features=AutomationControlled'])
    context = b.new_context()
    page = context.new_page()
    stealth_sync(page)
    page.goto('https://jcyxy.gszy.edu.cn/')
    page.wait_for_timeout(4000)
    
    cookies = {c['name']: c['value'] for c in context.cookies()}
    ua = page.evaluate('navigator.userAgent')
    b.close()

print('Cookies captured:', cookies.keys())
headers = {
    'User-Agent': ua,
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    'Connection': 'keep-alive'
}

r = requests.get('https://jcyxy.gszy.edu.cn/', cookies=cookies, headers=headers, verify=False)
print('Requests status:', r.status_code)
print('Response text len:', len(r.text))
print('Response snippet:\n', r.text[:500])
