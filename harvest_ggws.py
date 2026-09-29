import sys
import os
import re
import json
import datetime
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright
import pandas as pd
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
import requests

sys.stdout.reconfigure(encoding='utf-8')

ROOT_DIR = os.path.abspath(os.path.dirname(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from server.faculty_extractor import FacultyProfileExtractor, is_personal_email_for_scholar
from bs4 import BeautifulSoup

SCHOOL = "甘肃中医药大学"
COLLEGE = "公共卫生学院"
TASK_ID = "1af268be704040f494fe39279b636b2b"

BASE_URL = "https://ggws.gszy.edu.cn/"

COLUMNS = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']

def harvest():
    print(f"Starting harvest for {SCHOOL} - {COLLEGE}...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            channel="msedge",
            args=['--disable-blink-features=AutomationControlled']
        )
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        
        # 1. Clear WAF
        print("Navigating to home page to bypass WAF...")
        page.goto(BASE_URL, timeout=30000)
        page.wait_for_timeout(3500)
        print(f"Bypassed WAF, title: {page.title()}")
        
        categories = [
            ("教授", "https://ggws.gszy.edu.cn/ggwshtml/shizililiang/jiaoshou/"),
            ("副教授", "https://ggws.gszy.edu.cn/ggwshtml/shizililiang/fujiaoshou/"),
            ("讲师", "https://ggws.gszy.edu.cn/ggwshtml/shizililiang/jiangshi/"),
            ("助教", "https://ggws.gszy.edu.cn/ggwshtml/shizililiang/zhujiao/")
        ]
        
        teachers = []
        seen_urls = set()
        seen_names = set()
        
        for rank, cat_url in categories:
            print(f"\nProcessing category: {rank} ({cat_url})")
            # Discover pagination
            page.goto(cat_url, timeout=30000)
            page.wait_for_timeout(1500)
            
            # Find pagination links
            pages_to_visit = [cat_url]
            cat_links = page.evaluate('''() => {
                return Array.from(document.querySelectorAll('a')).map(a => ({
                    text: a.innerText.trim(),
                    href: a.href
                }));
            }''')
            for item in cat_links:
                h = item['href']
                t = item['text']
                if h and ('lists_' in h or t.isdigit() or t in ['下一页', '末页']):
                    if h not in pages_to_visit and h.startswith(cat_url.rstrip('/')):
                        pages_to_visit.append(h)
            
            print(f"Found {len(pages_to_visit)} pages for {rank}: {pages_to_visit}")
            
            for pu in pages_to_visit:
                if pu != page.url:
                    page.goto(pu, timeout=30000)
                    page.wait_for_timeout(1000)
                
                # Extract teacher links on this list page
                # In ggws.gszy.edu.cn, teacher links are in list containers
                items = page.evaluate('''() => {
                    const links = Array.from(document.querySelectorAll('a')).map(a => ({
                        text: a.innerText.trim(),
                        href: a.href
                    }));
                    return links;
                }''')
                
                for it in items:
                    name = it['text']
                    href = it['href']
                    if not href or not href.startswith('https://ggws.gszy.edu.cn/ggwshtml/shizililiang/'):
                        continue
                    # Must be a detail page: ending with .html and having digits (e.g. 233.html)
                    m = re.search(r'/(\d+)\.html$', href)
                    if not m:
                        continue
                    if name in ['教授', '副教授', '讲师', '助教', '首页', '末页', '上一页', '下一页']:
                        continue
                    # Clean name: remove whitespace or title if embedded
                    name = re.sub(r'[\s\xa0]+', '', name)
                    if not (2 <= len(name) <= 10):
                        continue
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)
                    teachers.append({
                        'name': name,
                        'title': rank,
                        'url': href
                    })
                    print(f"  Found teacher: {name} ({rank}) -> {href}")
        
        print(f"\nTotal faculty members found: {len(teachers)}")
        
        # Now fetch each teacher's profile via in-page fetch
        print("Fetching profiles concurrently via in-page fetch...")
        
        # Batch fetch in chunks of 10
        chunk_size = 10
        for i in range(0, len(teachers), chunk_size):
            chunk = teachers[i:i+chunk_size]
            urls = [t['url'] for t in chunk]
            html_dict = page.evaluate('''async (urls) => {
                const results = {};
                await Promise.all(urls.map(async (u) => {
                    try {
                        const r = await fetch(u);
                        results[u] = await r.text();
                    } catch (e) {
                        results[u] = "";
                    }
                }));
                return results;
            }''', urls)
            
            for t in chunk:
                html = html_dict.get(t['url'], '')
                t['html'] = html
            print(f"Fetched {min(i+chunk_size, len(teachers))}/{len(teachers)} profiles...")
            page.wait_for_timeout(500)
            
        browser.close()
        
    # Extract bios and emails
    print("\nExtracting biographies and emails...")
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    with_email_list = []
    no_email_list = []
    
    # Public emails to block
    PUBLIC_EMAILS = {'ggwsxysj@163.com', 'ggwsxyyz@163.com'}
    
    for idx, t in enumerate(teachers, 1):
        name = t['name']
        rank = t['title']
        url = t['url']
        html = t.get('html', '')
        
        bio = ""
        email = ""
        
        if html:
            soup = BeautifulSoup(html, 'html.parser')
            # Remove scripts, styles, nav, header, footer
            for tag in soup(['script', 'style', 'nav', 'header', 'footer']):
                tag.decompose()
            for cls in ['header', 'footer', 'main-nav', 'show-label', 'search', 'box1', 'box2', 'box3', 'box4', 'box5', 'box6']:
                for el in soup.find_all(class_=cls):
                    el.decompose()
            
            # Find main article container
            # In eyou cms, typically content or article body
            content_div = None
            for selector in ['.content', '.article-content', '.news_content', '.article_content', '.show_content', '.view-content']:
                el = soup.select_one(selector)
                if el and len(el.get_text(strip=True)) > 20:
                    content_div = el
                    break
            
            if not content_div:
                content_div = soup.body if soup.body else soup
                
            text = content_div.get_text('\n', strip=True)
            # Clean noise lines
            lines = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if any(noise in line for noise in ['当前位置：', '作者：', '发布者：', '发布日期：', '浏览次数：', '下一篇：', '上一篇：']):
                    continue
                if any(noise in line for noise in ['五里铺校区', '和平校区', '甘公网安备', 'Copyright', '电话：0931']):
                    continue
                if line in ['教授', '副教授', '讲师', '助教', '师资力量', '甘肃中医药大学公共卫生学院']:
                    continue
                lines.append(line)
            bio = "\n".join(lines).strip()
            
            # Extract email
            raw_emails = re.findall(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', html)
            for em in raw_emails:
                em = em.strip().rstrip('.')
                if em.lower() in PUBLIC_EMAILS:
                    continue
                if any(pub in em.lower() for pub in ['office', 'dean', 'admin', 'contact', 'yjs', 'xyyz', 'xysj']):
                    continue
                # Validate scholar email
                email = em
                break
        
        record = {
            '序号': idx,
            '姓名': name,
            '职称': rank,
            '邮箱': email,
            '学校': SCHOOL,
            '学院': COLLEGE,
            '采集人': '智能体',
            '采集日期': today_str,
            '简介': bio,
            '主页': url
        }
        
        if email:
            with_email_list.append(record)
        else:
            no_email_list.append(record)

    total_count = len(teachers)
    email_count = len(with_email_list)
    no_email_count = len(no_email_list)
    
    # Assert conservation
    assert email_count + no_email_count == total_count, "Conservation violation!"
    print(f"\nConservation Verified: {email_count} (with email) + {no_email_count} (without email) = {total_count} total")
    
    # Export dual-sheet Excel
    os.makedirs('server/outputs', exist_ok=True)
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    excel_path = f"server/outputs/{SCHOOL}_{COLLEGE}_{timestamp}.xlsx"
    standard_excel_path = f"server/outputs/{SCHOOL}_{COLLEGE}.xlsx"
    
    wb = openpyxl.Workbook()
    # Sheet 1
    ws1 = wb.active
    ws1.title = "教师列表"
    ws1.append(COLUMNS)
    for r in with_email_list:
        ws1.append([r[col] for col in COLUMNS])
        
    # Sheet 2
    ws2 = wb.create_sheet(title="无邮箱教师列表")
    ws2.append(COLUMNS)
    for r in no_email_list:
        ws2.append([r[col] for col in COLUMNS])
        
    # Styling
    header_fill = PatternFill(start_color="1F497D", end_color="1F497D", fill_type="solid")
    header_font = Font(name="微软雅黑", size=11, bold=True, color="FFFFFF")
    data_font = Font(name="微软雅黑", size=10)
    thin_border = Border(
        left=Side(style='thin', color='D9D9D9'),
        right=Side(style='thin', color='D9D9D9'),
        top=Side(style='thin', color='D9D9D9'),
        bottom=Side(style='thin', color='D9D9D9')
    )
    
    for ws in [ws1, ws2]:
        for col_idx in range(1, len(COLUMNS) + 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
            
        for row in range(2, ws.max_row + 1):
            for col in range(1, len(COLUMNS) + 1):
                c = ws.cell(row=row, column=col)
                c.font = data_font
                c.border = thin_border
                if COLUMNS[col-1] in ['序号', '姓名', '职称', '采集人', '采集日期']:
                    c.alignment = Alignment(horizontal="center", vertical="center")
                else:
                    c.alignment = Alignment(vertical="center")
                    
        for col in ws.columns:
            max_len = max(len(str(cell.value or '')) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws.column_dimensions[col_letter].width = min(max(max_len + 3, 12), 50)
            
    wb.save(excel_path)
    wb.save(standard_excel_path)
    print(f"Excel saved to {excel_path} and {standard_excel_path}")
    
    # Sync with backend
    sync_url = f"http://127.0.0.1:8000/api/task/{TASK_ID}/sync"
    sync_payload = {
        "excel_path": excel_path,
        "total_count": total_count,
        "email_count": email_count,
        "no_email_count": no_email_count
    }
    try:
        r = requests.post(sync_url, json=sync_payload, timeout=5)
        print(f"Backend sync response: {r.status_code} {r.text}")
    except Exception as e:
        print(f"Backend sync error: {e}")
        
    result_json = {
        "task_id": TASK_ID,
        "school": SCHOOL,
        "college": COLLEGE,
        "status": "completed",
        "total": total_count,
        "with_email": email_count,
        "no_email": no_email_count,
        "excel": f"server/outputs/{SCHOOL}_{COLLEGE}.xlsx"
    }
    print(f"\nFinal Result JSON:\n{json.dumps(result_json, ensure_ascii=False)}")
    return result_json

if __name__ == '__main__':
    harvest()
