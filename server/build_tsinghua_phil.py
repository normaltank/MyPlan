# -*- coding: utf-8 -*-
import os
import sys
import json
import datetime
import urllib.request
import pandas as pd
import openpyxl

records = [
    {
        "序号": 1,
        "姓名": "Asher Jiang",
        "职称": "教授",
        "邮箱": "asherjiang@tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "德国海德堡大学哲学博士与特许任教资格（Habilitation），现任清华大学哲学系长聘教授、外国哲学教研室主任。曾任海德堡大学助理教授、苏黎世大学助理教授。主要研究领域为早期分析哲学（卡尔纳普、弗雷格）、英国经验主义（洛克、休谟）及分析形而上学，在国际顶级哲学期刊发表多篇论文。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2214.htm"
    },
    {
        "序号": 2,
        "姓名": "陈壁生",
        "职称": "教授",
        "邮箱": "bishengchen@163.com",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "中山大学哲学博士，现任清华大学哲学系教授、副系主任。曾任中国人民大学国学院教授。主要研究领域为经学、汉代哲学与近现代哲学。著有《经学的瓦解》《孝经学史》《经学、制度与生活》等学术专著，曾获第三届全球华人国学大典国学成果奖，主持并参与多项国家级科研课题。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2198.htm"
    },
    {
        "序号": 3,
        "姓名": "陈来",
        "职称": "教授",
        "邮箱": "chenlai@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "北京大学哲学博士，师从张岱年先生。现任清华大学文科资深教授、国学研究院院长、校学术委员会副主任、国际哲学学院院士，兼任全国中国哲学史学会会长、中央文史馆馆员。长期致力于儒家哲学、宋元明清理学及现代儒家哲学研究，代表作有《仁学本体论》《朱熹哲学研究》《有无之境》等，为当代哲学界领军学者。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2197.htm"
    },
    {
        "序号": 4,
        "姓名": "丁四新",
        "职称": "教授",
        "邮箱": "dingsixin@tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "武汉大学哲学博士，教育部重大人才计划特聘教授，现任清华大学哲学系教授、博士生导师、中国哲学学科负责人，兼任中国哲学史学会副会长、中国周易学会副会长。主要从事先秦秦汉哲学、周易经学、简帛思想与出土文献研究。出版《郭店楚竹书哲学思想研究》等著作十余部，曾获全国优秀博士学位论文奖。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2196.htm"
    },
    {
        "序号": 5,
        "姓名": "韩立新",
        "职称": "教授",
        "邮箱": "hanlixin@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "日本一桥大学社会学博士，教育部新世纪优秀人才，现任清华大学人文学院副院长、哲学系长聘教授、马克思恩格斯文献研究中心主任。主要从事马克思主义哲学、新MEGA考证版文献学、日本马克思主义及环境伦理学研究。代表作《〈巴黎手稿〉研究——马克思思想的转折点》入选国家哲学社会科学成果文库与中华学术外译项目。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2195.htm"
    },
    {
        "序号": 6,
        "姓名": "黄裕生",
        "职称": "教授",
        "邮箱": "huangysh@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "中国社会科学院研究生院哲学博士，清华大学哲学系教授、博士生导师，曾任清华大学哲学系主任、中华全国外国哲学史学会秘书长。主要研究领域为第一哲学原理、德国哲学（康德与黑格尔）、宗教哲学与政治哲学。出版《时间与永恒：论海德格尔哲学中的时间问题》《真理与自由》《宗教与哲学的相遇》等代表性学术专著。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2194.htm"
    },
    {
        "序号": 7,
        "姓名": "刘奋荣",
        "职称": "教授",
        "邮箱": "fenrong@tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "阿姆斯特丹大学理学博士、中国社科院哲学博士，教育部长江学者特聘教授、国际科学哲学学院院士，阿姆斯特丹-中国逻辑学讲席教授，清华-阿姆斯特丹逻辑学联合研究中心主任。主要从事动态偏好逻辑、人工智能逻辑及中外逻辑史研究，著有《动态偏好逻辑》及英文专著，多次获教育部高校科研优秀成果奖。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2193.htm"
    },
    {
        "序号": 8,
        "姓名": "Martin Stokhof",
        "职称": "教授",
        "邮箱": "M.J.B.Stokhof@uva.nl",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "阿姆斯特丹大学荣誉退休教授，清华大学哲学系双聘教授，欧洲科学院院士、荷兰皇家艺术与科学院院士。主要研究领域为语义学哲学与维特根斯坦哲学，涵盖形式语义学概念与方法论问题（如组合性、形式语法）、诠释理论及维特根斯坦早期本体论与伦理学思想，著有《World and Life as One》等专著。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2210.htm"
    },
    {
        "序号": 9,
        "姓名": "圣凯",
        "职称": "教授",
        "邮箱": "sgkai@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "南京大学哲学博士，清华大学哲学博士后，现任清华大学哲学系长聘教授、博士生导师、道德与宗教研究院副院长，兼任《佛学研究》主编。主要从事佛教哲学、中国佛教史、梵文佛典及宗教社会学研究。主持国家社科基金重大项目“汉传佛教僧众社会生活史”，著有《摄论学派研究》《中国佛教通史》等，多次获部级科研奖励。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2192.htm"
    },
    {
        "序号": 10,
        "姓名": "宋继杰",
        "职称": "教授",
        "邮箱": "song_jijie@163.com",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "中国社会科学院哲学博士，哈佛大学、牛津大学访问学者，曾任清华大学哲学系主任，现任清华大学哲学系教授、博士生导师。主要从事古希腊哲学、形而上学及现代西方哲学研究。出版学术专著《海德格尔与存在论历史的解构》《逻各斯的技术——古希腊思想的语言哲学透视》，主编《BEING与西方哲学传统》等。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2191.htm"
    },
    {
        "序号": 11,
        "姓名": "唐浩",
        "职称": "教授",
        "邮箱": "cogitang@yahoo.com",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "美国匹兹堡大学哲学博士，曾任武汉大学副教授、中山大学教授，现任清华大学哲学系长聘教授。主要研究领域为维特根斯坦哲学、语言哲学、心灵哲学、行动哲学以及西方近代哲学（笛卡尔至康德），在国际知名哲学期刊发表多篇论文，合编《Wittgenstein at 100》。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2190.htm"
    },
    {
        "序号": 12,
        "姓名": "唐文明",
        "职称": "教授",
        "邮箱": "tangwm@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "北京大学哲学博士，哈佛-燕京学社访问学者，现任清华大学哲学系主任、教授、博士生导师，清华大学道德与宗教研究院副院长、儒学研究中心主任，中华孔子学会秘书长。主要研究领域为伦理学、宗教学与中国哲学。出版《与命与仁》《隐秘的颠覆》《敷教在宽》《彝伦攸斁》《不绝若线》等学术专著，主编《公共儒学》辑刊。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2189.htm"
    },
    {
        "序号": 13,
        "姓名": "万俊人",
        "职称": "教授",
        "邮箱": "junrenwan58@163.com",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "北京大学哲学硕士，师从周辅成先生。长江学者特聘教授，清华大学哲学系教授、博导、校学术委员会委员，曾任人文学院院长、中国伦理学会会长。主要研究领域为伦理学与政治哲学，出版《现代西方伦理学史》《寻求普世伦理》《正义为何如此脆弱》等学术专著与译著40余部，发表论文200余篇，是国内伦理学界领军学者。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2187.htm"
    },
    {
        "序号": 14,
        "姓名": "夏莹",
        "职称": "教授",
        "邮箱": "melodyxia9936@163.com",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "清华大学哲学博士，法国巴黎第一大学访问学者，现任清华大学哲学系长聘教授、博士生导师，兼任中国当代国外马克思主义研究会常务理事。主要研究领域为当代国外马克思主义、当代法国哲学及辩证法理论。出版多部学术专著，主持国家社科基金重大项目子课题，在《中国社会科学》《哲学研究》发表学术论文数十篇。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2186.htm"
    },
    {
        "序号": 15,
        "姓名": "肖鹰",
        "职称": "教授",
        "邮箱": "xiaoying@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "北京大学哲学博士，清华大学哲学系教授、博士生导师。曾任德国波茨坦大学客座教授、剑桥大学访问学者。主要研究领域为美学、艺术哲学与当代文化艺术批评。出版《以神为马——中国美学的游与思》《肖鹰文集初编》《中国美学史·明代卷》《中西艺术导论》等专著，主持并完成多项国家社科基金重点与规划课题。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2185.htm"
    },
    {
        "序号": 16,
        "姓名": "朱东华",
        "职称": "教授",
        "邮箱": "zhu_781@163.com",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "北京大学哲学博士，现任清华大学哲学系教授、道德与宗教研究院教授。主要研究领域为宗教现象学、唐元景教文献、原始基督教及叙利亚教父哲学。出版《从神圣到奥秘》等学术著作，主持国家社科基金项目，任多部国际宗教学术期刊审稿人。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2184.htm"
    },
    {
        "序号": 17,
        "姓名": "Jeremy Seligman",
        "职称": "教授",
        "邮箱": "jeremy@seligman.info",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "现任清华大学哲学系教授、奥克兰大学哲学系教授，清华-阿姆斯特丹逻辑学联合研究中心资深研究员。主要研究领域为社会认知逻辑、模态逻辑、行动逻辑以及中国古代逻辑思想史（先秦名辩之学），在国际权威哲学与逻辑学期刊发表多篇论著。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2209.htm"
    },
    {
        "序号": 18,
        "姓名": "Johan van Benthem",
        "职称": "教授",
        "邮箱": "johan@stanford.edu",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "荷兰阿姆斯特丹大学大学教授、美国斯坦福大学哲学教授、清华大学特聘访问教授。欧洲科学院院士、荷兰皇家艺术与科学院院士、美国文理科学院院士、国际哲学学院院士。动态逻辑与信息逻辑奠基人，研究涵盖信息逻辑、博弈与行动逻辑、认知逻辑及逻辑普及，是当代国际逻辑学界泰斗级学者。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2211.htm"
    },
    {
        "序号": 19,
        "姓名": "Dag Westerstahl",
        "职称": "教授",
        "邮箱": "dag.westerstahl@philosophy.su.se",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "瑞典斯德哥尔摩大学理论哲学与逻辑学教授、清华大学特聘访问教授，瑞典皇家科学院院士、哥德堡皇家艺术与科学学会会员，曾任国际逻辑学、方法论与科学哲学分会秘书长。主要研究领域为数理逻辑、形式语义学、语言哲学及广义量词理论，在哲学逻辑与语言学交叉领域享有崇高国际学术声誉。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1063/2212.htm"
    },
    {
        "序号": 20,
        "姓名": "Andree Hahmann",
        "职称": "副教授",
        "邮箱": "ahahmann@tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "德国马堡大学哲学博士、哥廷根大学特许任教资格。现任清华大学哲学系长聘副教授。曾任哥廷根大学助理教授、宾夕法尼亚大学客座教授。主要研究领域为古希腊罗马哲学史（亚里士多德、伊壁鸠鲁）、近现代欧洲大陆哲学（康德、黑格尔），出版多部德文哲学专著。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2199.htm"
    },
    {
        "序号": 21,
        "姓名": "陈浩",
        "职称": "副教授",
        "邮箱": "chenhao2014@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "清华大学法学博士与日本东北大学法学博士，现任清华大学哲学系副教授、副系主任，兼任英国政治学会期刊《Studies in Marxism》顾问。主要从事德国社会政治哲学、黑格尔法哲学、日本马克思主义及新MEGA文献学研究，在《世界哲学》《现代哲学》等期刊发表中日英文学术论文20余篇。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2208.htm"
    },
    {
        "序号": 22,
        "姓名": "范大邯",
        "职称": "副教授",
        "邮箱": "fandahan@tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "德国图宾根大学哲学博士（极优等 summa cum laude），曾任图宾根大学哲学系讲师，现任清华大学哲学系副教授。主要研究领域为德国哲学、康德哲学（其实践哲学与审美判断力批判理论）及当代实践哲学，出版德文专著一部，获德国利奥波德·卢卡斯青年学者奖。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2207.htm"
    },
    {
        "序号": 23,
        "姓名": "高海波",
        "职称": "副教授",
        "邮箱": "hb123gao@163.com",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "北京大学哲学博士，哈佛大学东亚语言文明系访问学者，曾任中国社科院哲学所助理研究员，现任清华大学哲学系副教授、儒学研究中心副主任。主要研究领域为宋明理学、中国近现代哲学与儒家思想。出版专著《刘蕺山哲学思想研究》，在《哲学动态》《中国哲学史》等核心期刊发表论文20余篇，主持多项省部级课题。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2206.htm"
    },
    {
        "序号": 24,
        "姓名": "瞿旭彤",
        "职称": "副教授",
        "邮箱": "quxutong@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "德国海德堡大学博士（极优等），普林斯顿巴特研究中心访问学者，现任清华大学哲学系长聘副教授、博士生导师。主要研究领域为德国现当代基督教神学（巴特、朋霍费尔）、中国基督教思想史、德国哲学及古希腊哲学。出版德文专著一部，荣获德国海德堡大学国际青年学者奖。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2205.htm"
    },
    {
        "序号": 25,
        "姓名": "石辰威",
        "职称": "副教授",
        "邮箱": "scw@tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "荷兰阿姆斯特丹大学逻辑学博士，曾任德国埃尔朗根-纽伦堡大学博士后，现任清华大学哲学系准聘副教授、副系主任兼党支部书记。主要研究领域为形式认识论、哲学逻辑、论辩理论及集体信念模型，在国际顶级哲学与逻辑学期刊发表多篇论文。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2204.htm"
    },
    {
        "序号": 26,
        "姓名": "孙晶",
        "职称": "副教授",
        "邮箱": "sjing@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "荷兰莱顿大学艺术史博士，北京大学博士后，哈佛大学文艺复兴研究中心访问学者，现任清华大学哲学系副教授、美学教研室主任。主要研究领域为美学原理、西方艺术史及中西艺术文化交流史。出版学术专著及合著多部，在国内外权威期刊发表学术论文数十篇。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2203.htm"
    },
    {
        "序号": 27,
        "姓名": "俞珺华",
        "职称": "副教授",
        "邮箱": "df7g5036-yjh@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "纽约市立大学研究生院计算机科学系逻辑学博士，现任清华大学哲学系副教授。主要研究领域为证明论、直觉主义逻辑、模态逻辑、自指涉语义学及理由逻辑（Justification Logic）。曾荣获逻辑学领域国际知名奖项 Rosser Prize（最佳学生论文奖）与第五届金岳霖学术奖，在专业期刊发表多篇论文。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2202.htm"
    },
    {
        "序号": 28,
        "姓名": "袁艾",
        "职称": "副教授",
        "邮箱": "yuanai@mail.tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "英国牛津大学东方学博士，现任清华大学哲学系副教授、清华-阿姆斯特丹逻辑学中心研究员、《国际儒学》责任编辑。主要研究领域为先秦道家哲学、比较伦理学及早期中国修辞与交流学。在国际主流学术期刊发表多篇论文，曾获国际中国哲学协会青年学者论文奖。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2201.htm"
    },
    {
        "序号": 29,
        "姓名": "张伟特",
        "职称": "副教授",
        "邮箱": "weitezhang@tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "德国海德堡大学哲学博士，现任清华大学哲学系与新雅书院双聘副教授、新雅书院党总支书记兼副院长、《清华西方哲学研究》副主编。主要研究领域为西方近代哲学（笛卡尔、斯宾诺莎）、形而上学、认识论、元哲学及人工智能哲学与计算哲学，出版学术专著一部。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2242.htm"
    },
    {
        "序号": 30,
        "姓名": "赵金刚",
        "职称": "副教授",
        "邮箱": "napsol@tsinghua.edu.cn",
        "学校": "清华大学",
        "学院": "哲学系",
        "采集人": "Antigravity Subagent (Browser)",
        "采集日期": "2026-09-21",
        "简介": "北京大学哲学博士，师从陈来教授，中国社科院哲学所博士后，现任清华大学哲学系副教授。主要研究领域为儒家哲学、宋明理学（朱熹思想）、春秋学与历史哲学。出版学术专著《朱熹的历史观——天理视域下的历史世界》，在《哲学研究》《世界宗教研究》《中国哲学史》等权威期刊发表学术论文20余篇。",
        "主页": "https://www.phil.tsinghua.edu.cn/info/1064/2200.htm"
    }
]

