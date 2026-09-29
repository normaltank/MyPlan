import os
import sys
import re
import json
import datetime
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup
import pandas as pd
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

sys.stdout.reconfigure(encoding='utf-8')

BASE_API = "http://127.0.0.1:8000"
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

EMAIL_RE = re.compile(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)')
PUBLIC_EMAILS = {'dean@', 'office@', 'admin@', 'info@', 'service@', 'contact@', 'webmaster@', 'portal@', 'foodsci@'}

VALID_TLDS = ('.cn', '.edu.cn', '.com', '.net', '.org', '.edu', '.cc', '.me')

def extract_email(text: str) -> str:
    if not text:
        return ""
    matches = EMAIL_RE.findall(text.replace('[at]', '@').replace('(at)', '@').replace('#', '@'))
    for m in matches:
        m_lower = m.lower()
        if any(p in m_lower for p in PUBLIC_EMAILS):
            continue
        if any(bad in m_lower for bad in ['html5shiv', 'respond', 'npm', 'cdn', 'jsdelivr', 'github', 'cloudflare']):
            continue
        if any(ext in m_lower for ext in ['.png', '.jpg', '.css', '.js', '.min', '.webp']):
            continue
        if not any(m_lower.endswith(tld) for tld in VALID_TLDS):
            continue
        return m
    return ""

