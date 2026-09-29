import sys
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

sys.stdout.reconfigure(encoding='utf-8')

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        channel="msedge"
    )
    context = browser.new_context(locale="zh-CN")
    page = context.new_page()
    stealth_sync(page)
    
    page.on("response", lambda r: print(f"Response: {r.status} {r.url}"))
    try:
        resp = page.goto("https://jcyxy.gszy.edu.cn/", timeout=15000)
    except Exception as e:
        print("goto error:", e)
        
    print("Waiting 10s for Ruishu worker to complete...")
    for i in range(10):
        page.wait_for_timeout(1000)
        cookies = context.cookies()
        if len(cookies) > 1:
            print(f"[{i+1}s] Cookies count: {len(cookies)}, Title: '{page.title()}', URL: {page.url}")
            if page.title():
                print("SUCCESSFULLY LOADED PAGE!")
                break
                
    print("Final Title:", page.title())
    print("Final URL:", page.url)
    print("Content preview:\n", page.content()[:500])
    browser.close()
