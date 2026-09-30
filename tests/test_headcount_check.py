"""Headcount evidence independent of the scout's plan; it reports, never blocks."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from playwright.async_api import async_playwright

from server.faculty_extractor import FacultyCandidate
from server.harvest_workflow import observe_roster, unrecognized_card_names
from server.headcount_check import (census, compare, menu_links, pager_totals, site_scope,
                                    stated_counts)

MENU = ('<nav><a href="/index.htm">首页</a><a href="/xygk.htm">学院概况</a>'
        '<a href="/szdw/js.htm">教师名录</a><a href="/szdw/xzry.htm">行政人员</a></nav>')


def roster(names, *, pager=""):
    cards = "".join(f'<li class="t"><a href="/info/{i}.htm">{name}</a></li>' for i, name in enumerate(names))
    return f"<html><head><title>名录</title></head><body>{MENU}<ul class='list'>{cards}</ul>{pager}</body></html>"


TEACHERS = ["王建国", "李秀英", "张伟", "刘洋", "陈静"]
STAFF = ["赵丽华", "孙志强", "周敏"]
PAGES = {
    "/index.htm": f"<html><body>{MENU}<p>欢迎</p></body></html>",
    "/xygk.htm": f"<html><body>{MENU}<p>学院现有教职工 12 人，其中专任教师 9 人，教授 3 人。</p></body></html>",
    "/szdw/js.htm": roster(TEACHERS, pager="<div class='pb'>共5条 1/1页</div>"),
    "/szdw/xzry.htm": roster(STAFF).replace("<title>名录</title>", "<title>行政人员</title>"),
}


def test_site_scope_stays_inside_the_college_directory():
    inside = site_scope("https://www.univ.edu.cn/cs/index.htm")
    assert inside("https://www.univ.edu.cn/cs/szdw.htm")
    assert not inside("https://www.univ.edu.cn/math/szdw.htm")
    assert not inside("https://other.edu.cn/cs/szdw.htm")
    assert not inside("https://www.univ.edu.cn/cs/files/list.pdf")


def test_menu_links_are_the_links_a_site_repeats():
    pages = {"https://a.edu.cn/index.htm": PAGES["/index.htm"], "https://a.edu.cn/xygk.htm": PAGES["/xygk.htm"]}
    links = menu_links(pages, site_scope("https://a.edu.cn/"))
    assert "https://a.edu.cn/szdw/xzry.htm" in links and "https://a.edu.cn/szdw/js.htm" in links


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_page", ["entry", "branch", "supplied"])
async def test_census_marks_an_unreachable_seed_or_target_as_incomplete(monkeypatch, failed_page):
    from server import headcount_check
    urls = {"entry": "https://a.edu.cn/index.htm", "branch": "https://a.edu.cn/js.htm",
            "supplied": "https://a.edu.cn/extra.htm"}

    async def open_page(_context, url):
        return None if url == urls[failed_page] else "<html><title>页面</title><p>欢迎</p></html>"

    monkeypatch.setattr(headcount_check, "_open", open_page)
    found = await census(None, urls["entry"], {"branches": [{"url": urls["branch"]}]},
                         extra_urls=[urls["supplied"]])
    assert found["status"] == "partial"
    assert found["failed_urls"] == [urls[failed_page]]
    check = compare(found, [])
    assert check["status"] == "partial" and "普查未完整" in check["summary"]


def test_same_name_records_count_as_two_people_without_a_false_stated_gap():
    page = {"url": "https://a.edu.cn/js.htm", "opened": True, "in_plan": True,
            "names": ["张伟", "李秀英", "陈静"]}
    harvested = iter(["张伟", "张伟", "李秀英", "陈静"])
    check = compare({"status": "complete", "pages": [page]}, harvested,
                    stated=[{"label": "专任教师", "count": 4, "url": "https://a.edu.cn/about.htm"}])
    assert check["harvested"] == 4 and check["status"] == "consistent"
    assert check["not_harvested_count"] == 0 and check["census"]["harvested_not_seen"] == 0


def test_same_name_records_keep_missing_names_and_the_actual_record_count():
    page = {"url": "https://a.edu.cn/js.htm", "opened": True, "in_plan": True,
            "names": ["张伟", "李秀英", "陈静"]}
    check = compare({"status": "complete", "pages": [page]}, ["张伟", "张伟"],
                    stated=[{"label": "专任教师", "count": 3, "url": "https://a.edu.cn/about.htm"}])
    assert check["harvested"] == 2 and "介绍 3 人，入库 2 人" in check["summary"]
    assert {item["name"] for item in check["not_harvested"]} == {"李秀英", "陈静"}


def test_pager_total_and_stated_counts_are_compared_with_the_harvest():
    url = "https://a.edu.cn/szdw/js.htm"
    observations = [{"url": url, "visible_cards": 5}]
    assert pager_totals([(url, PAGES["/szdw/js.htm"], url)], observations) == [
        {"branch_url": url, "stated_total": 5, "captured_cards": 5, "consistent": True}]
    counts = stated_counts({"https://a.edu.cn/xygk.htm": PAGES["/xygk.htm"]})
    assert {(item["label"], item["count"]) for item in counts} == {("教职工", 12), ("专任教师", 9), ("教授", 3)}
    check = compare({"status": "complete", "pages": []}, TEACHERS, stated=counts)
    assert check["status"] == "difference" and "介绍 9 人，入库 5 人" in check["summary"]


def test_unrecognised_name_on_a_verified_card_goes_to_review_not_repair():
    html = roster(["王建国", "维罗妮卡"])
    url = "https://a.edu.cn/szdw/js.htm"
    known = [FacultyCandidate(name="王建国", url="https://a.edu.cn/info/0.htm")]
    observation = observe_roster(html, url, "ul.list > li", known)
    assert [card["text"] for card in observation["unaccounted_cards"]] == ["维罗妮卡"]
    [person] = unrecognized_card_names(observation, url, known)
    assert person.name == "维罗妮卡" and person.url == "https://a.edu.cn/info/1.htm"
    assert not observe_roster(html, url, "ul.list > li", [*known, person])["unaccounted_cards"]


@pytest.mark.asyncio
@pytest.mark.browser
async def test_census_reports_lists_outside_the_plan_with_their_pages():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAGES.get(self.path)
            self.send_response(200 if body else 404)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write((body or "").encode("utf-8"))

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{http.server_port}"
    plan = {"branches": [{"url": base + "/szdw/js.htm"}]}
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            found = await census(await browser.new_context(), base + "/index.htm", plan)
            await browser.close()
    finally:
        http.shutdown()
        http.server_close()
    pages = found.pop("html")
    check = compare(found, TEACHERS, stated=stated_counts(pages))
    assert found["status"] == "complete"
    assert {item["name"] for item in check["not_harvested"]} == set(STAFF)
    assert check["not_harvested_by_page"] == [{"url": base + "/szdw/xzry.htm", "title": "行政人员",
                                               "not_harvested": 3}]
    assert "行政人员 3 人" in check["summary"] and check["census"]["harvested_not_seen"] == 0
    assert check["roster_pages_outside_plan"] == [{"url": base + "/szdw/xzry.htm", "names": 3,
                                                   "not_harvested": 3}]


@pytest.mark.asyncio
async def test_worker_records_the_cross_check_without_changing_delivery(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    from server.agent_worker import AgentWorker
    roster_path = tmp_path / "roster.html"
    roster_path.write_text(PAGES["/szdw/js.htm"], encoding="utf-8")
    url = "https://a.edu.cn/szdw/js.htm"
    coverage = tmp_path / "x_coverage.json"
    coverage.write_text(json.dumps({"snapshots": [{"kind": "directory", "url": url, "path": str(roster_path)}],
                                    "pages": [{"url": url, "branch_url": url}],
                                    "roster_observations": [{"url": url, "visible_cards": 5}]}), encoding="utf-8")
    records = tmp_path / "records.json"
    records.write_text(json.dumps([{"姓名": name} for name in TEACHERS], ensure_ascii=False), encoding="utf-8")
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"delivery_ready": True}), encoding="utf-8")
    report = {"delivery_ready": True, "coverage_path": str(coverage), "records_path": str(records),
              "audit_path": str(audit)}
    worker = AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    monkeypatch.setenv("HARVEST_CROSS_CHECK", "1")

    async def fake_census(entry_url, plan, extra_urls=None):
        return {"status": "complete", "html": {"https://a.edu.cn/xygk.htm": PAGES["/xygk.htm"]}, "pages": [
            {"url": "https://a.edu.cn/szdw/xzry.htm", "opened": True, "title": "行政人员", "names": STAFF,
             "in_plan": False}]}

    monkeypatch.setattr(worker, "_census", fake_census)
    task = SimpleNamespace(url="https://a.edu.cn/", school="U", college="C")
    await worker._attach_cross_check(task, {"branches": [{"url": url}]}, report, None)
    check = report["cross_check"]
    assert report["delivery_ready"] is True and check["status"] == "difference"
    assert check["not_harvested_count"] == 3 and check["pager_totals"][0]["consistent"] is True
    assert json.loads(audit.read_text(encoding="utf-8"))["cross_check"]["summary"] == check["summary"]
    assert (tmp_path / "cross-check.json").is_file()

    monkeypatch.setenv("HARVEST_CROSS_CHECK", "0")
    report.pop("cross_check")
    await worker._attach_cross_check(task, {"branches": []}, report, None)
    assert "cross_check" not in report


def test_field_checks_trace_each_record_to_its_page():
    from server.field_checks import check_records, page_text
    pages = {"https://a.edu.cn/p/1.htm": page_text("<h1>王建国</h1><p>教授，邮箱 wjg@a.edu.cn</p>"),
             "https://a.edu.cn/p/2.htm": page_text("<h1>李秀英</h1><p>上一条：王建国</p>")}
    records = [
        {"姓名": "王建国", "主页": "https://a.edu.cn/p/1.htm", "邮箱": "wjg@a.edu.cn", "职称": "教授", "简介": "王建国，教授。"},
        {"姓名": "李秀英", "主页": "https://a.edu.cn/p/2.htm", "邮箱": "wjg@a.edu.cn", "职称": "党委书记",
         "简介": "上一条：王建国"},
        {"姓名": "张伟", "主页": "https://a.edu.cn/p/1.htm", "邮箱": "", "职称": "讲师", "简介": ""},
    ]
    result = check_records(records, pages)
    assert result["counts"] == {"email_not_on_source_page": 1, "email_on_several_records": 2,
                                "title_outside_vocabulary": 1, "biography_names_another_teacher": 1,
                                "name_not_on_source_page": 1}
    assert result["flagged_records"] == 3


def test_spot_check_sheet_samples_both_sheets_and_writes_an_oracle_template(tmp_path):
    import json
    from server.spot_check import build, sample
    records = [{"姓名": f"教师{i}", "主页": f"https://a.edu.cn/p/{i}.htm", "邮箱": f"t{i}@a.edu.cn" if i < 4 else "",
                "职称": "讲师", "简介": ""} for i in range(10)]
    chosen = sample(records, 6, seed=1)
    assert len(chosen) == 6 and sum(bool(row["邮箱"]) for row in chosen) == 3
    (tmp_path / "records.json").write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    page = tmp_path / "p0.html"
    page.write_text("<p>教师0 讲师 邮箱 t0@a.edu.cn</p>", encoding="utf-8")
    audit = tmp_path / "x_audit.json"
    audit.write_text(json.dumps({"school": "U", "college": "C", "total": 10, "with_email": 4,
                                 "records_path": str(tmp_path / "records.json"),
                                 "snapshots": [{"url": "https://a.edu.cn/p/0.htm", "path": str(page)}]},
                                ensure_ascii=False), encoding="utf-8")
    html_path, oracle_path = build(audit, size=10, seed=0)
    html = html_path.read_text(encoding="utf-8")
    assert html.count("<tr><td>") == 10 and "不超过约 30%" in html
    oracle = json.loads(oracle_path.read_text(encoding="utf-8"))
    assert oracle["sampled_profile_count"] == 10 and oracle["reviewed_independently"] is None
