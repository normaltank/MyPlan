import json
import os
import re

# Load the raw profiles
with open(r'C:\Users\27451\.gemini\antigravity\brain\760874c3-398e-40b7-9814-716e7b6c2ea7\.system_generated\steps\243\output.txt', 'r', encoding='utf-8') as f:
    raw_content = f.read()

# Extract json
json_start = raw_content.find('{"total":57')
decoder = json.JSONDecoder()
data, _ = decoder.raw_decode(raw_content[json_start:])

# Load OCR emails
with open('parsed_ocr_emails.json', 'r', encoding='utf-8') as f:
    ocr_emails = json.load(f)

yue_intro = "岳嘉，副教授，硕士生导师，中组部第十二批“西部之光”访问学者。现任甘肃中医药大学公共卫生学院副院长。中国营养学会会员，甘肃省预防医学会会员。主持国家自然基金1项，主持或参与国家级、省部级、厅局级等科研课题 10 余项，申请国家专利 1 项。先后获得甘肃省高等学校科研优秀成果一等奖1项、兰州市城关区科技进步三等奖1项、甘肃省高等教育教学成果奖二等奖1项，校级教学成果一等奖2项，二等奖1项。发表教育教学、科研论文 20余篇，参编国家级规划教材副主编1部，编委4部。荣获甘肃省大中专学生暑期社会实践活动优秀指导教师、甘肃中医药大学优秀教育工作者、优秀教师等荣誉称号。"
zhao_intro = "赵翊，男，副教授，硕士生导师，国家注册安全工程师，公共卫生主管医师，甘肃省安全生产专家库成员，甘肃省卫生健康委员会政策研究专家咨询委员会委员，环境与职业医学教研室主任。主持或参与完成各级各类项目10余项，以第一作者公开发表论文8篇，出版专著1部，历获兰州市科技进步二等奖1项，甘肃省教育厅优秀成果奖一等奖、二等奖3项，甘肃省皇普谧科技进步奖4项。2017、2018连续2年获甘肃省科技统计先进个人。\n研究方向：环境、职业与健康。"

processed = []
with_email = 0
no_email = 0

for item in data['results']:
    name = item['name']
    title = item['title']
    url = item['url']
    intro = item['intro']
    
    if name == '岳嘉' and not intro:
        intro = yue_intro
    elif name == '赵翊' and not intro:
        intro = zhao_intro
        
    email = item.get('emailInText', '').strip()
    if not email:
        email = ocr_emails.get(name, '').strip()
        
    if email:
        with_email += 1
    else:
        no_email += 1
        
    processed.append({
        '序号': len(processed) + 1,
        '姓名': name,
        '职称': title,
        '邮箱': email,
        '学校': '甘肃中医药大学',
        '学院': '公共卫生学院',
        '采集人': 'Antigravity',
        '采集日期': '2026-09-24',
        '简介': intro,
        '主页': url
    })

print(f"Total: {len(processed)}, With Email: {with_email}, No Email: {no_email}")
with open('processed_teachers.json', 'w', encoding='utf-8') as f:
    json.dump(processed, f, ensure_ascii=False, indent=2)
