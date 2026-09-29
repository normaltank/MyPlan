import requests, urllib3, re
from bs4 import BeautifulSoup

urllib3.disable_warnings()

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# Let's inspect the reception paths on www.gszy.edu.cn
base = "https://www.gszy.edu.cn"

# Let's test special list / person list / school list / news list
urls = [
    f"{base}/reception/person/list/1.htm",
    f"{base}/reception/person/list/2.htm",
    f"{base}/reception/person/list/3.htm",
    f"{base}/reception/person/list/4.htm",
    f"{base}/reception/special/list/1.htm",
    f"{base}/reception/special/list/2.htm",
    f"{base}/reception/special/list/3.htm",
    f"{base}/reception/special/list/4.htm",
    f"{base}/reception/special/list/5.htm",
    f"{base}/reception/special/list/6.htm",
    f"{base}/reception/special/list/7.htm",
    f"{base}/reception/special/list/8.htm",
    f"{base}/reception/special/list/9.htm",
    f"{base}/reception/special/list/10.htm",
    f"{base}/reception/special/list/11.htm",
    f"{base}/reception/special/list/12.htm",
    f"{base}/reception/special/list/13.htm",
    f"{base}/reception/subject/list/index",
    f"{base}/reception/school/list/1.htm",
    f"{base}/reception/school/list/2.htm",
]

for u in urls:
    try:
        r = requests.get(u, headers=headers, verify=False, timeout=5)
        soup = BeautifulSoup(r.text, 'html.parser')
        title = soup.title.string.strip() if soup.title else ''
        text_len = len(soup.get_text())
        # check keywords
        kw = [w for w in ['教授', '副教授', '讲师', '教师', '学院', '博士'] if w in r.text]
        print(f"{u}: status={r.status_code}, len={text_len}, title='{title}', kw={kw}")
    except Exception as e:
        print(f"{u}: err {e}")
