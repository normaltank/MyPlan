import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, channel="msedge", args=["--disable-blink-features=AutomationControlled"])
    context = browser.new_context(
        locale="zh-CN",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0"
    )
    page = context.new_page()
    page.on("response", lambda r: print(f"Response: {r.status} {r.url}"))
    page.on("console", lambda m: print(f"Console [{m.type}]: {m.text}"))
    
    try:
        page.goto("https://jcyxy.gszy.edu.cn/", wait_until="networkidle", timeout=15000)
    except Exception as e:
        print("goto warning:", e)
    
    page.wait_for_timeout(3000)
    print("Final URL:", page.url)
    print("Title:", page.title())
    print("Cookies:", context.cookies())
    browser.close()
