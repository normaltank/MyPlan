import requests, urllib3
from playwright.sync_api import sync_playwright

urllib3.disable_warnings()

# Launch clean browser, wait for Ruishu to compute cookies, then read them
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        channel="msedge",
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(
        locale="zh-CN",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
    )
    page = context.new_page()
    page.goto("https://jcyxy.gszy.edu.cn/")
    page.wait_for_timeout(4000)
    
    cookies = {c['name']: c['value'] for c in context.cookies()}
    browser.close()

print('Cookies captured:', list(cookies.keys()))
for k, v in cookies.items():
    print(f"  {k} = {v[:30]}...")

headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://jcyxy.gszy.edu.cn/'
}

r = requests.get('https://jcyxy.gszy.edu.cn/', cookies=cookies, headers=headers, verify=False)
print('Requests status:', r.status_code)
print('Response text len:', len(r.text))
print('Response snippet:\n', r.text[:800])
