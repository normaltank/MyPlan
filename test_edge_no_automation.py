import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        channel="msedge",
        ignore_default_args=["--enable-automation"],
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox"
        ]
    )
    context = browser.new_context(
        locale="zh-CN",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 Edg/128.0.0.0"
    )
    page = context.new_page()
    page.on("response", lambda r: print(f"Response: {r.status} {r.url}"))
    
    try:
        resp = page.goto("http://jcyxy.gszy.edu.cn/", wait_until="networkidle", timeout=15000)
    except Exception as e:
        print("goto error:", e)
    
    page.wait_for_timeout(3000)
    print("Final URL:", page.url)
    print("Title:", page.title())
    print("Content preview:", page.content()[:300])
    browser.close()
