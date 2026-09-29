import requests
import urllib3
import sys
sys.stdout.reconfigure(encoding='utf-8')
urllib3.disable_warnings()

colleges = [
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

for name, url in colleges:
    try:
        r = requests.get(url, verify=False, timeout=5, allow_redirects=True)
        print(f"{name}: status={r.status_code}, final_url={r.url}")
    except Exception as e:
        print(f"{name}: error={e}")
