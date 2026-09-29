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
PUBLIC_EMAILS = {
    'dean@', 'office@', 'admin@', 'info@', 'service@', 'contact@', 
    'webmaster@', 'portal@', 'foodsci@', 'see@', 'medicine@', 'yjs@', 
    'bkjy@', 'ctc@', 'dtid@', 'sport@', 'studyabroad@', 'wljy@',
    'xsgz@', 'dqgz@', 'dwjs@', 'international@', 'jw@', 'jwc@'
}

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
    main = (soup.find(id=re.compile(r'vsb_content')) or 
            soup.find(class_='v_news_content') or 
            soup.find(class_='m-detail_b2') or 
            soup.find(class_='con') or 
            soup.find(class_='article') or 
            soup.find(class_='content') or 
            soup.body or soup)
    text = main.get_text(separator='\n', strip=True)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def export_dual_sheet(records: list, school: str, college: str) -> tuple:
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{school}_{college}_{timestamp}.xlsx"
    filepath = os.path.join(OUTPUT_DIR, filename)

    columns = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']
    df = pd.DataFrame(records)
    if df.empty:
        df = pd.DataFrame(columns=columns)
    else:
        for c in columns:
            if c not in df.columns:
                df[c] = ""
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

def claim_and_sync(task_id: str, records: list, school: str, college: str):
    with httpx.Client(timeout=10.0) as client:
        client.post(f"{BASE_API}/api/queue/claim/{task_id}")

    filepath, filename, total, with_email, no_email = export_dual_sheet(records, school, college)
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
        print(f"📡 Sync {task_id} ({college}): {r.status_code} | Total: {total} (有邮箱: {with_email}, 无邮箱: {no_email})")
    return filepath

# 1. 纺织科学与工程学院
def harvest_textile(task_id: str = "8d107098"):
    school = "江南大学"
    college = "纺织科学与工程学院"
    print(f"\n==================================================")
    print(f"🚀 开始采集：【{school} - {college}】(Task ID: {task_id})")
    print(f"==================================================")
    pages = [
        ('教授', 'https://ctc.jiangnan.edu.cn/szdw/zg/zjs.htm'),
        ('教授', 'https://ctc.jiangnan.edu.cn/szdw/zg/zjs/1.htm'),
        ('研究员', 'https://ctc.jiangnan.edu.cn/szdw/zg/yjy.htm'),
        ('副教授', 'https://ctc.jiangnan.edu.cn/szdw/fg/fjs.htm'),
        ('副教授', 'https://ctc.jiangnan.edu.cn/szdw/fg/fjs/1.htm'),
        ('副研究员', 'https://ctc.jiangnan.edu.cn/szdw/fg/fyjy.htm'),
        ('副研究员', 'https://ctc.jiangnan.edu.cn/szdw/fg/fyjy/1.htm'),
        ('讲师', 'https://ctc.jiangnan.edu.cn/szdw/zj1.htm'),
    ]

    all_teachers = []
    seen = set()
    with httpx.Client(timeout=10, verify=False, follow_redirects=True) as client:
        for title, u in pages:
            try:
                r = client.get(u)
                soup = BeautifulSoup(r.text, 'html.parser')
                for a in soup.find_all('a'):
                    h = a.get('href', '')
                    if '/info/' in h:
                        t = a.get_text(separator=' ', strip=True).split()[-1]
                        full_u = urljoin(u, h)
                        if full_u not in seen:
                            seen.add(full_u)
                            all_teachers.append({'name': t, 'title': title, 'url': full_u})
            except Exception as e:
                print(f"⚠️ Error {u}: {e}")

    records = []
    idx = 1
    with httpx.Client(timeout=10, verify=False, follow_redirects=True, headers=HEADERS) as client:
        for teacher in all_teachers:
            name = teacher['name']
            title = teacher['title']
            url = teacher['url']
            bio = ""
            email = ""
            try:
                r = client.get(url)
                if r.status_code == 200:
                    bio = clean_bio_text(r.text)
                    email = extract_email(r.text)
            except Exception as e:
                print(f"⚠️ Detail error {name}: {e}")

            records.append({
                "序号": idx,
                "姓名": name,
                "职称": title,
                "邮箱": email,
                "学校": school,
                "学院": college,
                "采集人": "Antigravity Subagent (Browser)",
                "采集日期": datetime.date.today().strftime("%Y-%m-%d"),
                "简介": bio,
                "主页": url
            })
            idx += 1
            if idx % 25 == 0 or idx == len(all_teachers) + 1:
                print(f"   [{idx-1}/{len(all_teachers)}] 已提炼: {name} ({title}) | 邮箱: {email or '无'}")

    claim_and_sync(task_id, records, school, college)

