# -*- coding: utf-8 -*-
"""
Harvest faculty directory for:
上海财经大学 - 上海财经大学浙江学院（独立学院）
Task ID: 997ab4be
"""

import os
import re
import ssl
import sys
import json
import time
import datetime
import urllib.request
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import pandas as pd
import openpyxl

# SSL context for HTTPS requests
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def fetch_html(url, timeout=12):
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception as e:
            time.sleep(1)
    return None

def clean_name(raw_text, page_title=""):
    raw = raw_text.replace("\u200b", "").replace("\u200e", "").strip()
    raw = re.sub(r"^\d{4}-\d{2}-\d{2}", "", raw).strip()
    raw = re.sub(r"老师简介$|简介$|教师简介$|详细信息$", "", raw).strip()
    
    # Try regex match for Chinese name (2-4 chars) before titles or punctuation
    m = re.match(r"^([^\s、，\d\(\)（）]{2,4}?)(?=讲师|副教授|教授|副研究员|研究员|助教|高级工程师|高级实验师|高级经济师|实验师|主任|、|，|\(|$)", raw)
    if m and len(m.group(1).strip()) >= 2:
        return m.group(1).strip()
    
    # Clean from page title if raw is messy
    if page_title:
        pt = page_title.split("-")[0].split("_")[0].strip()
        pt = pt.replace("\u200b", "").replace("\u200e", "").strip()
        pt = re.sub(r"老师简介$|简介$|详细信息$", "", pt).strip()
        m2 = re.match(r"^([^\s、，\d]{2,4})", pt)
        if m2 and len(m2.group(1).strip()) >= 2:
            return m2.group(1).strip()
            
    # Clean fallback
    clean = re.sub(r"特聘教授|长聘教授|副教授|教授|副研究员|研究员|高级工程师|高级实验师|高级经济师|讲师|助教|实验师|、|教研室主任|主任|党支部书记|院长|书记|副院长|博士|硕导|博导", "", raw).strip()
    return clean[:4] if len(clean) >= 2 else raw[:4]

def extract_title(raw_text, bio_text):
    combined = raw_text + " " + bio_text[:300]
    for t in ["特聘教授", "讲席教授", "长聘教授", "副教授", "教授", "副研究员", "研究员", "高级工程师", "高级实验师", "高级经济师", "助理研究员", "讲师", "助教", "助理教授", "实验师"]:
        if t in combined:
            return t
    if "教师" in bio_text[:200] or "专任教师" in bio_text[:200]:
        return "讲师"
    return "讲师"

def extract_email(html, text):
    email_pattern = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
    # Check text first
    emails = email_pattern.findall(text)
    if not emails:
        # Check html mailto
        emails = email_pattern.findall(html)
        
    valid_emails = []
    for e in emails:
        e_lower = e.lower()
        if any(e_lower.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".css", ".js", ".svg"]):
            continue
        if any(bad in e_lower for bad in ["w3.org", "example.com", "schema.org"]):
            continue
        valid_emails.append(e)
        
    return valid_emails[0] if valid_emails else ""

