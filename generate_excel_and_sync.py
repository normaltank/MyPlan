import os
import sys
import io
import datetime
import urllib.request
import json
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# Ensure output directory exists
os.makedirs('server/outputs', exist_ok=True)

now_str = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
date_today = datetime.datetime.now().strftime('%Y-%m-%d')
file_name = f"上海外国语大学_海外合作学院_{now_str}.xlsx"
output_path = os.path.join('server', 'outputs', file_name).replace('\\', '/')

teachers_data = [
    {
        "姓名": "汪小玲",
        "职称": "教授（博士生导师）",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学出国留学人员培训部主任、海外合作学院院长、教授、博士生导师。上海外国语大学英语语言文学专业博士，英国利兹大学TESOL访问进修，美国哈佛大学富布赖特访问学者。独立或合作出版英美文学与文化研究专著7部，发表论文60余篇，其中CSSCI期刊近40篇。主讲雅思写作与口语、学术英语及英美文学文化等课程。",
        "主页": "http://tcos.shisu.edu.cn/5200/list.htm"
    },
    {
        "姓名": "梅德明",
        "职称": "教授（博士生导师）",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学教授、博士生导师，国家教材委员会专家委员、教育部高中及义务教育英语课程标准修订组组长。美国宾州印第安纳大学博士。长期从事语言学、外语教育与国家外语政策规划研究。著有《现代语言学》《现代句法学》等专著教材60余部，主编国家级规划教材10余套，曾获国家级教学成果二等奖及宝钢优秀教师奖。",
        "主页": "http://tcos.shisu.edu.cn/17/fd/c5212a137213/page.htm"
    },
    {
        "姓名": "于飞",
        "职称": "副教授",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学副教授，曾任出国留学人员培训部主任。获加拿大渥太华大学教育硕士学位及卡尔顿大学TESL证书，曾在英国伯明翰大学与美国南加州大学访学。主研雅思口语与跨文化交际，编著《海外留学英语口语教程》《PETS备考教程5级》等教材辞书多部，曾获宝钢优秀教师奖与教育部优秀教师称号。",
        "主页": "http://tcos.shisu.edu.cn/17/fe/c5212a137214/page.htm"
    },
    {
        "姓名": "盛建元",
        "职称": "副教授",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学副教授，曾任出国留学人员培训部英语教研室主任。英国纽卡斯尔大学媒体技术辅助外语教学法硕士。深耕商务英语沟通、学术英语与演讲艺术，主编《大中小学一条龙英语教材编写原则与方法研究》《大学英语写作强化训练教程》等7部45卷教材，承担项目获国家级教学成果二等奖及上海市高校教学成果一等奖。",
        "主页": "http://tcos.shisu.edu.cn/18/01/c5212a137217/page.htm"
    },
    {
        "姓名": "陈立青",
        "职称": "副教授（硕士生导师）",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学出国留学人员培训部副主任、海外合作学院副院长、副教授、硕士生导师。上海外国语大学英语博士，英国华威大学应用语言学硕士，美国斯坦福大学访问学者。出版专著《英语阅读的后方法教学模式研究》，参与国家语委学生外语能力标准研究，主讲雅思、托福、公派出国听说读写及学术英语。",
        "主页": "http://tcos.shisu.edu.cn/18/07/c5212a137223/page.htm"
    },
    {
        "姓名": "朱玉山",
        "职称": "副教授（硕士生导师）",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学出国留学人员培训部副主任、海外合作学院副院长、副教授、硕士生导师。上海外国语大学英语语言文学博士，英国雷丁大学与美国肯特州立大学访问学者。出版专著译著5部，在《外国语》等发表论文多篇，主编大中小学教材参编超350万字，主持及参与国家社科基金和上海市重点项目多项，主讲雅思及学术英语。",
        "主页": "http://tcos.shisu.edu.cn/18/08/c5212a137224/page.htm"
    },
    {
        "姓名": "乔龙宝",
        "职称": "副教授（硕士生导师）",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学副教授、硕士生导师。上海外国语大学英语学士与硕士，英国布里斯托大学教育学硕士，上海财经大学经济管理博士，澳大利亚堪培拉大学访问学者。在核心刊物发表多篇关于成人英语学习策略、译者心智模型及品牌资产管理的论文，擅长英语阅读、写作、听力与口语等全方位教学。",
        "主页": "http://tcos.shisu.edu.cn/18/0b/c5212a137227/page.htm"
    },
    {
        "姓名": "陈佳",
        "职称": "副教授（硕士生导师）",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学副教授、硕士生导师、教研室主任。上外博士，英国剑桥大学访问学者。在SSCI、CSSCI等期刊发表论文近30篇，出版著作译著5部，主持教育部人文社科青年基金及上海市立德树人重点项目，任国际儿童语言学习研究协会（ELLRA）专家委员。主讲雅思、学术英语与认知语言学课程。",
        "主页": "http://tcos.shisu.edu.cn/18/0f/c5212a137231/page.htm"
    },
    {
        "姓名": "郝景洁",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学讲师、出国留学人员培训部及海外合作学院教研室副主任。上海外国语大学翻译方向硕士，国家公派英国约克大学与美国迈阿密大学访问学者。译有《汉字与人体》《城市取样1×1》，编著《海外留学英语阅读教程》《PETS5备考教程》等，擅长英语听力、阅读、写作与翻译教学。",
        "主页": "http://tcos.shisu.edu.cn/18/1e/c5212a137246/page.htm"
    },
    {
        "姓名": "陈怡",
        "职称": "副教授（硕士生导师）",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学副教授、硕士生导师、雅思课程协调人。上海外国语大学英语语言文学博士，英国剑桥大学及美国宾州印第安纳大学访问学者。在《外语教学理论与实践》《外语电化教学》等核心期刊发表多篇论文，著有《影视语料与当代英语口语研究》，长期主讲雅思听力、阅读与写作课程。",
        "主页": "http://tcos.shisu.edu.cn/18/10/c5212a137232/page.htm"
    },
    {
        "姓名": "陈健",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学讲师、日语项目教学负责人。上海外国语大学日语系学士、硕士，专业方向为日语语法，曾赴日本庆应义塾大学研修。发表《强化语法教学中的新尝试》《寒暖表现》等学术论文，主讲日语文法与实际交际运用，注重引导学生掌握地道自然的日语思维与语言表达。",
        "主页": "http://tcos.shisu.edu.cn/18/48/c5212a137288/page.htm"
    },
    {
        "姓名": "周成",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学讲师、海外考试中心主任。毕业于上海外国语大学，获英语语言文学学士与硕士学位，曾赴英国拉夫堡大学担任访问学者。长期从事英语综合技能、听说教学及国际标准化考试测评管理，注重在生动真实的影视与语境实践中提升学生跨文化语言应用能力。",
        "主页": "http://tcos.shisu.edu.cn/18/47/c5212a137287/page.htm"
    },
    {
        "姓名": "王俭",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学讲师。上海外国语大学英语语言文学博士，曾任英国剑桥大学访问学者（2006-2007）及美国加州富勒心理学院访问学者（2014）。主要研究方向为海明威文学与小说语料库分析，发表《基于语料库的海明威小说评论研究》，主讲雅思阅读、英语演讲、影视赏析与小说评论等课程。",
        "主页": "http://tcos.shisu.edu.cn/18/49/c5212a137289/page.htm"
    },
    {
        "姓名": "张宁",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学讲师。上海外国语大学国际关系博士、英语文学硕士，新加坡南洋理工大学国立教育学院访问学者。核心研究领域为语言政策与规划、东南亚区域研究及小国外交。主要讲授雅思、经贸英语、西方国家概况以及国际关系理论与实践等课程。",
        "主页": "http://tcos.shisu.edu.cn/18/4b/c5212a137291/page.htm"
    },
    {
        "姓名": "王继楠",
        "职称": "讲师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学讲师、教研室副主任、1.5/2+2项目教学负责人。上海外国语大学与英国剑桥大学联合培养博士，美国新泽西大学访问学者，托福满分。出版英汉句法-话语界面对比研究英文专著，参编《大学英语快速阅读》，主讲学术英语、雅思听读及公派研究生高级听说等课程。",
        "主页": "http://tcos.shisu.edu.cn/18/4c/c5212a137292/page.htm"
    },
    {
        "姓名": "蔡坚",
        "职称": "助理研究员",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学助理研究员、项目主管。上海外国语大学教育学硕士。长期从事教学管理与国际项目开发，主要研究绩效技术与网络教育，发表《绩效技术在提高网络学院教学质量中的应用研究》，参编《企业、政府与国际经济》《科技英语阅读教程》，主要讲授英语阅读与听力。",
        "主页": "http://tcos.shisu.edu.cn/18/4e/c5212a137294/page.htm"
    },
    {
        "姓名": "孔祥珺",
        "职称": "助理研究员",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学助理研究员、学院专职辅导员。澳大利亚阿德莱德大学金融硕士。主要研究领域为高等教育管理与国际预科教育，发表《高等教育学费标准探讨》《高中留学预科生的预科教育现状及其文化适应策略分析》，主讲管理学基础及跨文化适应指导等课程。",
        "主页": "http://tcos.shisu.edu.cn/18/4f/c5212a137295/page.htm"
    },
    {
        "姓名": "刘爽",
        "职称": "助理研究员",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学助理研究员、学院专职辅导员。2016年毕业于上海外国语大学高级翻译学院英语笔译专业（MTI），曾赴新西兰怀卡托大学及澳大利亚纽卡斯尔大学研修参访。主要从事学生思想政治教育与国际化人才跨文化引导，擅长英语阅读教学与跨文化交流。",
        "主页": "http://tcos.shisu.edu.cn/18/50/c5212a137296/page.htm"
    },
    {
        "姓名": "孙启梦",
        "职称": "专任教师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学出国留学人员培训部及海外合作学院英语专任教师。英国杜伦大学对外英语教学与应用语言学硕士。主要研究方向为语言焦虑与口语交流意愿、第二语言习得与跨文化交际。长期主讲雅思听说读写及国际预科英语课程，倡导在真实交际语境中循序渐进精进语言。",
        "主页": "http://tcos.shisu.edu.cn/e3/be/c5212a189374/page.htm"
    },
    {
        "姓名": "常翠",
        "职称": "专任教师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "上海外国语大学出国留学人员培训部及海外合作学院英语专任教师。西北工业大学文学学士、上海理工大学英语语言文学硕士。主要研究领域为翻译理论与实践、认知语言学及英美文学。主要承担雅思教学、学术英语及英美文学赏析等课程，注重文学思辨与跨文化国际素养培养。",
        "主页": "http://tcos.shisu.edu.cn/2d/77/c5212a208247/page.htm"
    },
    {
        "姓名": "Felix Goodbody",
        "职称": "外籍讲师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "英国学者、上海外国语大学海外合作学院外籍讲师。英国利物浦大学历史学学士与博士、曼彻斯特大学理学硕士。核心研究领域为医学史及中外高等教育与医疗合作。2021年来华任教，主讲学术英语及国际项目申请辅导课程，致力于通过启发式教学提升中国学生的英语水平与批判性思维。",
        "主页": "http://tcos.shisu.edu.cn/0f/2a/c5213a200490/page.htm"
    },
    {
        "姓名": "Cornelius Anjous Gomes",
        "职称": "外籍教师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "加拿大籍外教（Max C. Gomes）。毕业于加拿大麦克马斯特大学机械工程专业，拥有工程背景与十余年英语教学经验。曾就职于英孚及华尔街英语从事成人英语与企业培训，擅长将人工智能教学工具与多媒体技术应用于英语教学场景，致力于为国际预科生打造高效智能的第二语言课堂。",
        "主页": "http://tcos.shisu.edu.cn/e9/12/c5213a190738/page.htm"
    },
    {
        "姓名": "Sialupapu Siameja",
        "职称": "外籍教师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "新西兰籍学者（Papu Siameja）。赞比亚大学学士、新西兰怀卡托大学管理学硕士与经济学博士。曾任职于新西兰卫生部门及怀卡托大学管理学院经济学系。2020年起在上海外国语大学怀卡托合作项目担任专任外教，主要教授经济学、管理学与市场营销等专业学术全英文课程。",
        "主页": "http://tcos.shisu.edu.cn/c1/fa/c5213a180730/page.htm"
    },
    {
        "姓名": "Jonathan Highmark",
        "职称": "外籍专家",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "美国籍外籍专家。毕业于明尼苏达大学双城分校亚洲语言文学专业，拥有十余年大学英文教学及近十年在华教学经历。曾任教于浙江师范大学及贝立兹商务培训中心。2020年加入上海外国语大学，讲授教育部访问学者口语、企业商务英语、西方文化、英语演讲及学术英语等课程。",
        "主页": "http://tcos.shisu.edu.cn/c1/f7/c5213a180727/page.htm"
    },
    {
        "姓名": "德永富美惠",
        "职称": "外籍教师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "日本籍专任教师。日本大学人文社会科学研究科硕士毕业，曾任日本高中英语教师，2012年赴复旦大学留学深造。现任职于上海外国语大学出国留学人员培训部及海外合作学院，主要承担赴日留学预科日语精读、听说及文科综合科目的全方位教学，积极推动中日学术与青年文化交流。",
        "主页": "http://tcos.shisu.edu.cn/c8/8d/c5213a116877/page.htm"
    },
    {
        "姓名": "Michael Robert Gonzales",
        "职称": "外籍教师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "美国籍外教（Mike Gonzales）。来自加利福尼亚州圣地亚哥，美国哥伦比亚大学教师学院对外英语教学（TESOL）硕士。2005年起从事英语教学，曾执教于日本中学以及中国清华大学等多所重点高校。在上海外国语大学主要教授西方文化、商务英语及学术听说等系列课程。",
        "主页": "http://tcos.shisu.edu.cn/c7/e9/c5213a116713/page.htm"
    },
    {
        "姓名": "Ben Keegan",
        "职称": "外籍教师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "爱尔兰籍外教。商业学士、对外英语教学（TESOL）硕士，持有英国剑桥CELTA成人英语教学证书。曾任职于英国石油等跨国公司，2010年加入上海外国语大学出国留学人员培训部及海外合作学院，主讲英语口语、写作、雅思备考、跨文化交际及西方文化等课程。",
        "主页": "http://tcos.shisu.edu.cn/a4/01/c5213a107521/page.htm"
    },
    {
        "姓名": "Romain VUATTOUX",
        "职称": "外籍教师",
        "邮箱": "",
        "学校": "上海外国语大学",
        "学院": "海外合作学院",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": date_today,
        "简介": "法国籍外教。加拿大Mount Allison大学国际关系学士，瑞典马尔默大学可持续城市管理硕士，新西兰怀卡托大学教学法研究生证书。持有TEFL/TESOL资格。在上外讲授媒体研究、国际关系、欧洲历史、社会学及法语课程，兼任新西兰怀卡托大学基础课程教学助理。",
        "主页": "http://tcos.shisu.edu.cn/6d/11/c5213a93457/page.htm"
    }
]