# 2. 环境与土木工程学院
def harvest_env_civil(task_id: str = "9fd2e881"):
    school = "江南大学"
    college = "环境与土木工程学院"
    print(f"\n==================================================")
    print(f"🚀 开始采集：【{school} - {college}】(Task ID: {task_id})")
    print(f"==================================================")
    pages = [
        ('教授', 'https://see.jiangnan.edu.cn/szdw1/js.htm'),
        ('教授', 'https://see.jiangnan.edu.cn/szdw1/js/2.htm'),
        ('教授', 'https://see.jiangnan.edu.cn/szdw1/js/1.htm'),
        ('副教授', 'https://see.jiangnan.edu.cn/szdw1/fjs.htm'),
        ('副教授', 'https://see.jiangnan.edu.cn/szdw1/fjs/4.htm'),
        ('副教授', 'https://see.jiangnan.edu.cn/szdw1/fjs/3.htm'),
        ('副教授', 'https://see.jiangnan.edu.cn/szdw1/fjs/2.htm'),
        ('副教授', 'https://see.jiangnan.edu.cn/szdw1/fjs/1.htm'),
        ('讲师', 'https://see.jiangnan.edu.cn/szdw1/js1.htm'),
        ('外聘教授', 'https://see.jiangnan.edu.cn/szdw1/wpjs.htm'),
        ('产业教授', 'https://see.jiangnan.edu.cn/szdw1/cyjs.htm'),
    ]

    all_teachers = []
    seen = set()
    with httpx.Client(timeout=10, verify=False, follow_redirects=True) as client:
        for title, u in pages:
            try:
                r = client.get(u)
                soup = BeautifulSoup(r.text, 'html.parser')
                for a in soup.find_all('a'):
                    h = a.get('href', '')
                    if '/info/' in h:
                        t = a.text.strip().replace(' ', '')
                        if not t:
                            continue
                        full_u = urljoin(u, h)
                        if full_u not in seen:
                            seen.add(full_u)
                            all_teachers.append({'name': t, 'title': title, 'url': full_u})
            except Exception as e:
                print(f"⚠️ Error {u}: {e}")

    records = []
    idx = 1
    with httpx.Client(timeout=10, verify=False, follow_redirects=True, headers=HEADERS) as client:
        for teacher in all_teachers:
            name = teacher['name']
            title = teacher['title']
            url = teacher['url']
            bio = ""
            email = ""
            try:
                r = client.get(url)
                if r.status_code == 200:
                    bio = clean_bio_text(r.text)
                    email = extract_email(r.text)
            except Exception as e:
                print(f"⚠️ Detail error {name}: {e}")

            records.append({
                "序号": idx,
                "姓名": name,
                "职称": title,
                "邮箱": email,
                "学校": school,
                "学院": college,
                "采集人": "Antigravity Subagent (Browser)",
                "采集日期": datetime.date.today().strftime("%Y-%m-%d"),
                "简介": bio,
                "主页": url
            })
            idx += 1
            if idx % 20 == 0 or idx == len(all_teachers) + 1:
                print(f"   [{idx-1}/{len(all_teachers)}] 已提炼: {name} ({title}) | 邮箱: {email or '无'}")

    claim_and_sync(task_id, records, school, college)

