import sys
from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding='utf-8')

urls = [
    ("基础医学院", "https://jcyxy.gszy.edu.cn/"),
    ("药学院", "https://yxx.gszy.edu.cn/"),
    ("针灸推拿学院", "https://zjtnx.gszy.edu.cn/"),
    ("中医临床学院", "https://zylcxy.gszy.edu.cn/"),
    ("护理学院", "https://hlxy.gszy.edu.cn/"),
    ("公共卫生学院", "https://ggws.gszy.edu.cn/"),
    ("医学信息工程学院", "https://xxxy.gszy.edu.cn/"),
    ("卫生管理学院", "https://jgy.gszy.edu.cn/"),
    ("继续教育学院", "https://jxjy.gszy.edu.cn/"),
    ("马克思主义学院", "https://mkszyxy.gszy.edu.cn/"),
    ("第一临床医学院", "https://lcyxy.gszy.edu.cn/"),
    ("第二临床医学院", "http://www.lzsdyyy.cn/"),
]

with sync_playwright() as p:
    b = p.firefox.launch(headless=True)
    for name, u in urls:
        page = b.new_page()
        resps = []
        page.on('response', lambda r: resps.append(r.status))
        try:
            page.goto(u, wait_until='networkidle', timeout=10000)
            page.wait_for_timeout(2000)
        except Exception as e:
            pass
        print(f"{name} ({u}): final_url={page.url}, title='{page.title()}', resps={resps}")
        page.close()
    b.close()
