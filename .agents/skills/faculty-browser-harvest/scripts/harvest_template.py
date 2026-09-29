"""
Faculty Browser Harvest Helper Template
Standard execution script for harvesting college faculty data and exporting to dual-sheet Excel.
"""

import sys
import io
import re
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup
import pandas as pd
import openpyxl
import urllib3

urllib3.disable_warnings()

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

EMAIL_RE = re.compile(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)')
PUBLIC_EMAILS = {'dean@', 'office@', 'admin@', 'info@', 'service@', 'contact@', 'webmaster@', 'admission@', 'yjs@'}

def extract_clean_email(text: str) -> str:
    if not text:
        return ""
    normalized = text.replace('[at]', '@').replace('(at)', '@').replace('#', '@')
    matches = EMAIL_RE.findall(normalized)
    for m in matches:
        m_lower = m.lower()
        if not any(p in m_lower for p in PUBLIC_EMAILS) and not m_lower.endswith(('.png', '.jpg', '.jpeg', '.gif')):
            return m
    return ""

def export_dual_sheet_excel(records: list, output_xlsx: str, output_csv: str = None, agent_name: str = "BrowserAgent", date_str: str = "2026-09-20"):
    df = pd.DataFrame(records)
    if df.empty:
        print("No records to export.")
        return
    
    # Fill defaults
    if '采集人' not in df.columns:
        df['采集人'] = agent_name
    if '采集日期' not in df.columns:
        df['采集日期'] = date_str
    
    # Deduplicate by (姓名, 学校, 学院)
    df['bio_len'] = df['简介'].fillna('').astype(str).str.len()
    df['has_email'] = df['邮箱'].fillna('').astype(str).apply(lambda x: 1 if x.strip() else 0)
    df = df.sort_values(by=['has_email', 'bio_len'], ascending=False)
    df = df.drop_duplicates(subset=['姓名', '学校', '学院'], keep='first')
    df = df.drop(columns=['bio_len', 'has_email'])
    
    df = df.sort_values(by=['学校', '学院', '姓名']).reset_index(drop=True)
    
    # Split
    df_with_email = df[df['邮箱'].fillna('').astype(str).str.strip() != ""].copy().reset_index(drop=True)
    df_no_email = df[df['邮箱'].fillna('').astype(str).str.strip() == ""].copy().reset_index(drop=True)
    
    df_with_email['序号'] = range(1, len(df_with_email) + 1)
    df_no_email['序号'] = range(1, len(df_no_email) + 1)
    
    columns = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']
    for c in columns:
        if c not in df_with_email.columns:
            df_with_email[c] = ""
        if c not in df_no_email.columns:
            df_no_email[c] = ""
            
    df_with_email = df_with_email[columns]
    df_no_email = df_no_email[columns]
    
    with pd.ExcelWriter(output_xlsx, engine='openpyxl') as writer:
        df_with_email.to_excel(writer, sheet_name='教师列表', index=False)
        df_no_email.to_excel(writer, sheet_name='无邮箱教师列表', index=False)
    
    if output_csv:
        df_all = pd.concat([df_with_email, df_no_email]).reset_index(drop=True)
        df_all['序号'] = range(1, len(df_all) + 1)
        df_all.to_csv(output_csv, index=False, encoding='utf-8-sig')
        
    print(f"Exported successfully to {output_xlsx}")
    print(f"  Sheet 1 (有邮箱): {len(df_with_email)} 位")
    print(f"  Sheet 2 (无邮箱): {len(df_no_email)} 位")
    print(f"  总计: {len(df_with_email) + len(df_no_email)} 位 (严格守恒)")
