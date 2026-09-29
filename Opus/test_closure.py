"""python test_closure.py  - fusion A/B, text-span e-mail exclusivity, L3 synthesis."""
import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
import json

from bs4 import BeautifulSoup

from email_matching import (DomainConventionModel, build_weight_matrix, extract_email_mentions,
                            match_flow, persons_from_group)
from iso_mining import FusionConfig, mine_records, name_prob
from wrapper_synth import Page, Validator, WrapperRegistry, build_prompt, synthesize

NAMES = ["张伟", "李娜", "王强", "赵敏", "陈刚", "刘洋", "孙丽", "周杰", "吴昊", "郑爽",
         "何平", "高远", "罗兰", "梁静", "谢飞", "宋佳", "唐宁", "许诺", "韩梅", "冯军",
         "邓超", "曹阳", "彭博", "曾毅", "肖然", "田甜", "董洁", "袁泉"]


def banner(t):
    print("\n" + "=" * 72 + f"\n{t}\n" + "=" * 72)


# ---------------------------------------------------------------- 1
def test_fusion_ab():
    banner("1  multiplicative vs log-odds fusion: mixed personal-homepage links")
    # half the staff link to /info/, half to a personal-homepage system with
    # pinyin paths: realistic URL heterogeneity inside a genuine faculty list
    py = ["zhangwei", "lina", "wangqiang", "zhaomin", "chengang"]
    lis = []
    for i, n in enumerate(NAMES[:10]):
        href = (f"/info/1023/{4000 + i}.htm" if i % 2 == 0
                else f"https://faculty.x.edu.cn/{py[i // 2]}/zh_CN/index.htm")
        lis.append(f"<li><a href='{href}'>{n}</a></li>")
    nav = ("<ul><li><a href='/xygk/list.htm'>学院概况</a></li><li><a href='/kxyj/list.htm'>科学研究</a></li>"
           "<li><a href='/djgz/list.htm'>党建工作</a></li><li><a href='/xyxw/list.htm'>学院新闻</a></li></ul>")
    pager = "<div>" + "".join(f"<a href='/szdw/list{k}.htm'>{k}</a>" for k in range(1, 5)) + "</div>"
    soup = BeautifulSoup(f"<body>{nav}<ul class='t'>{''.join(lis)}</ul>{pager}</body>", "lxml")
    for mode in ("product", "logit"):
        res = mine_records(soup, page_url="https://x.edu.cn/szdw/list1.htm", cfg=FusionConfig(mode=mode))
        g = res.best
        fac = next(c for c in res.candidates if c.size == 10)
        print(f"[{mode:7s}] best={'None' if g is None else f'{g.size} records U={g.utility:.2f}'}"
              f"  faculty group: url_entropy={fac.features['url_entropy']:.3f} U={fac.utility:.2f}")
        if mode == "product":
            assert g is None or g.size < 10, "legacy rule should veto this group"
        else:
            assert g is not None and g.size == 10
    rej = [c for c in mine_records(soup, page_url="https://x.edu.cn/szdw/list1.htm").candidates
           if c.utility <= 0]
    print("logit still rejects:", [(c.texts()[:3], round(c.utility, 2)) for c in rej][:3])


# ---------------------------------------------------------------- 2
def test_text_span_emails():
    banner("2  span-level exclusivity for text-list persons")
    html = """<body><div class="article">
<p>教授：张三（zhangs@univ.edu.cn）、李四（lisi@163.com）<br>
副教授：王五（wangw@univ.edu.cn）、赵六 zhaoliu(at)univ.edu.cn、孙七<br>
讲师：周八 zb2020@qq.com　吴九 wujiu@univ.edu.cn<br>
课题组：郑十、钱一（共用邮箱 grp@univ.edu.cn）</p>
<p>联系我们：办公室邮箱：office@univ.edu.cn</p></div></body>"""
    soup = BeautifulSoup(html, "lxml")
    persons = persons_from_group(mine_records(soup).best, name_prob)
    emails = extract_email_mentions(soup)
    conv = DomainConventionModel(to_pinyin=lambda n: None)
    conv.observe([e.address for e in emails])
    df = {e.address: 1 for e in emails}
    W, reason, feats = build_weight_matrix(persons, emails, conv, df, df)
    r = match_flow(persons, emails, W, reason)
    got = {m.name: m.address for m in r.matches}
    for m in r.matches:
        print(f"  {m.name} <- {m.address:22s} conf={m.confidence:.3f} margin={m.margin:.2f}")
    for e, why in r.unassigned:
        print(f"  UNASSIGNED {e.address} ({why})")
    assert got == {"张三": "zhangs@univ.edu.cn", "李四": "lisi@163.com", "王五": "wangw@univ.edu.cn",
                   "赵六": "zhaoliu@univ.edu.cn", "周八": "zb2020@qq.com",
                   "吴九": "wujiu@univ.edu.cn", "钱一": "grp@univ.edu.cn"}
    names = [p.name for p in persons]
    no_mail = set(names) - set(got)
    print(f"  conservation: {len(names)} = {len(got)} + {len(no_mail)} {no_mail}")
    assert len(names) == len(got) + len(no_mail) == 9


