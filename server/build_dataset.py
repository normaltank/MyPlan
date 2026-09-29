import json
import os
import re
import sys
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')

# 1. Load data from step 156 and step 220
with open(r'C:\Users\27451\.gemini\antigravity\brain\5f1bc082-23da-425e-a997-78c28d986d3d\.system_generated\steps\156\output.txt', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line.startswith('{"totalFetched":'):
            data156 = json.loads(line)
            break

with open(r'C:\Users\27451\.gemini\antigravity\brain\5f1bc082-23da-425e-a997-78c28d986d3d\.system_generated\steps\220\output.txt', 'r', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line.startswith('### Result'):
            continue
        if line.startswith('['):
            data220 = json.loads(line)
            break

all_items = data156['items']
# Merge data220
existing_ids = set(x['id'] for x in all_items)
for it in data220:
    if it['id'] not in existing_ids:
        all_items.append({
            'id': it['id'],
            'rawName': it['name'],
            'name': it['name'],
            'title': '',
            'email': '',
            'url': f"https://hlxy.gszy.edu.cn/show_teacher/id/{it['id']}.html",
            'contentLen': len(it['content']),
            'content': it['content']
        })

print(f"Total merged items: {len(all_items)}")

# Title extraction hierarchy
TITLES_ORDER = [
    '教授', '副教授', '讲师', '助教',
    '主任护师', '副主任护师', '主管护师', '护师', '主任护士', '副主任护士',
    '主任医师', '副主任医师', '主治医师', '医师',
    '研究员', '副研究员', '助理研究员',
    '高级实验师', '实验师'
]

def extract_title(name, content):
    if name == '李学军':
        return '党委书记'
    if name == '杨洪宝':
        return '党委副书记'
    
    # Check early in text (first 300 chars usually has the primary title)
    early_text = content[:300]
    for t in TITLES_ORDER:
        if t in early_text:
            # normalize主任护士/副主任护士 to 主任护师/副主任护师
            if t == '主任护士': return '主任护师'
            if t == '副主任护士': return '副主任护师'
            return t
            
    # Then check full text
    for t in TITLES_ORDER:
        if t in content:
            if t == '主任护士': return '主任护师'
            if t == '副主任护士': return '副主任护师'
            return t
    return '教师'

def extract_email(content):
    # Regex to capture email stopping before Chinese characters or punctuation
    m = re.search(r'([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', content)
    if not m:
        return ''
    raw = m.group(1)
    # Strip any trailing non-ascii chars or unwanted punctuation
    raw = re.sub(r'[\u4e00-\u9fa5].*$', '', raw)
    raw = raw.rstrip('.,;:)，。；）')
    # Valid email format
    if re.match(r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$', raw):
        return raw
    return ''

# Group by teacher name
from collections import defaultdict
grouped = defaultdict(list)
for it in all_items:
    content = it['content'].strip()
    raw_name = it['rawName'].replace(' ', '')
    name = raw_name
    
    # Handle placeholder name '导师简介'
    if name == '导师简介' or not name:
        if '王波' in content[:50]:
            name = '王波'
        elif '许瑞' in content[:50]:
            name = '许瑞'
        elif '李芳' in content[:50]:
            name = '李芳'
        elif '王艳波' in content[:50]:
            name = '王艳波'
        else:
            continue
    
    grouped[name].append((it['id'], content, it['url']))

print(f"Unique grouped teachers: {len(grouped)}")

unique_teachers = []
for name, profiles in sorted(grouped.items()):
    # Pick the longest / most comprehensive profile
    best_id, best_content, best_url = max(profiles, key=lambda x: len(x[1]))
    
    title = extract_title(name, best_content)
    email = extract_email(best_content)
    
    # Also check if any other profile for this teacher has an email
    if not email:
        for _, other_content, _ in profiles:
            em = extract_email(other_content)
            if em:
                email = em
                break
                
    unique_teachers.append({
        '姓名': name,
        '职称': title,
        '邮箱': email,
        '学校': '甘肃中医药大学',
        '学院': '护理学院',
        '采集人': 'Antigravity',
        '采集日期': '2026-09-24',
        '简介': best_content,
        '主页': best_url
    })

# Add the 13 faculty members from show/id/391.html (专任教师队伍质量)
t391_data = [
    {
        "姓名": "崔宇红",
        "职称": "教授",
        "学历": "在职研究生/硕士",
        "专业": "护理学",
        "学术头衔": "1.甘肃省护理学会护理教育专业委员会副主任委员 2.世界中医药联合会护理专业委员会理事",
        "海外经历": ""
    },
    {
        "姓名": "李丹琳",
        "职称": "教授",
        "学历": "本科/学士",
        "专业": "护理学",
        "学术头衔": "1.中医儿科杂志编委和审稿专家 2.中西医结合护理（中英文）期刊编委和审稿专家 3.甘肃中医药大学学报编委和审稿专家",
        "海外经历": ""
    },
    {
        "姓名": "肖丽英",
        "职称": "副教授",
        "学历": "本科/学士",
        "专业": "护理学",
        "学术头衔": "",
        "海外经历": ""
    },
    {
        "姓名": "闫兰",
        "职称": "副教授",
        "学历": "本科/学士",
        "专业": "护理学",
        "学术头衔": "",
        "海外经历": ""
    },
    {
        "姓名": "吴晶",
        "职称": "副教授",
        "学历": "在职研究生/硕士",
        "专业": "内科学",
        "学术头衔": "1.中国老年医学学会院校教育分会第一届委员会委员 2.民盟甘肃省第十四届医疗工作委员会委员 3.中国老年保健协会医养结合人才专业委员会委员",
        "海外经历": ""
    },
    {
        "姓名": "王金霞",
        "职称": "副教授",
        "学历": "研究生/硕士",
        "专业": "内科学",
        "学术头衔": "",
        "海外经历": ""
    },
    {
        "姓名": "赵丽霞",
        "职称": "讲师",
        "学历": "研究生/硕士",
        "专业": "护理学",
        "学术头衔": "",
        "海外经历": ""
    },
    {
        "姓名": "张珺",
        "职称": "讲师",
        "学历": "研究生/硕士",
        "专业": "护理学",
        "学术头衔": "",
        "海外经历": ""
    },
    {
        "姓名": "杜娟",
        "职称": "讲师",
        "学历": "研究生/硕士",
        "专业": "护理学",
        "学术头衔": "",
        "海外经历": ""
    },
    {
        "姓名": "范琳琳",
        "职称": "讲师",
        "学历": "研究生/硕士",
        "专业": "护理学",
        "学术头衔": "1.甘肃省护理学会儿科护理专业委员会委员 2.中华护理学会儿科护理专业委员会委员",
        "海外经历": ""
    },
    {
        "姓名": "方蕾",
        "职称": "讲师",
        "学历": "研究生/硕士",
        "专业": "护理学",
        "学术头衔": "1.中国生命关怀协会人文护理专委会委员 2.甘肃省护理学会护理心理专委会委员",
        "海外经历": ""
    },
    {
        "姓名": "马晓婷",
        "职称": "讲师",
        "学历": "研究生/硕士",
        "专业": "护理学",
        "学术头衔": "",
        "海外经历": ""
    },
    {
        "姓名": "裴霄",
        "职称": "助教",
        "学历": "研究生/硕士",
        "专业": "护理学",
        "学术头衔": "",
        "海外经历": ""
    }
]

existing_names = set(t['姓名'] for t in unique_teachers)
for f in t391_data:
    if f['姓名'] not in existing_names:
        intro_parts = [f"甘肃中医药大学护理学院专任教师，职称：{f['职称']}，学历/学位：{f['学历']}，专业：{f['专业']}。"]
        if f['学术头衔']:
            intro_parts.append(f"学术头衔：{f['学术头衔']}。")
        if f['海外经历']:
            intro_parts.append(f"海外经历：{f['海外经历']}。")
        intro = "".join(intro_parts)
        unique_teachers.append({
            '姓名': f['姓名'],
            '职称': f['职称'],
            '邮箱': '',
            '学校': '甘肃中医药大学',
            '学院': '护理学院',
            '采集人': 'Antigravity',
            '采集日期': '2026-09-24',
            '简介': intro,
            '主页': 'https://hlxy.gszy.edu.cn/show/id/391.html'
        })

print(f"Total final faculty records: {len(unique_teachers)}")
with_email = [t for t in unique_teachers if t['邮箱']]
no_email = [t for t in unique_teachers if not t['邮箱']]
print(f"With email: {len(with_email)}")
print(f"No email: {len(no_email)}")
for t in with_email:
    print(f"  {t['姓名']}: {t['职称']} | {t['邮箱']} | {t['主页']}")