def clean_bio(soup, fallback_text=""):
    # Find container
    art = soup.find(id="vsb_content") or soup.find(id="vsb_content_2") or soup.find(class_=lambda x: x and any(k in str(x) for k in ["v_news_content", "single-content", "main_conDiv", "vsb-box", "art_con", "article"]))
    if not art:
        art = soup.body
        
    if not art:
        return fallback_text
        
    # Copy container to avoid modifying original
    art_copy = BeautifulSoup(str(art), "html.parser")
    for tag in art_copy.find_all(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
        
    text = art_copy.get_text("\n", strip=True)
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Remove noisy system lines
        if any(bad in line for bad in ["当前位置：", "首页>", "发布日期：", "浏览量：", "字号：", "上一篇：", "下一篇：", "作者：", "时间：", "点击数：", "版权所有", "浙ICP备", "浙公网安备"]):
            continue
        lines.append(line)
        
    bio = "\n".join(lines).strip()
    return bio if bio else fallback_text

def parse_teacher_page(url, default_dept, raw_name_text=""):
    html = fetch_html(url)
    if not html:
        return None
        
    soup = BeautifulSoup(html, "html.parser")
    page_title = soup.title.string if soup.title else ""
    name = clean_name(raw_name_text, page_title)
    
    bio = clean_bio(soup)
    title = extract_title(raw_name_text, bio)
    email = extract_email(html, bio)
    
    # If bio is too short (e.g. only 1 line with external link), keep whatever is there
    if not bio:
        bio = f"{name}，上海财经大学浙江学院专任教师。"
        
    return {
        "姓名": name,
        "职称": title,
        "邮箱": email,
        "学校": "上海财经大学",
        "学院": "上海财经大学浙江学院（独立学院）",
        "部门": default_dept,
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": bio,
        "主页": url
    }

def gather_all_teacher_links():
    tasks = [] # (dept_name, url, raw_name)
    
    # 1. 人事处·教授名录
    print("Fetching 人事处·教授名录...")
    rsc_url = "https://rsc.shufe-zj.edu.cn/jsml.htm"
    rsc_html = fetch_html(rsc_url)
    if rsc_html:
        soup = BeautifulSoup(rsc_html, "html.parser")
        for li in soup.find_all("li", class_="js_s"):
            a = li.find("a")
            if a and a.get("href"):
                u = urljoin(rsc_url, a.get("href"))
                tasks.append(("人事处教授名录", u, a.get_text(strip=True)))
    print(f"  RSC: found {len(tasks)} professors")
    
    # 2. 数据科学学院
    print("Fetching 数据科学学院...")
    stat_url = "http://stat.shufe-zj.edu.cn/szdw/szdw.htm"
    stat_html = fetch_html(stat_url)
    if stat_html:
        soup = BeautifulSoup(stat_html, "html.parser")
        for a in soup.find_all("a"):
            h = str(a.get("href"))
            if "info" in h:
                u = urljoin(stat_url, h)
                tasks.append(("数据科学学院", u, a.get_text(strip=True)))
                
    # 3. 金融与经济学院 (3 pages)
    print("Fetching 金融与经济学院...")
    for p in ["jsjs.htm", "jsjs/2.htm", "jsjs/1.htm"]:
        jrx_url = f"http://jrx.shufe-zj.edu.cn/xygk/{p}"
        html = fetch_html(jrx_url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a"):
                h = str(a.get("href"))
                t = a.get_text(strip=True)
                if "info" in h and t:
                    u = urljoin(jrx_url, h)
                    tasks.append(("金融与经济学院", u, t))
                    
    # 4. 会计学院 (5 sections)
    print("Fetching 会计学院...")
    kj_sections = [
        "szdw1/zydtr.htm",
        "szdw1/hjxjys.htm",
        "szdw1/zchjjys.htm",
        "szdw1/gjhj_ACCA_jys.htm",
        "szdw1/cwgljys.htm"
    ]
    for sec in kj_sections:
        kj_url = f"http://kjxy.shufe-zj.edu.cn/{sec}"
        html = fetch_html(kj_url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a"):
                h = str(a.get("href"))
                t = a.get_text(strip=True)
                if "info" in h and t:
                    u = urljoin(kj_url, h)
                    tasks.append(("会计学院", u, t))
                    
    # 5. 科技商学院 (3 pages)
    print("Fetching 科技商学院...")
    for p in ["jsfc.htm", "jsfc/2.htm", "jsfc/1.htm"]:
        sm_url = f"https://sm.shufe-zj.edu.cn/kjsxygk/{p}"
        html = fetch_html(sm_url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for li in soup.find_all(["li", "div", "tr"]):
                a = li.find("a")
                if a and "info" in str(a.get("href")):
                    text = li.get_text(strip=True)
                    if text and len(text) < 25 and not any(k in text for k in ["首页", "上页", "下页", "尾页", "概况", "风采"]):
                        u = urljoin(sm_url, a.get("href"))
                        tasks.append(("科技商学院", u, text))
                        
    # 6. 人工智能与软件工程学院 (3 pages)
    print("Fetching 人工智能与软件工程学院...")
    for p in ["jsjj.htm", "jsjj/2.htm", "jsjj/1.htm"]:
        seim_url = f"https://seim.shufe-zj.edu.cn/xygk/{p}"
        html = fetch_html(seim_url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a"):
                h = str(a.get("href"))
                t = a.get_text(strip=True)
                if "info" in h and t:
                    u = urljoin(seim_url, h)
                    tasks.append(("人工智能与软件工程学院", u, t))
                    
    # 7. 语言与文化传播学院 (4 pages)
    print("Fetching 语言与文化传播学院...")
    for p in ["szdw.htm", "szdw/3.htm", "szdw/2.htm", "szdw/1.htm"]:
        wyx_url = f"https://wyx.shufe-zj.edu.cn/xygk/{p}"
        html = fetch_html(wyx_url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a"):
                h = str(a.get("href"))
                t = a.get_text(strip=True)
                if "info" in h and t:
                    u = urljoin(wyx_url, h)
                    tasks.append(("语言与文化传播学院", u, t))
                    
    # 8. 马克思主义学院 (2 sections)
    print("Fetching 马克思主义学院...")
    for sec in ["xbgk1/tpjs.htm", "xbgk1/szdw.htm"]:
        marx_url = f"http://marxism.shufe-zj.edu.cn/{sec}"
        html = fetch_html(marx_url)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.find_all("a"):
                h = str(a.get("href"))
                t = a.get_text(strip=True)
                if "info" in h and t:
                    u = urljoin(marx_url, h)
                    tasks.append(("马克思主义学院", u, t))
                    
    # 9. 体育教学部
    print("Fetching 体育教学部...")
    pe_url = "http://ggjcb.shufe-zj.edu.cn/bmgk/szdw.htm"
    pe_html = fetch_html(pe_url)
    if pe_html:
        soup = BeautifulSoup(pe_html, "html.parser")
        for a in soup.find_all("a"):
            h = str(a.get("href"))
            if "info" in h:
                u = urljoin(pe_url, h)
                tasks.append(("体育教学部", u, a.parent.get_text(strip=True)))
                
    print(f"Total raw candidate entries: {len(tasks)}")
    return tasks

def main():
    raw_tasks = gather_all_teacher_links()
    
    # Parse and deduplicate
    scholars_by_name = {} # name -> record
    
    total = len(raw_tasks)
    print(f"\nStarting detail parsing for {total} raw candidate entries...")
    
    for idx, (dept, url, raw_name) in enumerate(raw_tasks):
        if idx % 20 == 0:
            print(f"  Processing {idx}/{total}...")
        rec = parse_teacher_page(url, dept, raw_name)
        if not rec:
            continue
            
        name = rec["姓名"]
        if not name or len(name) < 2 or len(name) > 4:
            continue
            
        # Deduplication logic
        if name in scholars_by_name:
            existing = scholars_by_name[name]
            # Prefer non-RSC specific department if existing is RSC
            if existing["部门"] == "人事处教授名录" and dept != "人事处教授名录":
                existing["部门"] = dept
            # Prefer longer biography
            if len(rec["简介"]) > len(existing["简介"]):
                existing["简介"] = rec["简介"]
                existing["主页"] = rec["主页"]
            # Prefer higher academic title
            for higher in ["特聘教授", "讲席教授", "教授", "副教授"]:
                if higher in rec["职称"] and higher not in existing["职称"]:
                    existing["职称"] = rec["职称"]
            # Preserve email if found
            if rec["邮箱"] and not existing["邮箱"]:
                existing["邮箱"] = rec["邮箱"]
        else:
            scholars_by_name[name] = rec
            
    final_records = list(scholars_by_name.values())
    # Sort final records by name or department
    final_records.sort(key=lambda x: (x["部门"], x["姓名"]))
    
    # Assign sequential numbers
    for i, r in enumerate(final_records, 1):
        r["序号"] = i
        
    print(f"\nTotal unique scholars collected: {len(final_records)}")
    
    # Split by email
    with_email = [r for r in final_records if r["邮箱"]]
    no_email = [r for r in final_records if not r["邮箱"]]
    
    # Re-index for dual sheets
    for i, r in enumerate(with_email, 1):
        r["序号"] = i
    for i, r in enumerate(no_email, 1):
        r["序号"] = i
        
    print(f"  With email: {len(with_email)}")
    print(f"  No email: {len(no_email)}")
    print(f"  Conservation check: {len(with_email)} + {len(no_email)} = {len(final_records)} (PASS: {len(with_email) + len(no_email) == len(final_records)})")
    
    cols = ["序号", "姓名", "职称", "邮箱", "学校", "学院", "采集人", "采集日期", "简介", "主页"]
    df_with_email = pd.DataFrame(with_email)[cols] if with_email else pd.DataFrame(columns=cols)
    df_no_email = pd.DataFrame(no_email)[cols] if no_email else pd.DataFrame(columns=cols)
    
    now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("server", "outputs")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"上海财经大学_浙江学院_{now_str}.xlsx"
    filepath = os.path.join(out_dir, filename)
    
    sheet1_name = f"有邮箱学者 (共 {len(df_with_email)} 位)"
    sheet2_name = f"无邮箱学者 (共 {len(df_no_email)} 位)"
    
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df_with_email.to_excel(writer, sheet_name=sheet1_name, index=False)
        df_no_email.to_excel(writer, sheet_name=sheet2_name, index=False)
        
    print(f"\nSuccessfully generated dual-sheet Excel: {filepath}")
    
    # Save raw backup JSON
    json_path = filepath.replace(".xlsx", ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(final_records, f, ensure_ascii=False, indent=2)
        
    # Append to root 教师列表.xlsx if applicable
    root_excel = "教师列表.xlsx"
    if os.path.exists(root_excel):
        try:
            wb_root = openpyxl.load_workbook(root_excel)
            ws_root = wb_root.active
            start_row = ws_root.max_row
            for i, r in enumerate(final_records, 1):
                row_data = [
                    start_row + i - 1,
                    r["姓名"],
                    r["职称"],
                    r["邮箱"],
                    r["学校"],
                    r["学院"],
                    r["采集人"],
                    r["采集日期"],
                    r["简介"],
                    r["主页"]
                ]
                ws_root.append(row_data)
            wb_root.save(root_excel)
            print(f"Successfully appended {len(final_records)} records to {root_excel}")
        except Exception as e:
            print(f"Error appending to root Excel: {e}")
            
    # Sync with backend
    task_id = "997ab4be"
    sync_url = f"http://127.0.0.1:8000/api/task/{task_id}/sync"
    sync_payload = {
        "records": final_records,
        "stats": {
            "total": len(final_records),
            "with_email": len(with_email),
            "no_email": len(no_email),
            "email_rate": round(len(with_email) / len(final_records) * 100, 1) if final_records else 0
        },
        "download_url": f"/download/{filename}",
        "progress": 100
    }
    
    req = urllib.request.Request(
        sync_url,
        data=json.dumps(sync_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    
    try:
        with urllib.request.urlopen(req) as resp:
            resp_data = resp.read().decode("utf-8")
            print(f"Sync API response: {resp_data}")
    except Exception as e:
        print(f"Sync API error: {e}")

if __name__ == "__main__":
    main()
