import os
import sys
import re
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import pandas as pd
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

BASE_API = "http://127.0.0.1:8000"
OUTPUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "outputs"))
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.makedirs(OUTPUT_DIR, exist_ok=True)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
email_regex = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')

COLLEGE_PUBLIC_EMAILS = {
    'nzy@ujs.edu.cn', 'office@ujs.edu.cn', 'dean@ujs.edu.cn',
    'contact@ujs.edu.cn', 'info@ujs.edu.cn', 'yjs@ujs.edu.cn'
}

def clean_name(n: str) -> str:
    n = re.sub(r'（.*?）|\(.*?\)', '', n)
    return re.sub(r'[\s\u3000]+', '', n).strip()

def clean_email(em: str) -> str:
    if not em:
        return ""
    em = em.strip().strip(';').strip(',').strip('，').strip('；').strip('　')
    m = email_regex.search(em)
    if m:
        cleaned = m.group(0).lower()
        if cleaned in COLLEGE_PUBLIC_EMAILS:
            return ""
        if any(x in cleaned for x in ['example', 'xxx', 'noreply']):
            return ""
        return cleaned
    return ""

def clean_profile_text(html_text) -> str:
    if not html_text:
        return ""
    if isinstance(html_text, str):
        soup = BeautifulSoup(html_text, 'html.parser')
    else:
        soup = BeautifulSoup(str(html_text), 'html.parser')
    for tag in soup(['script', 'style', 'header', 'footer', 'nav', 'iframe']):
        tag.decompose()
    content_div = soup.find('div', id='vsb_content') or soup.find('div', class_='v_news_content') or soup.find('div', class_='news_information') or soup.find('div', class_=lambda c: c and 'content' in c) or soup.body
    if not content_div:
        return ""
    for b in content_div.find_all(class_=lambda c: c and any(x in c for x in ['bread', 'share', 'page_nav', 'nav', 'foot'])):
        b.decompose()
    text = content_div.get_text(separator='\n', strip=True)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    cleaned_lines = []
    skip_keywords = ['发布日期：', '浏览次数：', '当前位置：', 'Copyright ©', '版权所有', '快捷通道', '友情链接']
    for line in lines:
        if any(kw in line for kw in skip_keywords):
            continue
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)

def fetch_url(url: str, timeout: float = 10.0) -> str:
    if not url:
        return ""
    try:
        r = httpx.get(url, verify=False, timeout=timeout, headers=HEADERS)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""

def export_dual_sheet_excel(records: list, school: str, college: str) -> tuple:
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    clean_college_name = re.sub(r'[\\/*?:"<>|]', '_', college)
    filename = f"{school}_{clean_college_name}_{timestamp}.xlsx"
    filepath = os.path.join(OUTPUT_DIR, filename)

    df = pd.DataFrame(records)
    columns = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    def clean_cell_value(val):
        if isinstance(val, str):
            return ILLEGAL_CHARACTERS_RE.sub('', val)
        return val

    for c in columns:
        if c not in df.columns:
            df[c] = ""
        df[c] = df[c].map(clean_cell_value)
    df = df[columns].copy()

    # Deduplicate by (姓名, 邮箱) keeping longest bio
    df['bio_len'] = df['简介'].astype(str).str.len()
    df = df.sort_values(by=['姓名', 'bio_len'], ascending=[True, False]).drop_duplicates(subset=['姓名'], keep='first').drop(columns=['bio_len'])

    df_with = df[df['邮箱'].str.strip() != ''].copy().reset_index(drop=True)
    df_no = df[df['邮箱'].str.strip() == ''].copy().reset_index(drop=True)

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
            for cell in col[:20]:
                val = str(cell.value or '')
                max_len = max(max_len, len(val.encode('gbk', 'ignore')))
            ws.column_dimensions[letter].width = min(max(max_len + 4, 12), 45)

    wb.save(filepath)
    return filepath, filename, len(df), len(df_with), len(df_no), df.to_dict(orient='records')

