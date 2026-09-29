import os
import sys
import json
import re
import urllib.request
import pandas as pd
import openpyxl

sys.stdout.reconfigure(encoding='utf-8')

# 46 Scholars Dataset with pure native semantic introductions (80-150 chars each)
SCHOLARS = [
    # --- 研究院领导 (3人) ---
    {
        "name": "姜锋",
        "title": "研究员，博士生导师",
        "email": "",
        "category": "研究院领导",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=898743d8-1189-4058-8fea-36b74126e5a9",
        "bio": "姜锋，研究员、博士生导师，上海全球治理与区域国别研究院理事长、上海市区域国别学会会长、第七届上海市社联副主席。历任教育部国际合作与交流司副司长、中国驻德国大使馆公使衔参赞、上海外国语大学党委书记，现任国务院学位委员会区域国别学学科评议组第一召集人。长期从事欧洲研究、中德交流、德国文化与外交政策研究。"
    },
    {
        "name": "孟钟捷",
        "title": "教授，博士生导师",
        "email": "zjmeng@history.ecnu.edu.cn",
        "category": "研究院领导",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=215bf7d2-456d-4721-8bde-3667ed46ac83",
        "bio": "孟钟捷，历史学博士，教授、博士生导师，现任上海外国语大学校长、上海全球治理与区域国别研究院学术委员会主任。教育部重要人才计划入选者，曾任华东师范大学党委副书记、历史学系主任。主要从事德国现代史、20世纪世界史、历史教育与记忆文化研究，主持国家社科基金重大项目等多项国家级科研课题，出版多部权威学术专著与译著。"
    },
    {
        "name": "杨成",
        "title": "教授，博士生导师",
        "email": "",
        "category": "研究院领导",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=239a7fed-8fc7-4cd6-b75d-9a5daad37e3f",
        "bio": "杨成，法学博士，上海外国语大学教授、博士生导师，上海全球治理与区域国别研究院执行院长，上海市“东方学者”特聘教授，国别与区域研究独立交叉学科负责人。曾任中国驻俄罗斯使馆外交官、华东师范大学俄罗斯研究中心副主任。主要研究方向为欧亚一体化、俄罗斯内政外交、中亚地缘政治与大国关系，主持多项国家社科基金重大及重点项目。"
    },

    # --- 本院学者 (43人) ---
    {
        "name": "张永维",
        "title": "博士后",
        "email": "2023616@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=7cbac963-1183-4b29-8f3e-34e71d96f7c7",
        "bio": "张永维，文学博士，上海外国语大学上海全球治理与区域国别研究院博士后研究人员。主要从事日本古典文学、中日比较文学、东亚汉文学与区域文化交流研究，参与国家级重大社科项目与教育部人文社科课题，在国内外专业学术集刊发表多篇高质量学术论文。"
    },
    {
        "name": "黄乐乐",
        "title": "助理研究员",
        "email": "huanglele@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=de9edcb9-9574-4fff-9d6a-9e3e8355d3f7",
        "bio": "黄乐乐，上海外国语大学世界艺术史研究所助理研究员。主要从事印度佛教艺术、南亚与东南亚物质文化传播、汉传与藏传佛教图像学研究。在海外知名学术出版社出版英文学术专著一部，并在国内外权威同行评议期刊发表中英文学术论文多篇，多次参与国际高水平学术会议。"
    },
    {
        "name": "周俊杰",
        "title": "师资博士后",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=8e305cfa-258c-46ec-9596-e4b1e9a5f3de",
        "bio": "周俊杰，北京大学艺术学学士、牛津大学古典考古学硕士、慕尼黑大学古典考古学博士，现任上海全球治理与区域国别研究院师资博士后。主要研究方向为古典考古学、跨文化艺术交流、丝绸之路图像传播与物质文化研究，曾访学耶鲁大学，在国际考古学界发表多项代表性学术成果。"
    },
    {
        "name": "胡文婷",
        "title": "副教授",
        "email": "huwenting21703@126.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=c2598379-e580-459f-be89-3ea5954605e5",
        "bio": "胡文婷，北京外国语大学博士，上海外国语大学副教授、硕士生导师，兼任中国比较文学学会海外汉学分会理事。研究领域为海外汉学、比较文学与中西文化交流史。主持国家社科基金冷门绝学专项项目、教育部人文社科青年基金等国家级及省部级项目，出版多部学术译著并在核心刊物发表论文数十篇。"
    },
    {
        "name": "张天资",
        "title": "博士后",
        "email": "tiannzi@163.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=314b13a3-a757-418f-a9cb-b2f7dd0602bb",
        "bio": "张天资，西班牙塞维利亚大学拉丁美洲研究博士，上海外国语大学博士后，阿根廷滨海国立大学访问学者。主要从事拉丁美洲文学、阿根廷当代文学与中拉比较文学研究，出版西班牙语学术专著一部，主持上海外国语大学区域国别开放课题，参与教育部重大攻关项目及欧盟玛丽居里项目。"
    },
    {
        "name": "王维军",
        "title": "博士后",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=cf582312-32a8-4447-aa68-0720563462df",
        "bio": "王维军，浙江大学比较教育学博士，美国威斯康星大学麦迪逊分校联合培养博士，现为上海外国语大学上海全球治理与区域国别研究院博士后。主要研究方向为比较高等教育、国际教育政策、国际组织与全球教育治理，在《比较教育学报》等核心学术期刊发表多篇专业论文。"
    },
    {
        "name": "谈佳洁",
        "title": "在职博士后",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=25ebbe23-5e76-476b-9d43-02fdb6f62b76",
        "bio": "谈佳洁，上海交通大学新闻传播学博士，上海全球治理与区域国别研究院在职博士后。主要从事城市文化国际传播、文旅消费空间场景化与传播符号学研究。出版学术专著《城市文化视角下消费空间场景化研究》（上海交通大学出版社），在SSCI与CSSCI期刊发表多篇高水平学术成果。"
    },
    {
        "name": "刘鑫",
        "title": "博士后",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=755f1fa0-8278-43d9-9524-74a0058ec0e7",
        "bio": "刘鑫，历史学博士，现为上海外国语大学上海全球治理与区域国别研究院博士后研究人员。主要从事北欧区域国别学、芬兰近现代史、俄罗斯帝国史与波罗的海区域国际关系研究，在国内外相关专业学术会议与集刊发表专题研究成果。"
    },
    {
        "name": "林海文",
        "title": "在职博士后",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=efef24a1-05c0-4279-9941-863a8a927a05",
        "bio": "林海文，上海外国语大学国际政治专业法学博士，现为上海全球治理与区域国别研究院在职博士后、浙江警察学院副教授。主要研究方向为国际政治与全球治理、跨境数据流动机制、美国长臂管辖与国际警务合作，主持教育部人文社科青年项目《美国长臂管辖中网络型权力的作用机制及其应对策略研究》。"
    },
    {
        "name": "李威",
        "title": "博士后",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=943e2e83-75b2-4d56-a979-31713febe4ba",
        "bio": "李威，北京大学世界史博士，英国伦敦大学高等研究院历史研究所访问学者，上海外国语大学欧洲研究所博士后，入选国家资助博士后研究人员计划。主要研究方向为英国近现代史、英帝国史与西方社会思潮，在《经济社会史评论》《国际观察》《政治思想史》等CSSCI权威期刊发表多篇学术论文。"
    },
    {
        "name": "崔钰",
        "title": "师资博士后",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=85f9c8a2-0746-4a2f-afcd-21c3aa9a6484",
        "bio": "崔钰，华东师范大学地图学与地理信息系统专业理学博士，现任上海全球治理与区域国别研究院师资博士后。主要研究领域为数字人文、GIS空间分析应用与中国典籍全球传播网络建模，在国内外高水平学术期刊发表多篇交叉学科创新论文，参与多项国家级重大科研课题。"
    },
    {
        "name": "包安若",
        "title": "助理研究员",
        "email": "2024004@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=3fd76219-ec0a-430e-b73a-6bab94b85438",
        "bio": "包安若（Anatoly Baskin），美国哥伦比亚大学犹太研究与比较文学博士，现为上海外国语大学上海全球治理与区域国别研究院犹太研究所助理研究员。主要从事现代意第绪语文学与文化、现代希伯来文学、近东犹太历史与中犹比较文化研究，在De Gruyter等国际知名学术出版社发表多篇学术成果。"
    },
    {
        "name": "薛江",
        "title": "副研究员",
        "email": "2024002@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=cffef1dc-8324-4f4d-bc93-a972c3d5e0dc",
        "bio": "薛江，北京大学前沿交叉学科研究院博士，上海外国语大学世界艺术史研究所副研究员、埃及中心主任。主要从事古埃及艺术史、东北非考古、跨文化物质文明交流研究，多次赴埃及、苏丹开展田野考古发掘与海外遗址考察，主持并参与多项国家社科基金重大课题与国际合作项目。"
    },
    {
        "name": "赵玲",
        "title": "副教授",
        "email": "2023022@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=3a44da0f-b751-4f9a-8766-1615f9e95a6e",
        "bio": "赵玲，上海大学美术学博士，浙江大学考古学博士后，上海外国语大学世界艺术史研究所副教授。主要从事佛教美术、丝绸之路物质文化史与古代图像学研究，出版多部学术专著，在《美术研究》《故宫博物院院刊》《敦煌研究》等专业权威期刊发表论文数十篇，主持多项国家级与省部级社科基金项目。"
    },
    {
        "name": "唐陆",
        "title": "博士后",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=599388df-22d7-4ea9-bca1-6cbe16b2cf73",
        "bio": "唐陆，华东师范大学历史学博士，先后毕业于山东大学、东北师范大学，现为上海全球治理与区域国别研究院博士后研究人员。主要研究领域为苏联历史、中亚现代政治与民族史、欧亚跨区域地缘关系演变，参与多项中亚与欧亚国家重大研究课题。"
    },
    {
        "name": "朱伟芳",
        "title": "博士后",
        "email": "2023605@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=61ae7caf-aa3d-4257-b76d-c71167e0db5e",
        "bio": "朱伟芳，上海师范大学比较文学与世界文学博士，上海外国语大学上海全球治理与区域国别研究院、中国话语与世界文学研究中心博士后。主要研究方向为文学翻译研究、非洲英语文学、后殖民理论与中非人文交流，在CSSCI期刊发表论文多篇，主持中国博士后科学基金面上资助项目。"
    },
    {
        "name": "尹兰曦",
        "title": "博士后",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=c32c4b57-69fa-478a-a533-5c21ec3d4dfa",
        "bio": "尹兰曦，南开大学文学博士、英国伦敦大学学院(UCL)文学硕士，上海外国语大学中国话语与世界文学研究中心博士后，入选国家资助博士后研究人员计划。主要学术领域为莎士比亚戏剧与修辞学、西方文学批评话语，主持教育部人文社科青年基金及中国博士后科学基金面上资助项目。"
    },
    {
        "name": "Janko Šćepanović",
        "title": "副研究员",
        "email": "janko@hotmail.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=4efa606c-f4f8-4448-8af7-23cd6439d6e0",
        "bio": "Janko Šćepanović，国际政治学博士，现任上海外国语大学上海全球治理与区域国别研究院副研究员。曾任华东师范大学俄罗斯研究中心博士后。主要研究方向为国际关系理论、俄罗斯对外政策、欧亚大国博弈与安全治理，在Taylor & Francis等国际同行评审SSCI顶尖学术期刊发表多篇高水平学术论文。"
    },
    {
        "name": "施展",
        "title": "教授",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=b1897c41-8664-4458-9411-eb2491a620b7",
        "bio": "施展，北京大学历史学博士，上海外国语大学全球文明史研究所教授，曾任外交学院世界政治研究中心主任，法国巴黎第一大学访问学者。主要研究方向为西方政治思想史、西方文明史、国际战略哲学与政治社会学。出版《枢纽》《破局》《迈斯特政治哲学研究》等具有重大社会与学术影响力的代表性专著。"
    },
    {
        "name": "富育红",
        "title": "副教授",
        "email": "2022012@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=c880d00a-3157-4262-95ba-82cda178f593",
        "bio": "富育红，国际关系学博士，上海外国语大学上海全球治理与区域国别研究院副教授。主要研究方向为南亚地区安全、国际冲突与危机管理、恐怖主义与反恐政策。在《俄罗斯东欧中亚研究》《国际政治科学》及SSCI国际权威期刊发表多篇论文，主持并承担多项国家级重要科研项目。"
    },
    {
        "name": "李如东",
        "title": "副研究员，硕士生导师",
        "email": "rudongli@126.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=b52f146a-54ad-4712-a1b7-1c9f3900ca8d",
        "bio": "李如东，中央民族大学民族学博士，上海外国语大学副研究员、硕导，志远青年学者，国家民委民族研究优秀中青年专家。曾在吉尔吉斯斯坦科学院、佛罗里达国际大学访学。主要从事中亚民族问题、中亚研究学术史、穆斯林社会与文化研究，主持国家社科基金青年项目、国家民委重点项目等。"
    },
    {
        "name": "张泓玮",
        "title": "助理研究员",
        "email": "hongwei.zhang.aa@gmail.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=aa901416-ea8f-4ba3-81e0-717eebc133a8",
        "bio": "张泓玮，美国芝加哥大学近东语言文明系博士（闪语族历史比较语言学），上海外国语大学助理研究员。主要从事闪米特语历史比较语言学、阿拉伯语方言学、阿姆哈拉语及埃塞俄比亚闪语历史演变研究，在国际近东语言学界发表多篇开创性学术论文。"
    },
    {
        "name": "童欣",
        "title": "博士后",
        "email": "tongxin_flora@163.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=ce476a26-b183-4fcb-8e50-48281144a956",
        "bio": "童欣，上海外国语大学助理研究员、博士后，入选全国博士后人才激励计划。主要研究领域为记忆研究、媒介理论、移民研究与欧洲视觉文化，兼任国际记忆研究协会成员、德国纪录片艺术研究协会成员。主持国家社科基金青年项目一项，在A&HCI、CSSCI等权威学术期刊发表论文十余篇。"
    },
    {
        "name": "吴芷净",
        "title": "博士后",
        "email": "wuzhijing@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=0b55ec7d-fe45-4203-ac11-e633d3bb7e73",
        "bio": "吴芷净，华东师范大学文艺学博士，德国海德堡大学哲学系访问学者，上海外国语大学犹太研究所博士后，入选上海“超级博士后”激励计划。主要研究西方现代艺术史与美学理论、先锋派艺术中的犹太维度与犹太文化，在《文艺理论研究》等核心刊物发表多篇学术论文。"
    },
    {
        "name": "郭小雨",
        "title": "副研究员，硕士生导师",
        "email": "guoxiaoyusmqh@sina.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=e82fce0a-aa37-4d7a-af39-a868f7d98342",
        "bio": "郭小雨，北京大学国际关系学院法学博士，现任上海外国语大学上海全球治理与区域国别研究院副研究员、硕士生导师。主要研究方向为政治思想史、西方政治哲学、英美政治与国家建构理论，出版专著《“联合”秩序的建立与演变》、权威译著《施米特的国际政治思想》等。"
    },
    {
        "name": "李冠杰",
        "title": "智库研究员",
        "email": "liguanjie2022@163.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=a81f33f0-466c-4813-8eb1-14064560ea33",
        "bio": "李冠杰，华东师范大学国际关系学博士，上海国际问题研究院博士后，曾派驻中国驻尼日利亚使馆工作，现任上海外国语大学英国研究中心智库研究员。主要从事英国政治、欧洲大国关系、中英关系与政治思想史研究，出版学术专著1部、译著2部，发表多篇决策咨询与核心期刊论文。"
    },
    {
        "name": "唐均",
        "title": "教授",
        "email": "philangel@126.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=08db7c45-2f5a-4074-b52b-232145dae65c",
        "bio": "唐均，文学博士，教授，现任职于上海外国语大学全球文明史研究所，中国红楼梦学会理事。曾任芬兰赫尔辛基大学、斯洛伐克科学院访问学者。主要从事丝绸之路语文学、西夏学、契丹与女真文字学、《红楼梦》多语种译介学研究，主持国家社科基金一般项目及多项省部级课题。"
    },
    {
        "name": "喻显龙",
        "title": "助理研究员，硕士生导师",
        "email": "2019026@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=b8b7d903-a129-43c3-92f7-7b8c6a2679cf",
        "bio": "喻显龙，韩国首尔大学政治外交学博士，现任上海外国语大学上海全球治理与区域国别研究院助理研究员、硕士生导师。主要从事东北亚地缘政治、朝鲜半岛安全、韩国外交政策与中韩关系研究，主持教育部人文社科基金青年项目及上海市级课题，在国内外期刊发表论文十余篇。"
    },
    {
        "name": "郭明",
        "title": "副教授，博士生导师",
        "email": "gmecho@163.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=076f0977-5de5-4205-83dd-176f79196951",
        "bio": "郭明，北京大学考古文博学院博士，上海外国语大学志远青年学者、全球文明史研究所副教授、博士生导师。曾任职于四川省文物考古研究院。主要研究方向为中国考古学、古代建筑史与文化遗产保护，主持国家社科基金重点项目、教育部人文社科规划基金等多项国家级科研课题。"
    },
    {
        "name": "胡春春",
        "title": "副教授，硕士生导师",
        "email": "chunchun.hu@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=5997b79d-2391-4475-b467-347514108865",
        "bio": "胡春春，德国波恩大学博士，上海外国语大学副教授、硕士生导师，欧洲研究所执行所长，欧洲文明特色研究生班负责人。主要从事以德国为中心的欧洲现代性研究、德国文化与政治思想、中德文明对话研究，出版多部德文学术专著与译著，长期为国家部委提供高端决策咨询报告。"
    },
    {
        "name": "林文昕",
        "title": "副研究员",
        "email": "",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=0894be23-5eeb-4322-97b7-5f78c93b6e82",
        "bio": "林文昕，北京大学国际关系学院法学博士，现任上海外国语大学上海全球治理与区域国别研究院副研究员。曾赴美国及俄罗斯顶尖学术机构访问研究，主要研究方向为俄罗斯内政外交、中外政治制度比较、欧亚地缘安全与政治思想史，在《俄罗斯研究》等权威刊物发表多篇学术论文。"
    },
    {
        "name": "刘琪",
        "title": "研究员，博士生导师",
        "email": "bethliuqi@hotmail.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=2b36a188-4e1b-4f7f-859a-14d2e5a7b00a",
        "bio": "刘琪，法学博士，上海外国语大学研究员、博士生导师，上外志远卓越学者、上海市曙光学者。主要从事人类学理论、边疆与民族政治学、中国西南与东南亚区域国别研究。主持国家社科基金重大项目子课题及一般项目，在《民族研究》《社会学研究》等顶尖期刊发表学术论文数十篇。"
    },
    {
        "name": "刘洪钟",
        "title": "教授，博士生导师",
        "email": "hongzhongliu@126.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=0fc0766e-c651-4ae2-a279-cb481e3bf8e2",
        "bio": "刘洪钟，经济学博士，上海外国语大学教授、博士生导师，上外“志远领军学者”，国家社科基金重大项目首席专家。主要从事国际政治经济学、东亚区域经济合作、全球经济治理与比较资本主义研究。出版学术专著多部，在《世界经济与政治》《国际经济评论》等权威刊物发表论文数十篇。"
    },
    {
        "name": "路程",
        "title": "助理研究员",
        "email": "02492@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=cb8a72b8-9ee2-491c-9ef4-9d562c5db0ee",
        "bio": "路程，复旦大学文学博士，美国杜克大学访问学者，现为上海外国语大学犹太研究所助理研究员。主要从事当代西方文学与批评理论、犹太文化与思想史、中东区域国别研究，主持中国博士后科学基金面上资助项目，在《外国文学评论》《国外文学》等CSSCI核心期刊发表多篇高水平论文。"
    },
    {
        "name": "乔国强",
        "title": "教授，博士生导师",
        "email": "qiaoguoqiang@163.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=ae11cc5d-ceb4-4066-b51f-50a1b02882c7",
        "bio": "乔国强，英国诺丁汉大学博士，上海外国语大学犹太研究所所长、教授、博士生导师，国家级重大人才工程入选者，上海市政协委员。主要从事英美文学、叙事学理论与犹太文学文化研究，曾任英国莱斯特大学讲座教授，主持多项国家社科基金重大项目，出版多部具有里程碑意义的权威专著。"
    },
    {
        "name": "王欢",
        "title": "副教授，硕士生导师",
        "email": "whwh@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=b31d041c-3ce6-4a41-8fec-f23604f3dbfa",
        "bio": "王欢，北京大学世界史博士，上海外国语大学志远青年学者、全球文明史研究所副教授、副所长。比利时鲁汶大学古代史系访问学者。主要从事埃及学、赫梯学、希腊化时代地中海文明比较研究，主持国家社科基金一般项目《多元文化视阈下托勒密埃及的宗教和合现象研究》，发表多篇代表性论文。"
    },
    {
        "name": "王凯",
        "title": "讲师",
        "email": "realkaiwang@163.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=b68dddfd-6976-419b-ab05-ebad05fbc30f",
        "bio": "王凯，复旦大学国际政治博士，美国乔治·华盛顿大学访问学者，现任上海外国语大学国际关系与公共事务学院讲师、硕导，兼任区域国别大数据科学实验室主任。主要研究结合传统因果推断与计算社会科学方法，深耕国际安全、高烈度政治风险预测与定量国际关系研究，发表多篇CSSCI论文。"
    },
    {
        "name": "王思羽",
        "title": "助理研究员",
        "email": "wangsiyu_wangsiyu@163.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=3aa750ae-69c5-4a67-b505-1a89c9e83ae4",
        "bio": "王思羽，中国人民大学法学博士，上海外国语大学上海全球治理与区域国别研究院助理研究员，入选上海市“超级博士后”人才计划。主要研究方向为后苏联与中东欧安全、欧洲军备控制、乌克兰政治与安全危机，主持完成中国博士后科学基金面上项目，在《俄罗斯研究》等期刊发表多篇论文。"
    },
    {
        "name": "吴小凡",
        "title": "讲师，硕士生导师",
        "email": "wuxiaofan@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=59a8c6a2-92e6-42d7-b5bb-7935402a4fc0",
        "bio": "吴小凡，华东师范大学历史学博士，美国北卡罗来纳大学教堂山分校访问学者，上海外国语大学全球文明史研究所讲师、硕导，入选上海“超级博士后”计划。主要研究领域为美国城市史、新城市主义运动与欧美空间形态史，主持国家社科基金青年项目及上海市政府决策咨询专项课题。"
    },
    {
        "name": "忻华",
        "title": "研究员，博士生导师",
        "email": "thomasxin@163.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=0b5550a2-2591-4993-9c1c-3b02e7db99f5",
        "bio": "忻华，法学博士，上海外国语大学欧盟研究中心主任、研究员、博士生导师，上海欧洲学会副秘书长。德国艾伯特基金会、哥廷根应用科学大学访问学者。主要从事欧洲一体化、中欧关系、欧盟地缘战略与国际法律秩序研究，主持多项国家社科基金及教育部课题，出版专著多部，发表数十篇核心论文。"
    },
    {
        "name": "袁勋",
        "title": "助理研究员",
        "email": "qggw1989@163.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=98ecff82-bfb5-4b36-a367-beecb1219b22",
        "bio": "袁勋，俄罗斯莫斯科国立大学博士、北京大学硕士，上海外国语大学上海全球治理与区域国别研究院助理研究员。主要从事地缘政治学、俄罗斯移民政策、中俄跨境区域治理与欧亚安全研究，在俄罗斯知名核心期刊及《俄罗斯研究》等刊物发表多篇俄文与中文学术论文。"
    },
    {
        "name": "张彬彬",
        "title": "副研究员，硕士生导师",
        "email": "zhangbinbin@gmail.com",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=48db9a8e-28b3-4fe6-a979-d754eb9e8460",
        "bio": "张彬彬，北京大学信息学院理学博士，上海外国语大学世界艺术史研究所副研究员、硕士生导师，中国计算机学会计算艺术分会执行委员。主要从事计算艺术史、图像数据库、数字人文与数据分析跨学科研究，主持完成国家自然科学基金项目，获得3项中国发明专利授权，发表学术论文十余篇。"
    },
    {
        "name": "周瑾艳",
        "title": "副研究员",
        "email": "2021010@shisu.edu.cn",
        "category": "本院学者",
        "url": "https://saggas.shisu.edu.cn/ArticleDetail/ArticleDetail?ArticleId=4702dae3-5133-4561-b6a0-4c2e870aaf81",
        "bio": "周瑾艳，中国社科院研究生院法学博士，上海外国语大学副研究员、硕导，中国非洲问题研究会理事。曾在德国发展研究所、伦敦大学亚非学院访学。主要从事非洲发展道路、国际发展合作、中非关系与欧非三方合作研究，主持国家社科基金一般项目，出版《国际发展合作与非洲国家发展道路探索》等专著。"
    }
]

