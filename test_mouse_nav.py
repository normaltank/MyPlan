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
        
    print("Waiting 4s for Ruishu cookies...")
    page.wait_for_timeout(4000)
    
    # Simulate user mouse movements
    page.mouse.move(100, 100)
    page.mouse.move(200, 300)
    page.mouse.move(300, 150)
    
    cookies = context.cookies()
    print("Cookies generated:", [c['name'] for c in cookies])
    
    # Now trigger navigation / reload with JS
    print("Navigating to home page...")
    try:
        page.evaluate("location.href = 'https://jcyxy.gszy.edu.cn/';")
        page.wait_for_timeout(4000)
    except Exception as e:
        print("nav error:", e)
        
    print("Title:", page.title())
    print("URL:", page.url)
    print("Content len:", len(page.content()))
    print("Snippet:", page.content()[:800])
    browser.close()
