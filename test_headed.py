import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, channel="msedge")
    context = browser.new_context(
        locale="zh-CN"
    )
    page = context.new_page()
    page.on("response", lambda r: print(f"Response: {r.status} {r.url}"))
    
    try:
        page.goto("https://jcyxy.gszy.edu.cn/", wait_until="networkidle", timeout=15000)
    except Exception as e:
        print("goto warning:", e)
    
    page.wait_for_timeout(3000)
    print("Final URL:", page.url)
    print("Title:", page.title())
    print("Content preview:", page.content()[:300])
    browser.close()
