import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=False,
        channel="msedge",
        args=['--disable-blink-features=AutomationControlled']
    )
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
        extra_http_headers={
            'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Microsoft Edge";v="120"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"'
        }
    )
    context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
    page = context.new_page()
    def on_req(req):
        if req.resource_type == 'document':
            print(f"Req: {req.method} {req.url}")
            for k, v in req.headers.items():
                print(f"  {k}: {v}")
    def on_resp(resp):
        if resp.request.resource_type == 'document':
            print(f"Resp: {resp.status} {resp.url}")
            for k, v in resp.headers.items():
                print(f"  resp {k}: {v}")
            if resp.status == 400:
                try:
                    print("  400 body:", resp.text())
                except Exception as e:
                    print("  body err:", e)
    page.on('request', on_req)
    page.on('response', on_resp)
    try:
        page.goto('https://ggws.gszy.edu.cn/', wait_until='networkidle', timeout=15000)
    except Exception as e:
        print('goto:', e)
    page.wait_for_timeout(3000)
    print('Final Title:', page.title())
    print('Final URL:', page.url)
    print('HTML len:', len(page.content()))
    if len(page.content()) > 500:
        print(page.content()[:400])
    browser.close()