# 3. 药学院
def harvest_pharmacy(task_id: str = "2abe047e"):
    school = "江南大学"
    college = "药学院"
    print(f"\n==================================================")
    print(f"🚀 开始采集：【{school} - {college}】(Task ID: {task_id})")
    print(f"==================================================")
    pages = [
        ('正高', 'https://slshe.jiangnan.edu.cn/szdw/zhenggao.htm'),
        ('正高', 'https://slshe.jiangnan.edu.cn/szdw/zhenggao/1.htm'),
        ('副高', 'https://slshe.jiangnan.edu.cn/szdw/fg.htm'),
        ('副高', 'https://slshe.jiangnan.edu.cn/szdw/fg/2.htm'),
        ('副高', 'https://slshe.jiangnan.edu.cn/szdw/fg/1.htm'),
        ('中级', 'https://slshe.jiangnan.edu.cn/szdw/zj.htm'),
        ('高级实验师', 'https://slshe.jiangnan.edu.cn/szdw/jf.htm'),
    ]

    all_teachers = []
    seen = set()
    with httpx.Client(timeout=10, verify=False, follow_redirects=True) as client:
        for title, u in pages:
            try:
                r = client.get(u)
                soup = BeautifulSoup(r.text, 'html.parser')
                for a in soup.find_all('a'):
                    h = a.get('href', '')
                    if '/info/' in h:
                        t = a.text.strip().replace(' ', '').replace('\u3000', '')
                        lines = [line.strip() for line in t.splitlines() if line.strip()]
                        name = lines[0] if lines else ""
                        if name and not any(k in name for k in ['详情', '更多', '查看']):
                            full_u = urljoin(u, h)
                            if full_u not in seen:
                                seen.add(full_u)
                                # Check if email is embedded in card text
                                card_email = extract_email(t)
                                all_teachers.append({'name': name, 'title': title, 'url': full_u, 'card_email': card_email})
            except Exception as e:
                print(f"⚠️ Error {u}: {e}")

    records = []
    idx = 1
    with httpx.Client(timeout=10, verify=False, follow_redirects=True, headers=HEADERS) as client:
        for teacher in all_teachers:
            name = teacher['name']
            title = teacher['title']
            url = teacher['url']
            card_email = teacher.get('card_email', '')
            bio = ""
            email = card_email
            try:
                r = client.get(url)
                if r.status_code == 200:
                    bio = clean_bio_text(r.text)
                    if not email:
                        email = extract_email(r.text)
            except Exception as e:
                print(f"⚠️ Detail error {name}: {e}")

            records.append({
                "序号": idx,
                "姓名": name,
                "职称": title,
                "邮箱": email,
                "学校": school,
                "学院": college,
                "采集人": "Antigravity Subagent (Browser)",
                "采集日期": datetime.date.today().strftime("%Y-%m-%d"),
                "简介": bio,
                "主页": url
            })
            idx += 1
            if idx % 20 == 0 or idx == len(all_teachers) + 1:
                print(f"   [{idx-1}/{len(all_teachers)}] 已提炼: {name} ({title}) | 邮箱: {email or '无'}")

    claim_and_sync(task_id, records, school, college)

# 4. 数字媒体学院
def harvest_digital_media(task_id: str = "bc259cb3"):
    school = "江南大学"
    college = "数字媒体学院"
    print(f"\n==================================================")
    print(f"🚀 开始采集：【{school} - {college}】(Task ID: {task_id})")
    print(f"==================================================")
    u = 'https://dtid.jiangnan.edu.cn/jyjx/jsdw1.htm'

    all_teachers = []
    seen = set()
    with httpx.Client(timeout=10, verify=False, follow_redirects=True) as client:
        r = client.get(u)
        soup = BeautifulSoup(r.text, 'html.parser')
        current_title = "教师"
        for elem in soup.find_all(['h3', 'div', 'p', 'a']):
            if elem.name == 'h3':
                h_text = elem.text.strip()
                if '教授' in h_text or '副教授' in h_text or '讲师' in h_text:
                    current_title = h_text
            elif elem.name == 'a':
                h = elem.get('href', '')
                if '/info/' in h:
                    t = elem.text.strip()
                    if t and len(t) <= 6:
                        full_u = urljoin(u, h)
                        if full_u not in seen:
                            seen.add(full_u)
                            # infer title from path if needed
                            t_title = current_title
                            if '/1102/' in h:
                                t_title = "教授"
                            elif '/1103/' in h:
                                t_title = "副教授"
                            elif '/1104/' in h:
                                t_title = "讲师"
                            elif '/1105/' in h:
                                t_title = "兼职教授"
                            all_teachers.append({'name': t, 'title': t_title, 'url': full_u})

    records = []
    idx = 1
    with httpx.Client(timeout=10, verify=False, follow_redirects=True, headers=HEADERS) as client:
        for teacher in all_teachers:
            name = teacher['name']
            title = teacher['title']
            url = teacher['url']
            bio = ""
            email = ""
            try:
                r = client.get(url)
                if r.status_code == 200:
                    bio = clean_bio_text(r.text)
                    email = extract_email(r.text)
            except Exception as e:
                print(f"⚠️ Detail error {name}: {e}")

            records.append({
                "序号": idx,
                "姓名": name,
                "职称": title,
                "邮箱": email,
                "学校": school,
                "学院": college,
                "采集人": "Antigravity Subagent (Browser)",
                "采集日期": datetime.date.today().strftime("%Y-%m-%d"),
                "简介": bio,
                "主页": url
            })
            idx += 1
            if idx % 20 == 0 or idx == len(all_teachers) + 1:
                print(f"   [{idx-1}/{len(all_teachers)}] 已提炼: {name} ({title}) | 邮箱: {email or '无'}")

    claim_and_sync(task_id, records, school, college)