columns = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']

# Split into with email and without email
with_email = [t for t in teachers_data if t['邮箱']]
no_email = [t for t in teachers_data if not t['邮箱']]

# Add sequence numbers
for idx, item in enumerate(with_email, 1):
    item['序号'] = idx

for idx, item in enumerate(no_email, 1):
    item['序号'] = idx

df_with = pd.DataFrame(with_email, columns=columns)
df_no = pd.DataFrame(no_email, columns=columns)

sheet1_name = f"有邮箱学者 (共 {len(df_with)} 位)"
sheet2_name = f"无邮箱学者 (共 {len(df_no)} 位)"

with pd.ExcelWriter(output_path, engine='openpyxl') as writer:
    df_with.to_excel(writer, sheet_name=sheet1_name, index=False)
    df_no.to_excel(writer, sheet_name=sheet2_name, index=False)

print(f"Excel generated successfully at: {output_path}")
print(f"Sheet 1: {sheet1_name}")
print(f"Sheet 2: {sheet2_name}")
print(f"Total count: {len(teachers_data)} (Email: {len(df_with)}, No Email: {len(df_no)})")

# Now call sync API
task_id = "4411dc4a"
sync_url = f"http://127.0.0.1:8000/api/task/{task_id}/sync"
payload = {
    "excel_path": output_path,
    "total_count": len(teachers_data),
    "email_count": len(df_with),
    "no_email_count": len(df_no)
}

req_data = json.dumps(payload).encode('utf-8')
req = urllib.request.Request(sync_url, data=req_data, headers={'Content-Type': 'application/json'})

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = resp.read().decode('utf-8')
        print(f"Sync API Response: {result}")
except Exception as e:
    print(f"Sync API Error: {e}")
