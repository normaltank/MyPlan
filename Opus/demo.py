"""Synthetic end-to-end check: python demo.py"""
from collections import Counter

from bs4 import BeautifulSoup

from email_matching import (DomainConventionModel, build_weight_matrix, extract_email_mentions,
                            match_1to1, match_flow, persons_from_group)
from iso_mining import css_path, infer_template_nodes, mine_records, name_prob

NAV = """<div class="nav"><ul>
<li class="on"><a href="/xygk/list.htm">学院概况</a></li><li><a href="/szdw/list.htm">师资队伍</a></li>
<li><a href="/kxyj/list.htm">科学研究</a></li><li><a href="/rcpy/list.htm">人才培养</a></li>
<li><a href="/djgz/list.htm">党建工作</a></li><li><a href="/xyxw/list.htm">学院新闻</a></li></ul></div>"""
FOOT = """<div class="foot"><p>地址：无锡市蠡湖大道1800号</p>
<p>办公室邮箱：office@jiangnan.edu.cn</p><p>版权所有 © 某某学院</p></div>"""
PAGER = """<div class="pager"><a href="/szdw/list1.htm">1</a><a href="/szdw/list2.htm">2</a>
<a href="/szdw/list3.htm">3</a><a href="/szdw/list2.htm">下一页</a><a href="/szdw/list3.htm">尾页</a></div>"""


def grid(names, per_row=5):
    rows = []
    for i in range(0, len(names), per_row):
        cells = "".join(f'<td class="{"odd" if k % 2 else "even"}"><span><a href="/info/1023/{4000+i+k}.htm">'
                        f'{n}</a></span></td>' for k, n in enumerate(names[i:i + per_row]))
        rows.append(f"<tr>{cells}</tr>")
    return f"<table>{''.join(rows)}</table>"


def page_grid(prof, assoc, leaders):
    lead = "".join(f'<li><a href="/info/1011/{900+i}.htm">{n}</a> {t}</li>' for i, (n, t) in enumerate(leaders))
    return f"""<html><body>{NAV}<div class="main">
<div class="leaders"><h3>学院领导</h3><ul>{lead}</ul></div>
<div class="content"><h3>教授</h3>{grid(prof)}<h3>副教授</h3>{grid(assoc)}</div>
{PAGER}</div>{FOOT}</body></html>"""


def card(i, name, title, extra):
    return (f'<li><a href="/info/1023/{5000+i}.htm"><div class="img"><img src="/p/{i}.jpg"></div></a>'
            f'<div class="txt"><h3>{name}</h3><p>{title}</p>{extra}</div></li>')


CARDS = [
    ("张伟", "教授", "<p>邮箱：8843120002@jiangnan.edu.cn</p>"),
    ("李娜", "副教授", "<p>E-mail: sunnyflower88@163.com</p>"),
    ("王强", "教授", "<p>邮箱：8843120011@jiangnan.edu.cn</p><p>备用邮箱：wq_lab@qq.com</p>"),
    ("赵敏", "讲师", "<p>邮箱：biolab2020@163.com</p>"),
    ("陈刚", "讲师", "<p>邮箱：biolab2020@163.com</p>"),
    ("刘洋", "副教授", "<p>电话/邮箱：0510-859112348843120007@jiangnan.edu.cn</p>"),
    ("孙丽", "讲师", "<p>办公室：理科楼301</p>"),
    ("周杰", "教授", "<p>邮箱：8843120019(at)jiangnan.edu.cn</p>"),
    ("吴昊", "副教授", "<p>邮箱：wuhao@jiangnan.edu.cn</p>"),
]


def page_cards(cards):
    lis = "".join(card(i, *c) for i, c in enumerate(cards))
    return f"<html><body>{NAV}<div class='main'><ul class='teachers'>{lis}</ul>{PAGER}</div>{FOOT}</body></html>"


TOY_PINYIN = {"张": "zhang", "伟": "wei", "李": "li", "娜": "na", "王": "wang", "强": "qiang",
              "赵": "zhao", "敏": "min", "陈": "chen", "刚": "gang", "刘": "liu", "洋": "yang",
              "孙": "sun", "丽": "li", "周": "zhou", "杰": "jie", "吴": "wu", "昊": "hao"}


def toy_pinyin(name):
    return [TOY_PINYIN[c] for c in name] if all(c in TOY_PINYIN for c in name) else None


