from playwright.sync_api import sync_playwright
import subprocess

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('https://xxxy.gszy.edu.cn/')
    page.wait_for_timeout(3000)
    cookies = page.context.cookies()
    cookie_str = '; '.join([f"{c['name']}={c['value']}" for c in cookies])
    print('Cookie string len:', len(cookie_str))
    print('Cookies:', [c['name'] for c in cookies])
    browser.close()

cmd = ['curl.exe', '-i', '-k', '-H', f'Cookie: {cookie_str}', 'https://xxxy.gszy.edu.cn/']
out = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='ignore')
print('Curl output status and headers:')
print('\n'.join(out.stdout.splitlines()[:25]))