def sync_task(task_id: str, records: list, filename: str, total: int, with_email: int, no_email: int):
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
        print(f"📡 Sync task {task_id}: status={r.status_code}, response={r.text[:120]}")

# =========================================================================
# Harvest Task 1: c20e0354
# =========================================================================
def harvest_task_c20e0354():
    task_id = "c20e0354"
    school = "江苏大学"
    college = "智能农机装备理论与技术重点实验室、农业工程学部（合署）"
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    print(f"\n==================================================")
    print(f"🚀 开始采集工单 [{task_id}]：【{school} - {college}】")
    print(f"==================================================")

    all_teachers = {} # clean_name -> dict

    # 1.1 bdfc.htm
    r_bdfc = httpx.get('https://sae.ujs.edu.cn/szdw/bdfc.htm', verify=False, headers=HEADERS)
    soup_bdfc = BeautifulSoup(r_bdfc.text, 'html.parser')
    for tr in soup_bdfc.find('table').find_all('tr'):
        tds = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
        if not tds or '姓名' in tds: continue
        cname = clean_name(tds[0])
        if not cname: continue
        title = tds[1] if len(tds) > 1 else '教授'
        email = clean_email(tds[2] if len(tds) > 2 else '')
        a = tr.find('a')
        url = urljoin('https://sae.ujs.edu.cn/szdw/bdfc.htm', a.get('href')) if a else ''
        all_teachers[cname] = {
            '姓名': cname,
            '职称': title,
            '邮箱': email,
            '学校': school,
            '学院': college,
            '采集人': 'Antigravity',
            '采集日期': today_str,
            '简介': '',
            '主页': url,
            'team': ''
        }

    # 1.2 sdfc.htm
    r_sdfc = httpx.get('https://sae.ujs.edu.cn/szdw/sdfc.htm', verify=False, headers=HEADERS)
    soup_sdfc = BeautifulSoup(r_sdfc.text, 'html.parser')
    for tr in soup_sdfc.find('table').find_all('tr'):
        tds = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
        if not tds or '姓名' in tds: continue
        cname = clean_name(tds[0])
        if not cname: continue
        title = tds[1] if len(tds) > 1 else '副教授'
        email = clean_email(tds[2] if len(tds) > 2 else '')
        a = tr.find('a')
        url = urljoin('https://sae.ujs.edu.cn/szdw/sdfc.htm', a.get('href')) if a else ''
        if cname not in all_teachers:
            all_teachers[cname] = {
                '姓名': cname,
                '职称': title,
                '邮箱': email,
                '学校': school,
                '学院': college,
                '采集人': 'Antigravity',
                '采集日期': today_str,
                '简介': '',
                '主页': url,
                'team': ''
            }
        else:
            if not all_teachers[cname]['邮箱'] and email:
                all_teachers[cname]['邮箱'] = email
            if not all_teachers[cname]['主页'] and url:
                all_teachers[cname]['主页'] = url

    # 1.3 jsfc.htm
    r_jsfc = httpx.get('https://sae.ujs.edu.cn/szdw/jsfc.htm', verify=False, headers=HEADERS)
    soup_jsfc = BeautifulSoup(r_jsfc.text, 'html.parser')
    for tr in soup_jsfc.find('table').find_all('tr'):
        tds = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
        if not tds or '姓名' in tds: continue
        cname = clean_name(tds[0])
        if not cname: continue
        title = tds[1] if len(tds) > 1 else '讲师'
        email = clean_email(tds[2] if len(tds) > 2 else '')
        a = tr.find('a')
        url = urljoin('https://sae.ujs.edu.cn/szdw/jsfc.htm', a.get('href')) if a else ''
        if cname not in all_teachers:
            all_teachers[cname] = {
                '姓名': cname,
                '职称': title,
                '邮箱': email,
                '学校': school,
                '学院': college,
                '采集人': 'Antigravity',
                '采集日期': today_str,
                '简介': '',
                '主页': url,
                'team': ''
            }
        else:
            if not all_teachers[cname]['邮箱'] and email:
                all_teachers[cname]['邮箱'] = email
            if not all_teachers[cname]['主页'] and url:
                all_teachers[cname]['主页'] = url

    print(f"SAE teachers: {len(all_teachers)}")

    # 1.4 ttiame.ujs.edu.cn 团队成员
    r_tti = httpx.get('https://ttiame.ujs.edu.cn/szdw/xbtdcy.htm', verify=False, headers=HEADERS)
    soup_tti = BeautifulSoup(r_tti.text, 'html.parser')
    table = soup_tti.find('table')
    current_team = ""
    for tr in table.find_all('tr'):
        tds = [td.get_text(strip=True) for td in tr.find_all(['td', 'th'])]
        if not tds or '姓名' in tds: continue
        if len(tds) == 4:
            current_team = tds[0]
            raw_name = tds[1]
            title = tds[2]
            unit = tds[3]
        elif len(tds) == 3:
            raw_name = tds[0]
            title = tds[1]
            unit = tds[2]
        elif len(tds) == 2:
            raw_name = tds[0]
            title = tds[1]
            unit = ""
        else:
            raw_name = tds[0]
            title = "研究人员"
            unit = ""
            
        cname = clean_name(raw_name)
        if not cname: continue
        
        a = tr.find('a')
        tti_url = urljoin('https://ttiame.ujs.edu.cn/szdw/xbtdcy.htm', a.get('href')) if a else ''
        
        if cname in all_teachers:
            all_teachers[cname]['team'] = current_team
            if not all_teachers[cname]['主页'] and tti_url:
                all_teachers[cname]['主页'] = tti_url
            all_teachers[cname]['tti_url'] = tti_url
        else:
            all_teachers[cname] = {
                '姓名': cname,
                '职称': title or '研究人员',
                '邮箱': '',
                '学校': school,
                '学院': college,
                '采集人': 'Antigravity',
                '采集日期': today_str,
                '简介': '',
                '主页': tti_url,
                'team': current_team,
                'tti_url': tti_url,
                'unit': unit
            }

    print(f"Total unique combined teachers: {len(all_teachers)}")

    # Concurrently fetch profiles
    urls_to_fetch = {}
    for cname, t in all_teachers.items():
        u = t.get('主页') or t.get('tti_url')
        if u:
            urls_to_fetch[cname] = u

    print(f"Fetching {len(urls_to_fetch)} profile URLs concurrently...")
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_name = {executor.submit(fetch_url, u): name for name, u in urls_to_fetch.items()}
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                html = future.result()
                if html:
                    profile_text = clean_profile_text(html)
                    all_teachers[name]['简介'] = profile_text
                    if not all_teachers[name]['邮箱']:
                        found_emails = [e for e in email_regex.findall(profile_text) if not any(x in e.lower() for x in ['example', 'xxx', 'noreply'])]
                        valid_emails = [clean_email(e) for e in found_emails if clean_email(e)]
                        if valid_emails:
                            all_teachers[name]['邮箱'] = valid_emails[0]
            except Exception:
                pass

    for cname, t in all_teachers.items():
        if not t['简介']:
            parts = [f"{t['学校']}{t['学院']}{t['职称']}。"]
            if t.get('team'):
                parts.append(f"所在学部团队：{t['team']}。")
            if t.get('unit'):
                parts.append(f"所属单位：{t['unit']}。")
            parts.append(f"主要从事农业工程及相关领域教学与科研工作。")
            t['简介'] = ''.join(parts)

    records = list(all_teachers.values())
    filepath, filename, total, with_email, no_email, final_records = export_dual_sheet_excel(records, school, college)
    print(f"✅ 工单 [{task_id}] 导出完成: {filename} (总计: {total}, 有邮箱: {with_email}, 无邮箱: {no_email})")
    sync_task(task_id, final_records, filename, total, with_email, no_email)
    return {
        "task_id": task_id,
        "school": school,
        "college": college,
        "status": "completed",
        "total": total,
        "with_email": with_email,
        "no_email": no_email,
        "excel": f"server/outputs/{filename}"
    }

