import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        channel="msedge",
        proxy={"server": "http://127.0.0.1:7897"}
    )
    context = browser.new_context(locale="zh-CN")
    page = context.new_page()
    page.on("response", lambda r: print(f"Response: {r.status} {r.url}"))
    
    try:
        page.goto("https://jcyxy.gszy.edu.cn/", timeout=15000)
    except Exception as e:
        print("goto error:", e)
        
    page.wait_for_timeout(4000)
    print("Title:", page.title())
    print("URL:", page.url)
    print("Content len:", len(page.content()))
    print("Snippet:", page.content()[:500])
    browser.close()