# ---------------------------------------------------------------- 3
NAV = ("<div id='nav'><ul class='menu'><li><a href='/xygk/list.htm'>学院概况</a></li>"
       "<li><a href='/szdw/list.htm'>师资队伍</a></li><li><a href='/kxyj/list.htm'>科学研究</a></li>"
       "<li><a href='/djgz/list.htm'>党建工作</a></li></ul></div>")
LEADERS = ("<div class='leaders'><ul class='wp_article_list'>"
           "<li class='list_item i1'><span class='Article_Title'><a href='/5d/01/c13687a150001/page.htm'>黄涛</a></span></li>"
           "<li class='list_item i2'><span class='Article_Title'><a href='/5d/02/c13687a150002/page.htm'>林峰</a></span></li>"
           "</ul></div>")


def vsb_list(items, start):
    lis = []
    for k, (title, extra) in enumerate(items):
        h = f"{(start + k) * 7 % 256:02x}/{(start + k) * 13 % 256:02x}"
        cls = f"list_item i{k + 1} {'odd' if k % 2 == 0 else 'even'}"
        lis.append(f"<li class='{cls}'><div class='fields pr_fields'><span class='Article_Title'>"
                   f"<a href='/{h}/c13687a15{start + k:04d}/page.htm' title='{title}'>{title}</a></span>"
                   f"<span class='Article_Info'>{extra}</span></div></li>")
    return f"<ul class='wp_article_list'>{''.join(lis)}</ul>"


def vsb_page(names, start, counter, kind="faculty"):
    items = [(n, "教授") for n in names] if kind == "faculty" else [(n, "2026-09-01") for n in names]
    return (f"<html><body>{NAV}<div class='main'>{LEADERS}"
            f"<div class='faculty' id='wp_news_w6'>{vsb_list(items, start)}</div>"
            f"<div class='pager'><a href='/szdw/list1.htm'>1</a><a href='/szdw/list2.htm'>2</a>"
            f"<a href='/szdw/list3.htm'>3</a><a href='/szdw/list4.htm'>4</a></div></div>"
            f"<div class='foot'><p>访问量：{counter} ｜ 办公室邮箱：office@law.x.edu.cn</p></div></body></html>")


ROUND1 = {"variants": [
    {"strategy": "structure-anchored", "mode": "dom",
     "record_xpath": "/html/body/div[2]/div[2]/ul/li", "name_xpath": ".//a", "url_xpath": ".//a/@href"},
    {"strategy": "class-anchored", "mode": "dom",
     "record_xpath": "//li[contains(concat(' ', normalize-space(@class), ' '), ' odd ')]",
     "name_xpath": ".//a", "url_xpath": ".//a/@href"},
    {"strategy": "class-anchored", "mode": "dom",
     "record_xpath": "//ul[contains(concat(' ', normalize-space(@class), ' '), ' wp_article_list ')]/li",
     "name_xpath": ".//a", "url_xpath": ".//a/@href"},
]}
ROUND2 = {"variants": [
    {"strategy": "class-anchored", "mode": "dom",
     "record_xpath": "//div[contains(concat(' ', normalize-space(@class), ' '), ' faculty ')]"
                     "//li[contains(concat(' ', normalize-space(@class), ' '), ' list_item ')]",
     "name_xpath": ".//span[contains(concat(' ', normalize-space(@class), ' '), ' Article_Title ')]/a",
     "url_xpath": ".//a/@href"},
    {"strategy": "structure-anchored", "mode": "dom",
     "record_xpath": "//div[@id='wp_news_w6']/ul/li", "name_xpath": ".//a/@title", "url_xpath": ".//a/@href"},
    {"strategy": "link-pattern-anchored", "mode": "dom",
     "record_xpath": "//a[contains(@href, '/page.htm')]", "name_xpath": ".", "url_xpath": "@href"},
]}


