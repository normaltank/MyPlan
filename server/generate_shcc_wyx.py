import os
import sys
import io
import json
import urllib.request
from datetime import datetime
import pandas as pd
import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

RECORDS = [
    {
        "姓名": "王志军",
        "职称": "教授",
        "邮箱": "13371896126@163.com",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "王志军，男，上海海关学院海关外语系主任兼党支部书记、教授，全面主管系部各项工作。办公室：志学楼A305，电话：021-28991116。主要研究方向包括海关英语、应用语言学、翻译理论与实践等。曾任教务处处长、海关英语语料库与翻译创新团队负责人，主持完成国家社科基金项目“基于汉英平行语料库的汉英动宾搭配对比研究”、上海市教委2023年本科重点教改项目“基于外语课程群的思政育人体系探索”等国家级与省部级课题多项，长期指导学生参加“外研社·国才杯”“理解当代中国”全国大学生外语能力大赛并屡获国家级奖项，在海关人才培养、MTI（翻译硕士）学科建设及大学英语教学改革等方面成果丰硕。",
        "主页": "https://wyx.shcc.edu.cn/xbld/list.htm"
    },
    {
        "姓名": "胡兴文",
        "职称": "副教授",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "胡兴文，上海海关学院海关外语系副主任兼党支部副书记、副教授，分管系教学工作。办公室：志学楼A304，联系电话：021-28991126。长期从事英语专业教学与科研工作，主要研究领域为翻译理论与实践、英语专业翻译教学、应用语言学等。主持与参与多项校级和省部级教学改革课题，发表多篇翻译学与外语教学研究论文，深度参与海关外语系翻译硕士（MTI）课程体系构建与本科教学评估工作。",
        "主页": "https://wyx.shcc.edu.cn/xbld/list.htm"
    },
    {
        "姓名": "胡宗利",
        "职称": "副主任",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "胡宗利，上海海关学院海关外语系副主任，分管学生工作。办公室：志学楼A302，联系电话：021-28991024。长期在上海海关学院从事学生思想政治教育、外事交流与海关业务研修管理工作。曾参与世界海关组织（WCO）公约审议会议相关技术工作、海关总署援外培训及涉外交流项目，具有丰富的高校学生工作与涉关涉外管理经验。",
        "主页": "https://wyx.shcc.edu.cn/xbld/list.htm"
    },
    {
        "姓名": "李祝玛",
        "职称": "系办公室主任",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "李祝玛，上海海关学院海关外语系办公室主任。办公室：志学楼A307，联系电话：021-28991122。全面负责海关外语系行政事务、人事协调、资产管理、教务考务组织、对外联络及新闻信息发布等综合管理工作，积极保障系部日常运转与教学科研各项业务顺利推进。",
        "主页": "https://wyx.shcc.edu.cn/xbgs/list.htm"
    },
    {
        "姓名": "戴雯娴",
        "职称": "系秘书",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "戴雯娴，上海海关学院海关外语系秘书。办公室：志学楼A306，联系电话：021-28991708。主要负责海关外语系日常教学秘书、教务管理、研究生培养与学术活动组织协调、档案整理及系务公开等行政支撑工作。",
        "主页": "https://wyx.shcc.edu.cn/xbgs/list.htm"
    },
    {
        "姓名": "郭晓群",
        "职称": "副教授",
        "邮箱": "guoxiaoqun@shcc.edu.cn",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "郭晓群，博士，上海海关学院海关外语系大学英语教研室主任、副教授。电子邮箱：guoxiaoqun@shcc.edu.cn。曾入选上海高校青年教师培养资助计划，主要研究方向为大学英语教学理论与实践、跨境贸易与海关数字化、应用语言学等。主持与参与多项校级和市级教学改革项目，多次组织并推进海关外语系大学英语分级教学改革、集体备课与审核评估教学研讨活动。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "杨茂娟",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "杨茂娟，上海海关学院海关外语系大学英语教研室专任教师。长期坚守外语本科教学一线，主讲大学英语系列核心课程。积极参与大学英语教学模式创新与教研活动，悉心指导学生参加全国大学生英语竞赛（NECCS）等各级外语赛事并屡获佳绩，深得师生好评。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "安全勇",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "安全勇，上海海关学院海关外语系实验中心主任、大学英语教研室教师。主要负责外语语音实验室、多媒体语言教学中心与智慧外语教学系统的建设、维护与管理，同时承担外语教学工作。参与多项外语教学改革研究，并参与编写《童话故事》等多部图书著作成果。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "初春玲",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "初春玲，上海海关学院海关外语系大学英语教研室专任教师。拥有多年高校外语一线教学经验，主讲大学英语精读、泛读与听力等课程。积极投身课堂教学改革与网络课程资源建设，多次参与教研室集体备课与教学研讨，专注于提升学生综合英语应用能力与跨文化沟通素养。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "陈涛",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "陈涛，上海海关学院海关外语系大学英语教研室专任教师。长期承担全校大学英语公共基础课的教学与教研工作。在混合式教学、数字化外语课堂建设及在线课程探索方面积累了丰富经验，积极开展以学生为中心的教学模式创新，教学作风扎实严谨。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "杨上",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "杨上，复旦大学英语语言文学专业博士，英国伯明翰大学硕士，上海海关学院海关外语系讲师、硕士生导师。主要研究方向为英汉双语词典编纂、术语研究、海关涉外话语分析等。曾受邀代表中国海关专家参加中俄SPS工作组第十四次会议；荣获第十五届、第十六届“外教社杯”全国高校外语教学大赛（外语类专业组）上海市赛二等奖与三等奖；承担《英语国家社会与文化》《海关英语》等专业课程教学与MTI研究生指导工作。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "陶小雨",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "陶小雨，英语语言文学博士，上海海关学院海关外语系大学英语教研室教师。主要研究方向为翻译学、副文本研究、文学翻译与译者主体性等。在国家级翻译学科权威期刊《中国翻译》发表重要学术论文《许渊冲莎剧称谓语汉译的女性形象构建与译者主体性探析》；荣获第十六届“外教社杯”全国高校外语教学大赛上海市赛二等奖，在青年教师科研与教学竞赛中表现优异。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "孙晓蓉",
        "职称": "副教授",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "孙晓蓉，上海大学语言文化与世界文明博士，上海海关学院海关外语系大学英语教研室专任教师（专业技术高级岗位）。主要研究方向为外国语言文学、比较文学、文化跨学科传播与外语教学理论。拥有扎实的学术造诣与丰富的一线教学经验，长期致力于高校英语课程思政与跨文化人文素养培养。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "马鸣皓",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "马鸣皓，上海外国语大学英语语言文学博士，上海海关学院海关外语系大学英语教研室专任教师。主要研究方向为英美文学、英语语言理论与外语教育教学。具有深厚的专业知识底蕴，积极参与大学英语教学团队建设与外语专业课程改革，在全国翻译学术交流与教学研讨中多有探索。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "周远航",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "周远航，上海交通大学外国语学院语言学博士，上海海关学院海关外语系大学英语教研室专任教师。主要从事语言学、文学翻译批评、翻译风格学及当代外语研究。在《当代外语研究》等学术平台发表多篇高质量学术论文，注重语言学实证方法与外语教学融合创新。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "李端阳",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "李端阳，英语语言文学博士，上海海关学院海关外语系海关英语教研室主任、翻译硕士（MTI）导师。主要研究领域为二语习得实证研究、海关学术英语、海关新闻英语语料库多维分析及跨境电商关务英语。在《现代外语》《中国外语》等学术刊物发表学术论文，主持教育部产学合作协同育人项目及多项校级科研项目，荣获上海海关学院教师教学创新大赛重要奖项，长期指导学生在大学生创新大赛中获奖。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "郑继正",
        "职称": "副教授",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "郑继正，上海海关学院海关外语系副教授，曾任外语系主任助理。长期从事语言学、应用语言学、英汉翻译及海关专用英语研究。主编出版我国权威关务辞书《英汉关务词典》（法律出版社出版），主持建设2024年度上海市高校市级重点课程《海关学术英语Ⅰ-Ⅱ》，在《外语教学理论与实践》《海关与经贸研究》发表学术论文数十篇，长期参与世界海关组织（WCO）文献及海关总署重大涉外公文汉英翻译与审校。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "王燕",
        "职称": "副教授",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "王燕，博士，上海海关学院海关外语系副教授。主要研究方向为跨文化交际、海关涉外商务英语、英美文学研究等。主持2022年度上海高校市级重点课程《跨文化交际》、2025年海关高等教育优秀教学成果奖课程思政示范项目《跨文化商务交际》及校级重点教改课题多项；指导学生荣获第五届“外教社杯”上海高校跨文化能力大赛二等奖等佳绩；在国内外知名学术刊物发表学术论文多篇。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "邱海颖",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "邱海颖，上海海关学院海关外语系海关英语教研室专任教师。主讲海关英语、涉关商务英语阅读与写作等课程。长期专注于高校涉关外语人才培养，参与编写海关英语相关教材与教参资料，积极组织教研活动与外语第二课堂竞赛辅导。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "贾晓哲",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "贾晓哲，上海海关学院海关外语系海关英语教研室专任教师。自进入上海海关学院任教以来，长期坚守一线本科课堂，主讲《大学英语》《海关专业英语》等多门核心课程。教学作风严谨负责，深受学生爱戴，多次在校级教学评估与课表教务考核中名列前茅。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "李芳兰",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "李芳兰，上海海关学院海关外语系海关英语教研室专任教师。主要从事商务英语教学与涉关商贸实务研究。长期指导学生团队参加国家级高水平学科竞赛，曾带队斩获全国高校“亿学杯”商务英语实践技能大赛沪津赛区第一名与全国总决赛特等奖，主持和参与多项校级外语教学改革研究课题。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "管小繁",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "管小繁，上海海关学院海关外语系海关英语教研室专任教师。主要从事英语教学法、时政文献翻译及海关涉外文献编译。主编及参编《国际海关动态》等权威专业参考著作；积极开展基于PBL与支架式教学的大学英语与海关英语课堂模式改革，多次在海关英语教研室开展“时政话语汉英翻译”专题研讨。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "王婷婷",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "王婷婷，上海海关学院海关外语系海关英语教研室专任教师。主讲海关英语听说、实用涉外英语交际等课程。积极投身海关外语系本科生专业认知教育与课外学术辅导，多次指导学生参与国家级与省部级外语能力竞赛并获得优秀成绩。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    },
    {
        "姓名": "胡妤",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海海关学院",
        "学院": "外语学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "胡妤，上海外国语大学英语语言文学博士，上海海关学院海关外语系海关英语教研室专任教师。主要研究方向为翻译理论与实践、外宣翻译规范与海关对外话语传播。著有学术专著《描写译学视域下的外宣翻译规范研究——以China Today为例》（上海交通大学出版社），出版译著《进口至美国——商业进口指南》，发表《汉语诗词中隐性美的传递》等多篇核心学术论文，长期参与世界海关组织（WCO）外宣文献翻译与编译工作。",
        "主页": "https://wyx.shcc.edu.cn/2025/0905/c1572a66453/page.htm"
    }
]

