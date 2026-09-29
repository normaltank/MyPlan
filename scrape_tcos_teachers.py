import urllib.request
import re
import json
import sys
import io
from bs4 import BeautifulSoup

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

teachers_meta = [
    {"name": "汪小玲", "url": "http://tcos.shisu.edu.cn/5200/list.htm", "category": "主任/院长"},
    {"name": "梅德明", "url": "http://tcos.shisu.edu.cn/17/fd/c5212a137213/page.htm", "category": "中教"},
    {"name": "于飞", "url": "http://tcos.shisu.edu.cn/17/fe/c5212a137214/page.htm", "category": "中教"},
    {"name": "盛建元", "url": "http://tcos.shisu.edu.cn/18/01/c5212a137217/page.htm", "category": "中教"},
    {"name": "陈立青", "url": "http://tcos.shisu.edu.cn/18/07/c5212a137223/page.htm", "category": "中教"},
    {"name": "朱玉山", "url": "http://tcos.shisu.edu.cn/18/08/c5212a137224/page.htm", "category": "中教"},
    {"name": "乔龙宝", "url": "http://tcos.shisu.edu.cn/18/0b/c5212a137227/page.htm", "category": "中教"},
    {"name": "陈佳", "url": "http://tcos.shisu.edu.cn/18/0f/c5212a137231/page.htm", "category": "中教"},
    {"name": "郝景洁", "url": "http://tcos.shisu.edu.cn/18/1e/c5212a137246/page.htm", "category": "中教"},
    {"name": "陈怡", "url": "http://tcos.shisu.edu.cn/18/10/c5212a137232/page.htm", "category": "中教"},
    {"name": "陈健", "url": "http://tcos.shisu.edu.cn/18/48/c5212a137288/page.htm", "category": "中教"},
    {"name": "周成", "url": "http://tcos.shisu.edu.cn/18/47/c5212a137287/page.htm", "category": "中教"},
    {"name": "王俭", "url": "http://tcos.shisu.edu.cn/18/49/c5212a137289/page.htm", "category": "中教"},
    {"name": "张宁", "url": "http://tcos.shisu.edu.cn/18/4b/c5212a137291/page.htm", "category": "中教"},
    {"name": "王继楠", "url": "http://tcos.shisu.edu.cn/18/4c/c5212a137292/page.htm", "category": "中教"},
    {"name": "蔡坚", "url": "http://tcos.shisu.edu.cn/18/4e/c5212a137294/page.htm", "category": "中教"},
    {"name": "孔祥珺", "url": "http://tcos.shisu.edu.cn/18/4f/c5212a137295/page.htm", "category": "中教"},
    {"name": "刘爽", "url": "http://tcos.shisu.edu.cn/18/50/c5212a137296/page.htm", "category": "中教"},
    {"name": "孙启梦", "url": "http://tcos.shisu.edu.cn/e3/be/c5212a189374/page.htm", "category": "中教"},
    {"name": "常翠", "url": "http://tcos.shisu.edu.cn/2d/77/c5212a208247/page.htm", "category": "中教"},
    {"name": "Felix Goodbody", "url": "http://tcos.shisu.edu.cn/0f/2a/c5213a200490/page.htm", "category": "外教"},
    {"name": "Cornelius Anjous Gomes", "url": "http://tcos.shisu.edu.cn/e9/12/c5213a190738/page.htm", "category": "外教"},
    {"name": "Sialupapu Siameja", "url": "http://tcos.shisu.edu.cn/c1/fa/c5213a180730/page.htm", "category": "外教"},
    {"name": "Jonathan Highmark", "url": "http://tcos.shisu.edu.cn/c1/f7/c5213a180727/page.htm", "category": "外教"},
    {"name": "德永富美惠", "url": "http://tcos.shisu.edu.cn/c8/8d/c5213a116877/page.htm", "category": "外教"},
    {"name": "Michael Robert Gonzales", "url": "http://tcos.shisu.edu.cn/c7/e9/c5213a116713/page.htm", "category": "外教"},
    {"name": "Ben Keegan", "url": "http://tcos.shisu.edu.cn/a4/01/c5213a107521/page.htm", "category": "外教"},
    {"name": "Romain VUATTOUX", "url": "http://tcos.shisu.edu.cn/6d/11/c5213a93457/page.htm", "category": "外教"}
]

email_regex = re.compile(r'[a-zA-Z0-9_.+-]+(?:@|\[at\]|#)[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')

raw_data = []

for item in teachers_meta:
    url = item["url"]
    name = item["name"]
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        html = urllib.request.urlopen(req, timeout=10).read().decode('utf-8', errors='ignore')
        soup = BeautifulSoup(html, 'html.parser')
        
        art = soup.find(class_='article')
        if not art:
            # try finding main content area
            candidates = soup.find_all('div')
            for c in candidates:
                if name in c.get_text() and len(c.get_text()) > 100:
                    art = c
                    break
        
        text = art.get_text(separator='\n') if art else soup.body.get_text(separator='\n')
        
        # Clean text
        lines = [line.strip() for line in text.split('\n') if line.strip()]
        clean_text = '\n'.join(lines)
        
        # Look for emails in html and text
        found_emails = email_regex.findall(html) + email_regex.findall(clean_text)
        # Normalize emails
        clean_emails = []
        for em in found_emails:
            em = em.replace('[at]', '@').replace('#', '@').strip('.')
            if '@' in em and not em.endswith(('.png', '.jpg', '.gif', '.css', '.js')):
                clean_emails.append(em.lower())
        clean_emails = list(set(clean_emails))
        
        raw_data.append({
            "name": name,
            "category": item["category"],
            "url": url,
            "emails": clean_emails,
            "raw_text": clean_text
        })
        print(f"Processed: {name} (Emails: {clean_emails})")
    except Exception as e:
        print(f"Error {name}: {e}")
        raw_data.append({
            "name": name,
            "category": item["category"],
            "url": url,
            "emails": [],
            "error": str(e)
        })

with open("tcos_raw_teachers.json", "w", encoding="utf-8") as f:
    json.dump(raw_data, f, ensure_ascii=False, indent=2)

print("Done! Total:", len(raw_data))