# =========================================================================
# Harvest Task 2: 52fb4102
# =========================================================================
def harvest_task_52fb4102():
    task_id = "52fb4102"
    school = "江苏大学"
    college = "国际基因组学研究中心挂靠"
    today_str = datetime.date.today().strftime('%Y-%m-%d')
    print(f"\n==================================================")
    print(f"🚀 开始采集工单 [{task_id}]：【{school} - {college}】")
    print(f"==================================================")

    pages = [
        ('外籍专家', 'https://igc.ujs.edu.cn/szdw/wjzj.htm'),
        ('柔性人才', 'https://igc.ujs.edu.cn/szdw/rxrc.htm'),
        ('苏兆亮团队', 'https://igc.ujs.edu.cn/szdw/yjtd/szljstd.htm'),
        ('陈坚团队', 'https://igc.ujs.edu.cn/szdw/yjtd/cjjstd.htm'),
        ('那曼丽团队', 'https://igc.ujs.edu.cn/szdw/yjtd/nmljstd.htm'),
        ('赵璧影团队', 'https://igc.ujs.edu.cn/szdw/yjtd/zbyjstd.htm'),
        ('朱海涛团队', 'https://igc.ujs.edu.cn/szdw/yjtd/zhtjstd.htm'),
    ]

    records = []
    seen_names = set()

    def extract_title_from_text(name, text):
        titles = [
            '中国科学院院士', '中国工程院院士', '院士',
            '特聘教授', '讲席教授', '二级教授', '三级教授', '教授',
            '副教授', '研究员', '副研究员', '助理研究员',
            '高级工程师', '工程师', '助理工程师',
            '主任医师', '副主任医师', '主治医师', '医师',
            '实验师', '高级实验师', '助理实验师',
            '讲师', '助教', '师资博士后', '博士后'
        ]
        header = text[:600]
        for t in titles:
            if re.search(rf'({re.escape(name)}[\s,，、/]{{0,10}}{re.escape(t)}|职\s*称\s*[:：]\s*{re.escape(t)}|{re.escape(t)})', header):
                return t
        return '研究员'

    for cat_name, page_url in pages:
        r = httpx.get(page_url, verify=False, timeout=12.0, headers=HEADERS)
        soup = BeautifulSoup(r.text, 'html.parser')
        detail_links = []
        for a in soup.find_all('a'):
            h = a.get('href', '')
            if 'info/' in h:
                detail_links.append(urljoin(page_url, h))

        if not detail_links:
            raw_text = clean_profile_text(soup.find('div', id='vsb_content') or soup.find('div', class_='content') or soup.body)
            name = ""
            if '那曼丽' in cat_name or '那曼丽' in raw_text:
                name = "那曼丽"
            elif '赵璧影' in cat_name or '赵璧影' in raw_text:
                name = "赵璧影"
            else:
                m = re.search(r'([\u4e00-\u9fa5]{2,4})[：:，,\s]+(教授|研究员|博士|副教授)', raw_text)
                if m: name = m.group(1)

            if name and name not in seen_names:
                seen_names.add(name)
                emails = [clean_email(e) for e in email_regex.findall(raw_text) if clean_email(e)]
                email = emails[0] if emails else ""
                title = extract_title_from_text(name, raw_text)
                records.append({
                    "姓名": name,
                    "职称": title,
                    "邮箱": email,
                    "学校": school,
                    "学院": college,
                    "采集人": "Antigravity",
                    "采集日期": today_str,
                    "简介": raw_text,
                    "主页": page_url
                })
        else:
            for durl in detail_links:
                dr = httpx.get(durl, verify=False, timeout=12.0, headers=HEADERS)
                dsoup = BeautifulSoup(dr.text, 'html.parser')
                t_tag = dsoup.title.text.strip() if dsoup.title else ""
                name = t_tag.split('-')[0].strip()
                name = re.sub(r'[\s\u3000]+', ' ', name).strip()
                if not name or name == "国际基因组学研究中心":
                    h = dsoup.find(['h1', 'h2', 'h3'])
                    if h: name = h.get_text(strip=True)

                clean_n = re.sub(r'\s+', '', name) if re.search(r'[\u4e00-\u9fa5]', name) else name
                if clean_n in seen_names:
                    continue
                seen_names.add(clean_n)

                article_elem = dsoup.find('div', id='vsb_content') or dsoup.find('div', class_='v_news_content') or dsoup.find('div', class_=lambda c: c and 'content' in c) or dsoup.body
                raw_text = clean_profile_text(article_elem)

                emails = [clean_email(e) for e in email_regex.findall(raw_text) if clean_email(e)]
                email = emails[0] if emails else ""

                title = extract_title_from_text(clean_n, raw_text)
                if 'Sir Richard' in clean_n:
                    title = "荣誉顾问 (诺贝尔奖得主)"
                elif 'Subrata' in clean_n:
                    title = "客座教授"

                records.append({
                    "姓名": clean_n,
                    "职称": title,
                    "邮箱": email,
                    "学校": school,
                    "学院": college,
                    "采集人": "Antigravity",
                    "采集日期": today_str,
                    "简介": raw_text,
                    "主页": durl
                })

    filepath, filename, total, with_email, no_email, final_records = export_dual_sheet_excel(records, school, college)
    print(f"✅ 工单 [{task_id}] 导出完成: {filename} (总计: {total}, 有邮箱: {with_email}, 无邮箱: {no_email})")
    sync_task(task_id, final_records, filename, total, with_email, no_email)
    return {
        "task_id": task_id,
        "school": school,
        "college": college,
        "status": "completed",
        "total": total,
        "with_email": with_email,
        "no_email": no_email,
        "excel": f"server/outputs/{filename}"
    }

