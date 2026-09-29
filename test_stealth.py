from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-setuid-sandbox'
        ]
    )
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
        viewport={'width': 1920, 'height': 1080},
    )
    page = context.new_page()
    stealth_sync(page)
    
    page.on('response', lambda r: print('STATUS:', r.status, r.url))
    resp = page.goto('https://xxxy.gszy.edu.cn/')
    print('Initial goto done. Waiting 5s for challenge...')
    page.wait_for_timeout(5000)
    print('Cookies after wait:', [c['name'] for c in context.cookies()])
    print('Reloading...')
    r2 = page.goto('https://xxxy.gszy.edu.cn/', wait_until='networkidle')
    print('R2 status:', r2.status if r2 else None)
    print('Final URL:', page.url)
    print('Title:', page.title())
    print('Content len:', len(page.content()))
    if len(page.content()) > 500:
        print('Snippet:', page.content()[:500])
    browser.close()
