"""python test_production.py  -> prints diagnostics, asserts invariants."""
from bs4 import BeautifulSoup

from email_matching import extract_email_mentions
from iso_mining import (css_path, infer_template_nodes, mine_records, norm_entropy,
                        url_templates)
from template_infer import infer_template_nodes_robust

NAMES10 = ["张伟", "李娜", "王强", "赵敏", "陈刚", "刘洋", "孙丽", "周杰", "吴昊", "郑爽"]
HASH = ["5d/4e", "5d/51", "5d/4b", "5d/ab", "5e/03", "5d/5f", "5d/cc", "5d/60", "5e/1a", "5d/de"]
NAV = ("<ul class='nav'><li><a href='/xygk/list.htm'>学院概况</a></li><li><a href='/szdw/list.htm'>师资队伍</a></li>"
       "<li><a href='/kxyj/list.htm'>科学研究</a></li><li><a href='/djgz/list.htm'>党建工作</a></li></ul>")


def banner(t):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


# ---------------------------------------------------------------- T1
def t1_vsb_hash_urls():
    banner("T1  VSB hex-hash URLs: template entropy")
    urls = [f"https://law.x.edu.cn/{h}/c13687a1549{50+i}/page.htm" for i, h in enumerate(HASH)]
    t = url_templates(urls)
    print("templates:", set(t), " H_norm =", round(norm_entropy(t), 3))
    assert norm_entropy(t) == 0.0
    cards = "".join(f"<li><a href='/{h}/c13687a1549{50+i}/page.htm'><img src='x.jpg'><p>{n}</p></a></li>"
                    for i, (h, n) in enumerate(zip(HASH, NAMES10)))
    soup = BeautifulSoup(f"<body>{NAV}<ul class='list'>{cards}</ul></body>", "lxml")
    g = mine_records(soup, page_url="https://law.x.edu.cn/szdw/list.htm").best
    print("best:", g.texts(), "U=%.2f" % g.utility, g.features["url_entropy"])
    assert g and g.size == 10 and g.utility > 8


# ---------------------------------------------------------------- T2
TEXT_PAGE = """<body>""" + NAV + """<div class="article">
<p>学院现有专任教师56人，其中教授18人、副教授22人，讲师16人。</p>
<p><strong>教授（4人）：</strong>张三、李四（博导）、欧阳明华、<a href="/info/1023/77.htm">王五</a><br>
副教授：刘某；陈某 ；郗鉴<br>
讲师：王　伟　李小强　赵　敏<br>
外籍教师：John Smith, Mary Lee</p>
<p>博士后：孙丽周杰吴昊</p>
<p>研究方向：法理学、宪法学、行政法学、刑法学、民商法学</p>
</div></body>"""


def t2_text_lists():
    banner("T2  plain-text name lists -> virtual records")
    soup = BeautifulSoup(TEXT_PAGE, "lxml")
    res = mine_records(soup, page_url="https://x.edu.cn/szdw/list.htm")
    for g in [res.best] + res.alternatives:
        print(f"group U={g.utility:.2f} container={css_path(g.container)[-40:]}")
        for i, r in enumerate(g.records):
            v = r[0]
            tag = ("ACCEPT" if i in g.features["accept"] else
                   "review" if i in g.features["review"] else "noise ")
            print(f"   {tag} {v.core:14s} p={g.record_probs[i]:.2f} section={v.section} "
                  f"annot={v.annot} via={v.via} href={v.href} ptr={css_path(v)[-14:]}")
    accepted = {g.records[i][0].core for g in [res.best] + res.alternatives for i in g.features["accept"]}
    review = {g.records[i][0].core for g in [res.best] + res.alternatives for i in g.features["review"]}
    expect = {"张三", "李四", "欧阳明华", "王五", "刘某", "陈某", "王伟", "李小强", "赵敏",
              "John Smith", "Mary Lee", "孙丽", "周杰", "吴昊"}
    missing = expect - accepted - review
    print("missing:", missing, "| rare-surname review:", review)
    assert not missing
    assert "郗鉴" in accepted | review
    prose = [c for c in res.candidates if "法理学" in c.texts() or any("专任教师" in t for t in c.texts())]
    print("prose/research groups U:", [round(c.utility, 2) for c in prose])
    assert all(c.utility <= 0 for c in prose)


# ---------------------------------------------------------------- T3
def page_t3(names, counter, date, ticker):
    lis = "".join(f"<li><a href='/info/1023/{i}.htm'>{n}</a></li>" for i, n in enumerate(names))
    return f"""<body><div class="top">今天是 {date} <span id="tk">{ticker}</span></div>{NAV}
<ul class="list">{lis}</ul>
<div class="foot"><p>访问量：{counter} ｜ 办公室邮箱：office@law.x.edu.cn</p>
<p><a href="mailto:dean@law.x.edu.cn">院长信箱</a></p><p>版权所有 法学院</p></div></body>"""


def t3_robust_template():
    banner("T3  robust template inference (counters / dates / rotating ticker)")
    a = BeautifulSoup(page_t3(NAMES10, 10243, "2026年9月22日 星期二", "我院召开教学工作会议"), "lxml")
    b = BeautifulSoup(page_t3(["何平", "高远", "罗兰", "张伟"], 10311, "2026年9月23日 星期三",
                              "学院获批国家社科基金重大项目"), "lxml")
    a2 = BeautifulSoup(page_t3(NAMES10, 10244, "2026年9月22日 星期二", "图书馆开放时间调整通知"), "lxml")

    strict = infer_template_nodes(a, [b])
    robust, dbg = infer_template_nodes_robust(a, [b], refetches=[a2], return_debug=True)
    print("theta =", dbg["theta"], "volatile leaves =", dbg["volatile"])
    for k, v in dbg["scores"].items():
        print(f"   s={v:.2f}  {k}")
    for name, ids in (("strict", strict), ("robust", robust)):
        em = extract_email_mentions(a, ids)
        print(f"{name:6s}: " + ", ".join(f"{e.address} template={e.in_template}" for e in em))
    em = {e.address: e.in_template for e in extract_email_mentions(a, robust)}
    assert em["office@law.x.edu.cn"] and em["dean@law.x.edu.cn"]
    data_leaves = [l for l in a.select("ul.list a")]
    assert not any(id(l) in robust for l in data_leaves), "teacher leaves must stay data"
    g = mine_records(a, page_url="https://law.x.edu.cn/szdw/list.htm", template_ids=robust).best
    print("mining with robust template ->", g.texts())
    assert g.size == 10


if __name__ == "__main__":
    t1_vsb_hash_urls()
    t2_text_lists()
    t3_robust_template()
    print("\nALL TESTS PASSED")