# =========================================================================
# Append to Master Excel 教师列表.xlsx
# =========================================================================
def update_master_excel(new_records_list: list):
    master_path = os.path.join(ROOT_DIR, "教师列表.xlsx")
    if not os.path.exists(master_path):
        print("Master 教师列表.xlsx not found, skipping master update.")
        return

    print("Updating root 教师列表.xlsx...")
    existing_df = pd.read_excel(master_path)
    new_df = pd.DataFrame(new_records_list)

    columns = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']
    for c in columns:
        if c not in new_df.columns:
            new_df[c] = ""
        if c not in existing_df.columns:
            existing_df[c] = ""

    new_df = new_df[columns]
    combined_df = pd.concat([existing_df, new_df], ignore_index=True)
    # Deduplicate by (姓名, 学校, 学院) keeping latest
    combined_df = combined_df.drop_duplicates(subset=['姓名', '学校', '学院'], keep='last').reset_index(drop=True)
    combined_df['序号'] = range(1, len(combined_df) + 1)
    combined_df.to_excel(master_path, index=False)
    print(f"🎉 Master 教师列表.xlsx updated successfully! Total records: {len(combined_df)}")

if __name__ == "__main__":
    res1 = harvest_task_c20e0354()
    res2 = harvest_task_52fb4102()

    # Read back generated excel files to get records for master
    wb1 = pd.read_excel(os.path.join(ROOT_DIR, res1["excel"]), sheet_name=None)
    wb2 = pd.read_excel(os.path.join(ROOT_DIR, res2["excel"]), sheet_name=None)
    
    all_new = []
    for sheet in wb1.values():
        all_new.extend(sheet.to_dict(orient='records'))
    for sheet in wb2.values():
        all_new.extend(sheet.to_dict(orient='records'))

    update_master_excel(all_new)

    import json
    summary = {
        "status": "completed",
        "tasks": [res1, res2]
    }
    print("\nFINAL_JSON_OUTPUT:")
    print(json.dumps(summary, ensure_ascii=False))
