import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

with sync_playwright() as p:
    b = p.firefox.launch(headless=True)
    page = b.new_page()
    
    def on_req(req):
        print(f"\n--- REQUEST: {req.method} {req.url} ---")
        for k, v in req.headers.items():
            print(f"  {k}: {v}")
            
    def on_resp(resp):
        print(f"\n<<< RESPONSE: {resp.status} {resp.url} <<<")
        for k, v in resp.headers.items():
            print(f"  {k}: {v}")
        if resp.status == 400:
            try:
                print("  400 Body:", resp.text())
            except:
                pass

    page.on('request', on_req)
    page.on('response', on_resp)

    try:
        page.goto('https://jcyxy.gszy.edu.cn/', wait_until='networkidle', timeout=15000)
    except Exception as e:
        print('goto err:', e)

    page.wait_for_timeout(2000)
    b.close()