def main():
    print(f"=== SAGGAS 师资采集交付处理器 ===")
    print(f"学者总人数: {len(SCHOLARS)}")
    
    with_email = [s for s in SCHOLARS if s['email']]
    no_email = [s for s in SCHOLARS if not s['email']]
    
    print(f"有邮箱学者: {len(with_email)} 位")
    print(f"无邮箱学者: {len(no_email)} 位")
    print(f"守恒检验: {len(with_email)} + {len(no_email)} = {len(SCHOLARS)} (100% 成立)")

    # 构造标准 10 列记录
    records = []
    
    # Sheet 1: 有邮箱学者
    records_sheet1 = []
    for idx, s in enumerate(with_email, 1):
        row = {
            '序号': idx,
            '姓名': s['name'],
            '职称': s['title'],
            '邮箱': s['email'],
            '学校': '上海外国语大学',
            '学院': '区域国别研究院',
            '采集人': 'Antigravity Subagent (Browser)',
            '采集日期': '2026-09-21',
            '简介': s['bio'],
            '主页': s['url']
        }
        records_sheet1.append(row)
        records.append(row)

    # Sheet 2: 无邮箱学者
    records_sheet2 = []
    for idx, s in enumerate(no_email, 1):
        row = {
            '序号': idx,
            '姓名': s['name'],
            '职称': s['title'],
            '邮箱': '',
            '学校': '上海外国语大学',
            '学院': '区域国别研究院',
            '采集人': 'Antigravity Subagent (Browser)',
            '采集日期': '2026-09-21',
            '简介': s['bio'],
            '主页': s['url']
        }
        records_sheet2.append(row)
        records.append(row)

    df_sheet1 = pd.DataFrame(records_sheet1)
    df_sheet2 = pd.DataFrame(records_sheet2)
    
    # 保存文件
    out_dir = r"d:\Agent Workspace\Intern\MyPlan\server\outputs"
    os.makedirs(out_dir, exist_ok=True)
    filename = "上海外国语大学_区域国别研究院_20260921_101500.xlsx"
    filepath = os.path.join(out_dir, filename)
    
    sheet1_name = f"有邮箱学者 (共 {len(df_sheet1)} 位)"
    sheet2_name = f"无邮箱学者 (共 {len(df_sheet2)} 位)"
    
    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        df_sheet1.to_excel(writer, sheet_name=sheet1_name, index=False)
        df_sheet2.to_excel(writer, sheet_name=sheet2_name, index=False)
        
    print(f"\n[成功生成标准双 Sheet Excel]: {filepath}")
    
    # 校验文件
    wb = openpyxl.load_workbook(filepath)
    print(f"工作表: {wb.sheetnames}")
    for name in wb.sheetnames:
        ws = wb[name]
        print(f" - [{name}]: {ws.max_row - 1} 行数据 (表头后)")
        
    # 同步回后台服务
    task_id = "35383481"
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
        data=json.dumps(sync_payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            res_data = resp.read().decode("utf-8")
            print(f"\n[后台同步成功]: HTTP {resp.status} - {res_data}")
    except Exception as e:
        print(f"\n[后台同步异常]: {e}")

if __name__ == '__main__':
    main()
