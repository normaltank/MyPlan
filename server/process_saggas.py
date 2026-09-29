import os
import sys
import json
import re
import urllib.request
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding='utf-8')

def main():
    json_path = r'C:\Users\27451\.gemini\antigravity\brain\bf1ac4c6-4dd1-4b63-8b12-660318151ec3\.system_generated\steps\203\output.txt'
    with open(json_path, 'r', encoding='utf-8') as f:
        content = f.read()

    data_idx = content.find('### Result')
    if data_idx != -1:
        raw_json = content[data_idx + len('### Result'):].strip()
        end_idx = raw_json.find('### Ran Playwright code')
        if end_idx != -1:
            raw_json = raw_json[:end_idx].strip()
        items = json.loads(raw_json)
    else:
        items = json.loads(content)

    print(f"Loaded {len(items)} raw scholars.")
    
    # Check emails & dehydrated texts
    email_re = re.compile(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)')
    public_emails = {'saggas@shisu.edu.cn', 'office@', 'admin@', 'info@', 'contact@'}
    
    scholar_data = []
    card_titles = {
        'Janko Šćepanović': '副研究员',
        '包安若': '助理研究员',
        '崔钰': '师资博士后',
        '富育红': '副教授',
        '郭明': '副教授，博士生导师',
        '郭小雨': '副研究员，硕士生导师',
        '胡春春': '副教授，硕士生导师',
        '胡文婷': '副教授',
        '黄乐乐': '助理研究员',
        '李冠杰': '智库研究员',
        '李如东': '副研究员，硕士生导师',
        '李威': '博士后',
        '林海文': '在职博士后',
        '林文昕': '副研究员',
        '刘洪钟': '教授，博士生导师',
        '刘琪': '研究员，博士生导师',
        '刘鑫': '博士后',
        '路程': '助理研究员',
        '乔国强': '教授，博士生导师',
        '施展': '教授',
        '谈佳洁': '在职博士后',
        '唐均': '教授',
        '唐陆': '博士后',
        '童欣': '博士后',
        '王欢': '副教授，硕士生导师',
        '王凯': '讲师',
        '王思羽': '助理研究员',
        '王维军': '博士后',
        '吴小凡': '讲师，硕士生导师',
        '吴芷净': '博士后',
        '忻华': '研究员，博士生导师',
        '薛江': '副研究员',
        '尹兰曦': '博士后',
        '喻显龙': '助理研究员，硕士生导师',
        '袁勋': '助理研究员',
        '张彬彬': '副研究员，硕士生导师',
        '张泓玮': '助理研究员',
        '张天资': '博士后',
        '张永维': '博士后',
        '赵玲': '副教授',
        '周瑾艳': '副研究员',
        '周俊杰': '师资博士后',
        '朱伟芳': '博士后',
        '姜锋': '研究员，博士生导师',
        '孟钟捷': '教授，博士生导师',
        '杨成': '教授，博士生导师'
    }
    
    extra_emails = {
        'Janko Šćepanović': 'janko@hotmail.com',
        '孟钟捷': 'zjmeng@history.ecnu.edu.cn',
        '张天资': 'tiannzi@163.com'
    }

    for idx, it in enumerate(items, 1):
        name = it['name']
        cat = it['category']
        raw_html = it.get('contentInfo', '')
        soup = BeautifulSoup(raw_html, 'html.parser')
        text = ' '.join(soup.get_text().split())
        
        all_emails = email_re.findall(text)
        valid_emails = [e for e in all_emails if not any(p in e.lower() for p in public_emails)]
        email = valid_emails[0] if valid_emails else extra_emails.get(name, '')
        
        title = card_titles.get(name, '研究员')
        
        scholar_data.append({
            'index': idx,
            'name': name,
            'title': title,
            'email': email,
            'category': cat,
            'url': it['url'],
            'raw_text': text
        })
        
    out_file = r'server/saggas_dehydrated.json'
    with open(out_file, 'w', encoding='utf-8') as f:
        json.dump(scholar_data, f, ensure_ascii=False, indent=2)
        
    print(f"Saved {len(scholar_data)} scholars to {out_file}.")
    with_email = [s for s in scholar_data if s['email']]
    no_email = [s for s in scholar_data if not s['email']]
    print(f"With email: {len(with_email)} | No email: {len(no_email)} | Total: {len(scholar_data)}")

if __name__ == '__main__':
    main()
