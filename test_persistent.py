import os, time
from playwright.sync_api import sync_playwright

user_data_dir = os.path.abspath("./tmp_chrome_profile")
os.makedirs(user_data_dir, exist_ok=True)

with sync_playwright() as p:
    context = p.chromium.launch_persistent_context(
        user_data_dir=user_data_dir,
        channel="chrome",
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--window-size=1920,1080',
        ]
    )
    page = context.pages[0] if context.pages else context.new_page()
    page.on('response', lambda r: print('STATUS:', r.status, r.url[:60]))
    try:
        page.goto('https://xxxy.gszy.edu.cn/', wait_until='networkidle', timeout=15000)
    except Exception as e:
        print('Goto error:', e)
    
    print('Waiting 3s...')
    page.wait_for_timeout(3000)
    print('URL:', page.url)
    print('Title:', page.title())
    print('HTML len:', len(page.content()))
    context.close()
