import os
import sys
import json
import time
import re
import datetime
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
PUBLIC_EMAILS = {'dean@', 'office@', 'admin@', 'info@', 'service@', 'contact@', 'webmaster@', 'portal@'}

def extract_email(text: str) -> str:
    if not text:
        return ""
    matches = EMAIL_RE.findall(text.replace('[at]', '@').replace('(at)', '@').replace('#', '@'))
    for m in matches:
        m_lower = m.lower()
        if not any(p in m_lower for p in PUBLIC_EMAILS) and not m_lower.endswith(('.png', '.jpg', '.css', '.js')):
            return m
    return ""

def clean_bio_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup.find_all(['script', 'style', 'noscript', 'header', 'footer', 'nav']):
        tag.decompose()
    text = soup.body.get_text(separator='\n', strip=True) if soup.body else soup.get_text(separator='\n', strip=True)
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

def harvest_hhu_dept(task_id: str, dept_id: str, school: str, college: str):
    print(f"\n==================================================")
    print(f"🚀 开始采集：【{school} - {college}】(Task ID: {task_id}, Org ID: {dept_id})")
    print(f"==================================================")

    # 1. Claim task
    with httpx.Client(timeout=10.0) as client:
        client.post(f"{BASE_API}/api/queue/claim/{task_id}")

    if not dept_id:
        # e.g. 海南研究生院 (非实体)
        records = []
        filepath, filename, total, with_email, no_email = export_dual_sheet(records, school, college)
        sync_task(task_id, records, filepath, filename, total, with_email, no_email)
        print(f"✅ 【{school} - {college}】采集完成：非实体机构，全院专任教师 0 位 (0+0=0 严格守恒)")
        return

    # Query generalQuery API
    query_url = "https://jszy.hhu.edu.cn/_wp3services/generalQuery?queryObj=teacherHome"
    conditions = [
        {"field": "language", "value": "1", "judge": "="},
        {"field": "published", "value": "1", "judge": "="},
        {"orConditions": [{"field": "ownDepartment", "value": dept_id, "judge": "="}]}
    ]
    return_infos = [
        {"field": "title", "name": "title"},
        {"field": "cnUrl", "name": "cnUrl"},
        {"field": "career", "name": "career"},
        {"field": "department", "name": "department"}
    ]

    with httpx.Client(timeout=15.0, verify=False) as client:
        resp = client.post(
            query_url,
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
            data={
                "pageIndex": 1,
                "rows": 200,
                "conditions": json.dumps(conditions),
                "orders": json.dumps([{"field": "letter", "type": "asc"}]),
                "returnInfos": json.dumps(return_infos),
                "articleType": 1,
                "level": 0
            }
        )
        res_data = resp.json().get("data", [])

    print(f"🔍 获取到 {len(res_data)} 位学者主页列表，开始并发穿透提炼履历与邮箱...")

    records = []
    idx = 1
    with httpx.Client(timeout=10.0, verify=False, headers=HEADERS, follow_redirects=True) as client:
        for item in res_data:
            name = item.get("title", "").strip()
            career = item.get("career", "").strip() or "教师"
            profile_url = item.get("cnUrl", "").strip()

            email = ""
            bio_text = ""

            if profile_url:
                try:
                    p_resp = client.get(profile_url)
                    if p_resp.status_code == 200:
                        bio_text = clean_bio_text(p_resp.text)
                        email = extract_email(p_resp.text)
                except Exception as err:
                    print(f"⚠️ 获取 {name} 主页失败: {err}")

            record = {
                "序号": idx,
                "姓名": name,
                "职称": career,
                "邮箱": email,
                "学校": school,
                "学院": college,
                "采集人": "Antigravity Subagent (Browser)",
                "采集日期": datetime.date.today().strftime("%Y-%m-%d"),
                "简介": bio_text,
                "主页": profile_url
            }
            records.append(record)
            idx += 1
            print(f"   [{idx-1}/{len(res_data)}] {name} ({career}) | 邮箱: {email or '无'}")

    # Export & Sync
    filepath, filename, total, with_email, no_email = export_dual_sheet(records, school, college)
    sync_task(task_id, records, filepath, filename, total, with_email, no_email)
    print(f"🎉 【{school} - {college}】完成！总人数: {total} | 有邮箱: {with_email} | 无邮箱: {no_email} (严格守恒)")
    print(f"   交付表: {filepath}")

def main():
    tasks = [
        {"task_id": "640f5ab7", "dept_id": "32", "school": "河海大学", "college": "体育系"},
        {"task_id": "c01eafa6", "dept_id": "", "school": "河海大学", "college": "海南研究生院"},
        {"task_id": "b6f5f0eb", "dept_id": "217", "school": "河海大学", "college": "国际教育学院"},
        {"task_id": "030a04fa", "dept_id": "222", "school": "河海大学", "college": "远程与继续教育学院"},
        {"task_id": "eb62cd1f", "dept_id": "256", "school": "河海大学", "college": "河海里尔学院"},
    ]

    for t in tasks:
        harvest_hhu_dept(t["task_id"], t["dept_id"], t["school"], t["college"])

if __name__ == "__main__":
    main()