def main():
    task_id = "294ecd8a"
    school = "上海海关学院"
    college = "外语学院"
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{school}_{college}_{timestamp}.xlsx"
    out_dir = os.path.join(os.path.dirname(__file__), "outputs")
    os.makedirs(out_dir, exist_ok=True)
    filepath = os.path.join(out_dir, filename)

    df = pd.DataFrame(RECORDS)
    df["序号"] = range(1, len(df) + 1)
    
    # 保证标准10列
    cols = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']
    df = df[cols]

    # 分流双Sheet
    has_email_mask = df['邮箱'].fillna('').str.strip().str.len() > 0
    df_with_email = df[has_email_mask].copy().reset_index(drop=True)
    df_no_email = df[~has_email_mask].copy().reset_index(drop=True)

    df_with_email['序号'] = range(1, len(df_with_email) + 1)
    df_no_email['序号'] = range(1, len(df_no_email) + 1)

    sheet1_name = f"有邮箱学者 (共 {len(df_with_email)} 位)"
    sheet2_name = f"无邮箱学者 (共 {len(df_no_email)} 位)"

    with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
        df_with_email.to_excel(writer, sheet_name=sheet1_name, index=False)
        df_no_email.to_excel(writer, sheet_name=sheet2_name, index=False)

    # 样式精细化渲染
    wb = openpyxl.load_workbook(filepath)
    header_fill = PatternFill(start_color="F1F5F9", end_color="F1F5F9", fill_type="solid")
    header_font = Font(name="Microsoft YaHei", size=11, bold=True, color="1E293B")
    data_font = Font(name="Microsoft YaHei", size=10, color="334155")
    thin_border = Border(
        left=Side(style='thin', color='E2E8F0'),
        right=Side(style='thin', color='E2E8F0'),
        top=Side(style='thin', color='E2E8F0'),
        bottom=Side(style='thin', color='E2E8F0')
    )

    for ws in wb.worksheets:
        ws.views.sheetView[0].showGridLines = True
        for row_idx, row in enumerate(ws.iter_rows(), start=1):
            for cell in row:
                cell.border = thin_border
                if row_idx == 1:
                    cell.fill = header_fill
                    cell.font = header_font
                    cell.alignment = Alignment(horizontal='center', vertical='center')
                else:
                    cell.font = data_font
                    if cell.column in [1, 2, 3, 5, 6, 7, 8]:
                        cell.alignment = Alignment(horizontal='center', vertical='center')
                    else:
                        cell.alignment = Alignment(horizontal='left', vertical='center')

        for col in ws.columns:
            col_letter = get_column_letter(col[0].column)
            max_len = 0
            for cell in col[:15]:
                val_str = str(cell.value or '')
                max_len = max(max_len, len(val_str.encode('gbk', 'ignore')))
            ws.column_dimensions[col_letter].width = min(max(max_len + 4, 12), 50)

    wb.save(filepath)
    print(f"🎉 Standard Dual-Sheet Excel saved to: {filepath}")
    print(f"  {sheet1_name}")
    print(f"  {sheet2_name}")
    print(f"  Total teachers: {len(df)} (Strictly Conserved: {len(df_with_email)} + {len(df_no_email)} = {len(df)})")
    
    # 4. Sync with Backend
    sync_url = f"http://127.0.0.1:8000/api/task/{task_id}/sync"
    sync_payload = {
        "records": RECORDS,
        "stats": {
            "total": len(RECORDS),
            "with_email": len(df_with_email),
            "no_email": len(df_no_email),
            "email_rate": round(len(df_with_email) / len(RECORDS) * 100, 1) if RECORDS else 0
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
            print(f"✅ Sync API Response: {resp_data}")
    except Exception as e:
        print(f"❌ Sync API Error: {e}")

if __name__ == "__main__":
    main()
