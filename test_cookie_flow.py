import sys
from playwright.sync_api import sync_playwright
from playwright_stealth import stealth_sync

sys.stdout.reconfigure(encoding='utf-8')

with sync_playwright() as p:
    b = p.chromium.launch(
        channel='msedge', 
        headless=True,
        args=['--disable-blink-features=AutomationControlled']
    )
    context = b.new_context(locale='zh-CN')
    page = context.new_page()
    stealth_sync(page)
    def on_req(req):
        if 'gszy.edu.cn' in req.url:
            print('REQ:', req.method, req.url)
            print('  Cookie header:', req.headers.get('cookie'))
    def on_resp(resp):
        if 'gszy.edu.cn' in resp.url:
            print('RESP:', resp.status, resp.url)
    page.on('request', on_req)
    page.on('response', on_resp)
    page.goto('https://yxx.gszy.edu.cn/')
    page.wait_for_timeout(4000)
    print('Context cookies before nav:', [c['name'] for c in context.cookies()])
    page.goto('https://yxx.gszy.edu.cn/')
    page.wait_for_timeout(3000)
    print('Final title:', page.title())
    b.close()