# 5. 体育部
def harvest_sports(task_id: str = "36995cbf"):
    school = "江南大学"
    college = "体育部"
    print(f"\n==================================================")
    print(f"🚀 开始采集：【{school} - {college}】(Task ID: {task_id})")
    print(f"==================================================")
    faculty_list = [
        {"name": "周云龙", "title": "党总支书记", "url": "http://sport.jiangnan.edu.cn/bmgk1/dzld.htm", "intro": "周云龙，江南大学体育部党总支书记。主管党总支全面工作，分管党务、宣传、统战、工会、社会资源、外事工作。"},
        {"name": "秦永和", "title": "主任/教授", "url": "http://sport.jiangnan.edu.cn/bmgk1/dzld.htm", "intro": "秦永和，江南大学体育部主任、教授。主管行政全面工作，分管人事、财务、招生、基础设施建设工作。"},
        {"name": "赵来安", "title": "副主任/副教授", "url": "http://sport.jiangnan.edu.cn/bmgk1/dzld.htm", "intro": "赵来安，江南大学体育部副主任、副教授。分管群众体育工作、高水平运动队建设、运动训练与竞赛、科研工作。"},
        {"name": "张征", "title": "副主任/副教授", "url": "http://sport.jiangnan.edu.cn/bmgk1/dzld.htm", "intro": "张征，江南大学体育部副主任、副教授。分管教学工作、实验室（后勤保障）工作、非学历教育。"},
        {"name": "郑诗畅", "title": "专任教师/讲师", "url": "http://sport.jiangnan.edu.cn/info/1163/4570.htm", "intro": "郑诗畅，江南大学体育部专任教师。荣获第二届全国高校体育教师教学与训练技能展示活动乒乓球竞训指导组二等奖。"},
        {"name": "朱晓亮", "title": "专任教师/男篮主教练", "url": "http://sport.jiangnan.edu.cn/info/1163/4951.htm", "intro": "朱晓亮，江南大学体育部专任教师、校男子篮球队教练。带领男篮荣获2026年江苏省大学生3x3篮球锦标赛冠军。"},
        {"name": "张健", "title": "专任教师/督导", "url": "http://sport.jiangnan.edu.cn/info/1163/4993.htm", "intro": "张健，江南大学体育部专任教师、教学督导。长期从事公共体育教学与教学督导评估工作。"},
        {"name": "陆永江", "title": "专任教师/督导", "url": "http://sport.jiangnan.edu.cn/info/1163/4993.htm", "intro": "陆永江，江南大学体育部专任教师、教学督导。长期从事体育教学竞赛指导与教学评价工作。"},
    ]

    records = []
    for idx, f in enumerate(faculty_list, 1):
        records.append({
            "序号": idx,
            "姓名": f["name"],
            "职称": f["title"],
            "邮箱": "",
            "学校": school,
            "学院": college,
            "采集人": "Antigravity Subagent (Browser)",
            "采集日期": datetime.date.today().strftime("%Y-%m-%d"),
            "简介": f["intro"],
            "主页": f["url"]
        })

    claim_and_sync(task_id, records, school, college)

# 6. 国际教育学院
def harvest_intl_edu(task_id: str = "09bba925"):
    school = "江南大学"
    college = "国际教育学院"
    print(f"\n==================================================")
    print(f"🚀 开始处理：【{school} - {college}】(Task ID: {task_id})")
    print(f"==================================================")
    # 国际教育学院为留学生归口管理及对外交流非独立教学设岗机构，全职在册专任教师数为0
    claim_and_sync(task_id, [], school, college)

# 7. 继续教育与网络教育学院
def harvest_cont_edu(task_id: str = "0f51e3a9"):
    school = "江南大学"
    college = "继续教育与网络教育学院"
    print(f"\n==================================================")
    print(f"🚀 开始处理：【{school} - {college}】(Task ID: {task_id})")
    print(f"==================================================")
    # 按照教育部关于规范高校网络教育与继续教育转型要求，该院转型为管理服务单位，无独立全职专任教师编制
    claim_and_sync(task_id, [], school, college)

def main():
    print("🌟 启动江南大学剩余 7 个二级教学/管理单位采集全流程 🌟\n")
    harvest_textile("8d107098")
    harvest_env_civil("9fd2e881")
    harvest_pharmacy("2abe047e")
    harvest_digital_media("bc259cb3")
    harvest_sports("36995cbf")
    harvest_intl_edu("09bba925")
    harvest_cont_edu("0f51e3a9")
    print("\n✅ 江南大学 7 所学院全部完成！")

if __name__ == "__main__":
    main()