class MockLLM:
    """Stands in for the model: round 1 returns flawed wrappers, round 2 (after
    receiving gate feedback in the prompt) returns corrected ones."""

    def __init__(self):
        self.calls, self.prompts = 0, []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        self.calls += 1
        if self.calls == 1:
            return json.dumps(ROUND1)
        assert "G1_coverage" in prompt and "G0_lint" in prompt, "feedback must reach the model"
        return "```json\n" + json.dumps(ROUND2, ensure_ascii=False) + "\n```"


def test_l3_synthesis():
    banner("3  L3 wrapper synthesis with acceptance gates")
    pages = [Page(f"list{k + 1}", f"https://law.x.edu.cn/szdw/list{k + 1}.htm",
                  vsb_page(NAMES[k * 7:(k + 1) * 7], k * 7, 10240 + k)) for k in range(4)]
    news = ["学院召开教学工作会议", "关于国庆节放假安排的通知", "我院教师获批国家社科基金",
            "2026级新生开学典礼举行", "法学院举办学术讲座"]
    negatives = [Page("news", "https://law.x.edu.cn/xyxw/list.htm", vsb_page(news, 90, 10250, "news"))]
    v = Validator(pages, negatives, n_train=2)
    print("L2 reference sizes (must):", {pid: len(r.must) for pid, r in v.refs.items()})
    llm = MockLLM()
    res = synthesize(v, llm, cms_hint="VSB/博达 CMS")
    for i, verdicts in enumerate(res.rounds, 1):
        print(f"-- round {i}")
        for vd in verdicts:
            status = "PASS" if vd.passed else "fail"
            fails = [f"{r.gate}:{json.dumps(r.detail, ensure_ascii=False)[:110]}"
                     for r in vd.reports if not r.passed]
            print(f"  [{status}] {vd.spec.strategy:22s} {vd.spec.record_xpath[:70]}")
            for f in fails:
                print(f"         {f}")
    assert res.accepted is not None and not res.escalate
    print("accepted:", res.accepted.spec.strategy, "| N-version agreement:", res.n_version_agreement)
    print("prompt size (chars):", len(llm.prompts[0]))

    reg = WrapperRegistry()
    reg.put("law.x.edu.cn|vsb|faculty-list", res.accepted)
    spec = reg.get("law.x.edu.cn|vsb|faculty-list")
    by_id = {p.pid: p for p in pages}
    assert reg.regress("law.x.edu.cn|vsb|faculty-list", spec, by_id) == []
    wide = ROUND1["variants"][2]
    from wrapper_synth import WrapperSpec
    diffs = reg.regress("law.x.edu.cn|vsb|faculty-list",
                        WrapperSpec(wide["record_xpath"], wide["name_xpath"], wide["url_xpath"]), by_id)
    print("regression of a wider recompiled wrapper differs on:", diffs)
    assert diffs


def test_l3_text_list():
    banner("4  L3 on a text-list cluster (mode=text_list)")
    def page(names_a, names_b, c):
        return (f"<html><body>{NAV}<div class='article'><p>教授：{'、'.join(names_a)}<br>"
                f"副教授：{'、'.join(names_b)}</p></div><div class='foot'>访问量：{c}</div></body></html>")
    pages = [Page(f"p{k}", f"https://wx.x.edu.cn/szdw/p{k}.htm",
                  page(NAMES[k * 6:k * 6 + 3], NAMES[k * 6 + 3:k * 6 + 6], 500 + k)) for k in range(4)]
    v = Validator(pages, [], n_train=2)
    spec_json = {"variants": [{"strategy": "class-anchored", "mode": "text_list",
                               "record_xpath": "//div[contains(concat(' ', normalize-space(@class), ' '), ' article ')]/p"}]}
    res = synthesize(v, lambda prompt: json.dumps(spec_json), max_rounds=1)
    for r in res.rounds[0][0].reports:
        print(f"  {r.gate:12s} {'PASS' if r.passed else 'fail'} {json.dumps(r.detail, ensure_ascii=False)[:90]}")
    assert res.accepted is not None


if __name__ == "__main__":
    test_fusion_ab()
    test_text_span_emails()
    test_l3_synthesis()
    test_l3_text_list()
    print("\nALL CLOSURE TESTS PASSED")
