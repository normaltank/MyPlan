"""Contract regressions found in the Sept-2026 architecture review.
Each test failed on the pre-review code (see comments)."""
import time

import server.faculty_extractor as fx
from server.faculty_extractor import (FacultyDirectoryExtractor as D, FacultyHarvestPipeline,
                                      FacultyProfileExtractor as P)

NAV = ("<div class='menu'><a href='/xygk/'>学院概况</a><a href='/kxyj/'>科学研究</a>"
       "<a href='/djgz/'>党建工作</a></div>")
BASE = "https://x.edu.cn/szdw/"

def _page(body):
    return f"<html><body>{NAV}{body}</body></html>"

def test_l2_layer_is_actually_loaded():
    # before: import silently failed -> every page used the keyword-blacklist fallback
    assert fx.L2_AVAILABLE and fx.mine_records is not None

def test_text_list_page():
    # before: ['科学研究', '党建工作'] (navigation delivered as faculty)
    html = _page("<div class='article'><p>教授：张三、李四、王五<br>副教授：赵六、孙七、周八</p></div>")
    c = D.extract(html, BASE)
    assert [x.name for x in c] == ["张三", "李四", "王五", "赵六", "孙七", "周八"]
    assert {x.title for x in c[3:]} == {"副教授"} and all(x.url == BASE for x in c)

def test_profile_less_faculty_kept():
    # before: 陈刚/刘洋 (no link) silently dropped
    lis = "".join(f"<li><a href='/t/{i}.htm'>{n}</a><span>教授</span></li>" for i, n in enumerate(["张伟", "李娜", "王强", "赵敏"]))
    lis += "".join(f"<li><span>{n}</span><span>讲师</span></li>" for n in ["陈刚", "刘洋"])
    names = [x.name for x in D.extract(_page(f"<ul class='t'>{lis}</ul>"), BASE)]
    assert names == ["张伟", "李娜", "王强", "赵敏", "陈刚", "刘洋"]

def test_homonyms_are_distinct_people():
    # before: second 王伟 merged away by name-only dedupe
    lis = "".join(f"<li><a href='/t/{i}.htm'>{n}</a></li>" for i, n in enumerate(["王伟", "李娜", "王伟", "赵敏", "陈刚"]))
    c = D.extract(_page(f"<ul class='t'>{lis}</ul>"), BASE)
    assert sum(x.name == "王伟" for x in c) == 2

def test_low_probability_records_not_exported():
    # before: '更多>>' exported because the GROUP had U > 0
    lis = "".join(f"<li><a href='/t/{i}.htm'>{n}</a></li>" for i, n in enumerate(["张伟", "李娜", "王强", "赵敏", "更多>>"]))
    assert "更多>>" not in [x.name for x in D.extract(_page(f"<ul class='t'>{lis}</ul>"), BASE)]

def test_email_next_to_han_not_truncated():
    # before: 'zhangwei@x.edu' (\\b treats 联 as a word char)
    html = "<div class='content'><h2>张伟</h2><p>邮箱：zhangwei@x.edu.cn联系电话：123</p></div>"
    assert P.extract(html, "u", "张伟").email == "zhangwei@x.edu.cn"

def test_obfuscated_email():
    # before: ''
    html = "<div class='content'><h2>张伟</h2><p>Email: zhangwei(at)x.edu.cn</p></div>"
    assert P.extract(html, "u", "张伟").email == "zhangwei@x.edu.cn"

def _dir(n):
    lis = "".join(f"<li><a href='/t/{i}.htm'>{x}</a></li>" for i, x in enumerate(["张伟", "李娜", "王强", "赵敏", "陈刚"][:n]))
    return _page(f"<ul class='t'>{lis}</ul>")

def test_footer_email_rejected_by_detail_df():
    # before: every teacher without a personal address got the college mailbox
    def fetch(url):
        body = "<p>邮箱：wq@x.edu.cn</p>" if url.endswith("/2.htm") else ""
        return (f"<html><body><div class='x1'><h2>T</h2><p>教授</p>{body}</div>"
                f"<div class='x9'>地址：某路1号 邮箱：xyb@x.edu.cn</div></body></html>")
    r = FacultyHarvestPipeline.harvest_directory(_dir(5), BASE, fetch)
    got = {x["姓名"]: x["邮箱"] for x in r["sheet1_with_email"]}
    assert got == {"王强": "wq@x.edu.cn"} and len(r["sheet2_no_email"]) == 4

def test_fetch_failure_is_unresolved_not_sheet2():
    # before: a TimeoutError aborted the whole college; a fetcher returning ''
    # on timeout put the teacher into Sheet 2 with is_conserved=True
    def fetch(url):
        if url.endswith("/1.htm"):
            raise TimeoutError(url)
        return "<div class='content'><p>教授</p></div>"
    r = FacultyHarvestPipeline.harvest_directory(_dir(5), BASE, fetch)
    assert [u["姓名"] for u in r["unresolved"]] == ["李娜"]
    assert not r["is_conserved"] and not r["delivery_ready"]
    assert r["total_count"] + len(r["unresolved"]) == 5

def test_email_regex_linear_time():
    # before (Opus/text_lists._EMAIL_SPAN): 20k-char alnum run took ~11 s
    from text_lists import email_spans
    t0 = time.perf_counter()
    email_spans("a" * 50000 + " x@y.cn")
    assert time.perf_counter() - t0 < 0.5