def clean_bio_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(['script', 'style', 'noscript', 'header', 'footer', 'nav']):
        tag.decompose()
    main = soup.find(class_='content') or soup.find(class_='v_news_content') or soup.find(id='vsb_content') or soup.body or soup
    text = main.get_text(separator='\n', strip=True)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def export_dual_sheet(records: list, school: str, college: str) -> str:
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{school}_{college}_{timestamp}.xlsx"
    filepath = os.path.join(OUTPUT_DIR, filename)

    columns = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']
    df = pd.DataFrame(records)
    if df.empty:
        df = pd.DataFrame(columns=columns)
    else:
        df = df[columns]
        for col in df.columns:
            df[col] = df[col].apply(lambda x: re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', str(x)) if isinstance(x, str) else x)

    df_with = df[df['邮箱'].str.strip() != ''].copy()
    df_no = df[df['邮箱'].str.strip() == ''].copy()

    df_with['序号'] = range(1, len(df_with) + 1)
    df_no['序号'] = range(1, len(df_no) + 1)

    s1_name = f"有邮箱学者 (共 {len(df_with)} 位)"
    s2_name = f"无邮箱学者 (共 {len(df_no)} 位)"

    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        df_with.to_excel(writer, sheet_name=s1_name, index=False)
        df_no.to_excel(writer, sheet_name=s2_name, index=False)

    wb = openpyxl.load_workbook(filepath)
    h_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    h_font = Font(name="Microsoft YaHei", size=11, bold=True, color="1E293B")
    d_font = Font(name="Microsoft YaHei", size=10, color="334155")
    thin = Border(
        left=Side(style='thin', color='E2E8F0'),
        right=Side(style='thin', color='E2E8F0'),
        top=Side(style='thin', color='E2E8F0'),
        bottom=Side(style='thin', color='E2E8F0')
    )

    for ws in wb.worksheets:
        ws.views.sheetView[0].showGridLines = True
        for row_idx, row in enumerate(ws.iter_rows(), start=1):
            for cell in row:
                cell.border = thin
                if row_idx == 1:
                    cell.fill = h_fill
                    cell.font = h_font
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                else:
                    cell.font = d_font
                    if cell.column in [1, 2, 3, 5, 6, 7, 8]:
                        cell.alignment = Alignment(horizontal='center', vertical='center')
                    else:
                        cell.alignment = Alignment(horizontal='left', vertical='center')

        for col in ws.columns:
            letter = get_column_letter(col[0].column)
            max_len = 0
            for cell in col[:15]:
                val = str(cell.value or '')
                max_len = max(max_len, len(val.encode('gbk', 'ignore')))
            ws.column_dimensions[letter].width = min(max(max_len + 4, 12), 50)

    wb.save(filepath)
    return filepath, filename, len(df), len(df_with), len(df_no)

def sync_task(task_id: str, records: list, filepath: str, filename: str, total: int, with_email: int, no_email: int):
    payload = {
        "records": records,
        "stats": {
            "total": total,
            "with_email": with_email,
            "no_email": no_email,
            "email_rate": round(with_email / total * 100, 1) if total > 0 else 0
        },
        "download_url": f"/download/{filename}",
        "progress": 100
    }
    with httpx.Client(timeout=10.0) as client:
        r = client.post(f"{BASE_API}/api/task/{task_id}/sync", json=payload)
        print(f"📡 Sync {task_id}: {r.status_code}")

def harvest_foodsci(task_id: str = "c955e40c"):
    school = "江南大学"
    college = "食品学院"
    print(f"\n==================================================")
    print(f"🚀 开始采集：【{school} - {college}】(Task ID: {task_id})")
    print(f"==================================================")

    # Claim task
    with httpx.Client(timeout=10.0) as client:
        client.post(f"{BASE_API}/api/queue/claim/{task_id}")

    categories = [
        {"title": "教授", "base_url": "https://foodsci.jiangnan.edu.cn/szdw/js.htm", "total_pages": 8, "sub_path": "js"},
        {"title": "副教授", "base_url": "https://foodsci.jiangnan.edu.cn/szdw/fjs1.htm", "total_pages": 7, "sub_path": "fjs1"},
        {"title": "讲师", "base_url": "https://foodsci.jiangnan.edu.cn/szdw/zjzc.htm", "total_pages": 1, "sub_path": "zjzc"},
    ]

    all_teachers = []
    seen_urls = set()

    with httpx.Client(timeout=10.0, verify=False, headers=HEADERS, follow_redirects=True) as client:
        for cat in categories:
            t_name = cat["title"]
            base_u = cat["base_url"]
            t_pages = cat["total_pages"]
            sub = cat["sub_path"]

            page_urls = [base_u]
            if t_pages > 1:
                for p in range(t_pages - 1, 0, -1):
                    page_urls.append(f"https://foodsci.jiangnan.edu.cn/szdw/{sub}/{p}.htm")

            for pu in page_urls:
                try:
                    r = client.get(pu)
                    soup = BeautifulSoup(r.text, 'html.parser')
                    for a in soup.find_all('a'):
                        h = a.get('href')
                        t = a.text.replace(' ', '').replace('\u3000', '').strip()
                        if not h or not t:
                            continue
                        if len(t) >= 2 and len(t) <= 4 and not any(k in t for k in ['首页', '学院', '教授', '职称', '概况', '电话', '邮箱', '科学', '党政', '历任', '招生', '国际', '社会', '关工', '先锋', '中央', '主题', '学生', '通知', '科创', '下一页', '上一页', '跳转', '手机', '交流', '董事', '培训']):
                            full_u = urljoin(pu, h)
                            if full_u not in seen_urls and ('/szdw/' in full_u or '/info/' in full_u):
                                seen_urls.add(full_u)
                                all_teachers.append({
                                    "name": t,
                                    "title": t_name,
                                    "url": full_u
                                })
                except Exception as e:
                    print(f"⚠️ 获取目录页失败 {pu}: {e}")

    print(f"🔍 食品学院共检索出 {len(all_teachers)} 位专任学者主页，开始穿透提炼...")

    cache_file = os.path.join(OUTPUT_DIR, "foodsci_records.json")
    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            records = json.load(f)
        print(f"📦 已从本地缓存读取 {len(records)} 条学者详情！")
    else:
        records = []
        idx = 1
        with httpx.Client(timeout=10.0, verify=False, headers=HEADERS, follow_redirects=True) as client:
            for teacher in all_teachers:
                name = teacher["name"]
                title = teacher["title"]
                url = teacher["url"]

                bio_text = ""
                email = ""

                try:
                    p_resp = client.get(url)
                    if p_resp.status_code == 200:
                        bio_text = clean_bio_text(p_resp.text)
                        email = extract_email(p_resp.text)
                except Exception as e:
                    print(f"⚠️ 无法访问 {name} ({url}): {e}")

                record = {
                    "序号": idx,
                    "姓名": name,
                    "职称": title,
                    "邮箱": email,
                    "学校": school,
                    "学院": college,
                    "采集人": "Antigravity Subagent (Browser)",
                    "采集日期": datetime.date.today().strftime("%Y-%m-%d"),
                    "简介": bio_text,
                    "主页": url
                }
                records.append(record)
                idx += 1
                if idx % 20 == 0 or idx == len(all_teachers) + 1:
                    print(f"   [{idx-1}/{len(all_teachers)}] 已提炼: {name} ({title}) | 邮箱: {email or '无'}")
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    filepath, filename, total, with_email, no_email = export_dual_sheet(records, school, college)
    sync_task(task_id, records, filepath, filename, total, with_email, no_email)
    print(f"🎉 【{school} - {college}】完成！总人数: {total} | 有邮箱: {with_email} | 无邮箱: {no_email} (严格守恒)")
    print(f"   交付表: {filepath}")

if __name__ == "__main__":
    harvest_foodsci()