def part1():
    print("=" * 70, "\nPART 1  L2 isomorphic mining on a grid page\n" + "=" * 70)
    a = BeautifulSoup(page_grid(["张伟", "李娜", "王强", "赵敏", "陈刚", "刘洋", "孙丽", "周杰"],
                                ["吴昊", "郑爽", "欧阳明", "马超"],
                                [("黄涛", "院长"), ("林峰", "副院长")]), "lxml")
    b = BeautifulSoup(page_grid(["何平", "高远", "罗兰"], ["梁静", "谢飞"],
                                [("黄涛", "院长"), ("林峰", "副院长")]), "lxml")
    tpl = infer_template_nodes(a, [b])
    res = mine_records(a, page_url="https://x.edu.cn/szdw/list1.htm", template_ids=tpl)
    g = res.best
    print("best group:", g.texts())
    print("utility=%.2f  container=%s" % (g.utility, css_path(g.container)))
    print("features:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in g.features.items()})
    for alt in res.alternatives:
        print("alternative:", alt.texts(), "U=%.2f" % alt.utility)
    print("\nrejected (top 6 by |U|):")
    rej = sorted((c for c in res.candidates if c.utility <= 0), key=lambda c: c.utility)[:6]
    for c in rej:
        print("  U=%7.2f n=%2d %s" % (c.utility, c.size, [t[:12] for t in c.texts()][:6]))


def part2():
    print("\n" + "=" * 70, "\nPART 2  email attribution on a card page\n" + "=" * 70)
    a = BeautifulSoup(page_cards(CARDS), "lxml")
    b = BeautifulSoup(page_cards([("何平", "教授", "<p>邮箱：8843120030@jiangnan.edu.cn</p>")]), "lxml")
    tpl = infer_template_nodes(a, [b])
    group = mine_records(a, page_url="https://x.edu.cn/szdw/list1.htm", template_ids=tpl).best
    persons = persons_from_group(group, name_prob)
    print("persons:", [p.name for p in persons])

    emails = extract_email_mentions(a, tpl)
    conv = DomainConventionModel(to_pinyin=toy_pinyin)
    conv.observe([e.address for e in emails] +
                 [f"88431200{k:02d}@jiangnan.edu.cn" for k in range(40, 60)])  # site-wide sample
    for e in emails:                                   # repair glued phone + staff id
        fixed, phone, amb = conv.split_glued_numeric(e.address)
        if fixed != e.address:
            e.note = f"split phone={phone} ambiguous={amb} from {e.address}"
            e.address = fixed
    conv.fit([("wuhao@jiangnan.edu.cn", "吴昊"), ("zhangs@jiangnan.edu.cn", "张三")])

    # df_site: number of distinct person records carrying the address (+ other pages)
    rec_of = {}
    for p in persons:
        for n in p.record_nodes:
            rec_of[id(n)] = p.pid
    df_site = Counter()
    for addr in {e.address for e in emails}:
        owners = set()
        for e in emails:
            if e.address == addr:
                owners |= {rec_of[id(x)] for x in e.node.parents if id(x) in rec_of} or {"page"}
        df_site[addr] = len(owners)
    df_site["office@jiangnan.edu.cn"] += 40            # seen on every page of the site
    df_global = dict(df_site)

    for e in emails:
        print(f"  mention {e.mid}: {e.address:32s} template={e.in_template!s:5} "
              f"label={e.label_before!s:5} role={e.role_label!s:5} {e.note}")

    W, reason, feats = build_weight_matrix(persons, emails, conv, df_site, df_global)
    for solver in (match_1to1, match_flow):
        r = solver(persons, emails, W, reason)
        print(f"\n[{solver.__name__}]")
        for m in sorted(r.matches, key=lambda m: m.pid):
            print(f"  {m.name:4s} <- {m.address:30s} conf={m.confidence:.3f} "
                  f"margin={m.margin:.2f} {m.flags}")
        for e, why in r.unassigned:
            print(f"  UNASSIGNED {e.address:30s} reason={why}")
    no_mail = {p.name for p in persons} - {m.name for m in match_flow(persons, emails, W, reason).matches}
    print("\npersons without email:", no_mail,
          "| conservation:", len(persons), "=",
          len({m.pid for m in match_flow(persons, emails, W, reason).matches}), "+", len(no_mail))


if __name__ == "__main__":
    part1()
    part2()
