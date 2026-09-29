import sys
from playwright.sync_api import sync_playwright
from playwright_stealth.stealth import StealthConfig

sys.stdout.reconfigure(encoding='utf-8')

combined_stealth_js = '\n'.join(StealthConfig().enabled_scripts)

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        channel="msedge",
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(locale="zh-CN")
    page = context.new_page()
    page.add_init_script(combined_stealth_js)
    
    page.on("response", lambda r: print(f"Response: {r.status} {r.url}"))
    try:
        resp = page.goto("https://jcyxy.gszy.edu.cn/", wait_until="networkidle", timeout=15000)
    except Exception as e:
        print("goto error:", e)
        
    print("Waiting 5s for Ruishu cookies...")
    page.wait_for_timeout(5000)
    print("Title:", page.title())
    print("URL:", page.url)
    print("Content preview:\n", page.content()[:800])
    
    # Check if page reloaded or if we need to reload
    if not page.title():
        print("Trying page.reload()...")
        page.reload(wait_until="networkidle", timeout=10000)
        print("After reload Title:", page.title(), "Len:", len(page.content()))
        print("Snippet after reload:\n", page.content()[:800])
        
    browser.close()