def main():
    sys.stdout.reconfigure(encoding="utf-8")
    print(f"Total faculty members: {len(records)}")
    
    # Strictly verify intro lengths (80-150 chars)
    bad_count = 0
    for r in records:
        l = len(r["简介"])
        status = "OK" if 80 <= l <= 150 else "OUT_OF_RANGE"
        if status != "OK":
            bad_count += 1
        print(f"[{r['序号']:02d}] {r['姓名']}: {r['职称']} | {r['邮箱']} | 简介长度: {l}字 ({status})")
    assert bad_count == 0, f"{bad_count} records out of range!"
    
    print("\n✅ All 30 academic intros strictly satisfy 80 <= length <= 150 characters!")
    
    with_email = [r for r in records if r["邮箱"] and "@" in r["邮箱"]]
    no_email = [r for r in records if not (r["邮箱"] and "@" in r["邮箱"])]
    
    print(f"\nDual sheet conservation check: With Email={len(with_email)}, No Email={len(no_email)}, Total={len(records)}")
    assert len(with_email) + len(no_email) == len(records), "Dual sheet conservation violation!"
    
    df_with_email = pd.DataFrame(with_email)
    df_no_email = pd.DataFrame(no_email)
    
    cols = ['序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页']
    if not df_with_email.empty:
        df_with_email = df_with_email[cols]
    else:
        df_with_email = pd.DataFrame(columns=cols)
        
    if not df_no_email.empty:
        df_no_email = df_no_email[cols]
    else:
        df_no_email = pd.DataFrame(columns=cols)
        
    now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join("server", "outputs")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"清华大学_哲学系_{now_str}.xlsx"
    filepath = os.path.join(out_dir, filename)
    
    sheet1_name = f"有邮箱学者 (共 {len(df_with_email)} 位)"
    sheet2_name = f"无邮箱学者 (共 {len(df_no_email)} 位)"
    
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df_with_email.to_excel(writer, sheet_name=sheet1_name, index=False)
        df_no_email.to_excel(writer, sheet_name=sheet2_name, index=False)
        
    print(f"\nSuccessfully generated Excel: {filepath}")
    
    # Sync with local FastAPI backend
    task_id = "9ec3a512"
    sync_url = f"http://127.0.0.1:8000/api/task/{task_id}/sync"
    
    sync_payload = {
        "records": records,
        "stats": {
            "total": len(records),
            "with_email": len(with_email),
            "no_email": len(no_email),
            "email_rate": round(len(with_email) / len(records) * 100, 1) if records else 0
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
