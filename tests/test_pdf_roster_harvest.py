"""End to end in real Chromium: a roster page whose only content is a PDF.

The pages, the PDF and the people are synthetic; requests are answered by a
Playwright route, so nothing reaches a real website.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading

from openpyxl import load_workbook
from playwright.async_api import async_playwright
import pytest

from server.browser_batch_tool import run_plan
from tests.test_pdf_roster import TEXT_PAGES, _pdf

PDF_BYTES = _pdf(TEXT_PAGES)


@pytest.fixture
def site(monkeypatch):
    """A local HTTP site: pages for the browser, the PDF for its request context."""
    served = {"pdf": (PDF_BYTES, "application/pdf"), "requested": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            served["requested"].append(path)
            if path == "/files/mentors.pdf":
                body, content_type = served["pdf"]
            elif path in served["pages"]:
                body, content_type = served["pages"][path].encode("utf-8"), "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.setenv(name, "127.0.0.1,localhost")
    served["root"] = f"http://127.0.0.1:{server.server_address[1]}"
    yield served
    server.shutdown()





@pytest.fixture(autouse=True)
def _no_real_ocr_engine(monkeypatch):
    import sys
    import types
    fake = types.ModuleType("rapidocr_onnxruntime")
    fake.RapidOCR = lambda: pytest.fail("a text-layer PDF must not load OCR")
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake)


def _pages(card):
    return {
        "/info/1132/19852.htm": ('<html><body><header>学院首页 师资队伍</header>'
                                 f'<div id="content">{card}</div></body></html>'),
        "/system/pdfjs/viewer.html": "<html><body>viewer</body></html>",
        "/szdw/list.htm": '<main><ul class="list"><li><a href="/szdw/zhangwei.htm">张伟</a> 教授</li></ul></main>',
        "/szdw/zhangwei.htm": ('<html><body><article><h1>张伟</h1><p>职称：教授</p>'
                               '<p>张伟，主要从事机器人与智能制造研究，完整个人主页简介。</p>'
                               '<p>邮箱：zhangwei@example.edu.cn</p></article></body></html>'),
    }


async def _run(site, tmp_path, card, *, listing=False, source_kind="university_faculty_roster"):
    site["pages"] = _pages(card)
    root = site["root"]
    branches = [{"url": root + "/info/1132/19852.htm", "card_selector": "#content", "single_page": True,
                 "source_kind": source_kind}]
    if listing:
        branches.append({"url": root + "/szdw/list.htm", "card_selector": "ul.list > li", "single_page": True})
    plan = {"school": "样本大学", "college": "样本学院", "branches": branches}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        report = await run_plan(plan, context, tmp_path)
        await browser.close()
    records = json.loads(Path(report["records_path"]).read_text(encoding="utf-8"))
    return report, records


VIEWER = '<iframe src="/system/pdfjs/viewer.html?file=%2Ffiles%2Fmentors.pdf"></iframe>'
LINK = '<p><a href="/files/mentors.pdf">导师名单下载</a></p>'


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize("card", [VIEWER, LINK], ids=["pdfjs_viewer", "download_link"])
async def test_pdf_roster_page_delivers_people_from_the_pdf(site, tmp_path, card):
    report, records = await _run(site, tmp_path, card)
    embed = site["root"] + "/info/1132/19852.htm"

    assert report["unresolved"] == [] and report["delivery_ready"] is True
    assert "/files/mentors.pdf" in site["requested"]
    by_name = {row["姓名"]: row for row in records}
    assert sorted(by_name) == ["张伟", "李娜", "王强"]
    assert by_name["李娜"]["简介"] == ("李娜，女，副教授\n主讲机械原理，发表论文十余篇。\n"
                                       "曾获省级教学成果奖。邮箱：lina@example.edu.cn")
    assert by_name["李娜"]["职称"] == "副教授" and by_name["李娜"]["邮箱"] == "lina@example.edu.cn"
    assert by_name["王强"]["邮箱"] == "" and by_name["王强"]["主页"] == embed
    assert report["total"] == 3 and report["with_email"] == 2 and report["no_email"] == 1

    observation = next(item for item in report["roster_observations"] if item["url"] == embed)
    assert observation["status"] == "pdf_roster_reconciled" and observation["extracted_candidates"] == 3
    source = next(item for item in report["coverage"]["field_sources"]
                  if item["profile_url"] == embed and records[item["record_index"] - 1]["姓名"] == "李娜")
    assert source["attribution_scope"] == "pdf_roster_record"
    assert source["segment_record"]["pdf_pages"] == [1, 2]
    evidence = report["pdf_roster_evidence"][0]
    assert Path(evidence["original_path"]).read_bytes() == PDF_BYTES

    workbook = load_workbook(report["excel_path"])
    with_email, without = workbook["教师列表"], workbook["无邮箱教师列表"]
    assert [cell.value for cell in with_email[1]] == ["序号", "姓名", "职称", "邮箱", "学校", "学院",
                                                      "采集人", "采集日期", "简介", "主页"]
    assert with_email.max_row - 1 + without.max_row - 1 == report["total"]


@pytest.mark.asyncio
@pytest.mark.browser
async def test_pdf_and_web_observations_of_one_person_are_not_counted_twice(site, tmp_path):
    report, records = await _run(site, tmp_path, VIEWER, listing=True)

    names = [row["姓名"] for row in records]
    assert sorted(names) == ["张伟", "李娜", "王强"]
    merge = next(item for item in report["identity_merges"] if item["name"] == "张伟")
    assert merge["kept_url"] == site["root"] + "/szdw/zhangwei.htm"
    assert merge["merged_list_urls"] == [site["root"] + "/info/1132/19852.htm"]


@pytest.mark.asyncio
@pytest.mark.browser
async def test_reading_the_pdf_does_not_approve_its_source(site, tmp_path):
    report, records = await _run(site, tmp_path, VIEWER, source_kind="hospital_doctor_roster")

    assert records == [] and report["delivery_ready"] is False
    assert {item["status"] for item in report["unresolved"]} == {"profile_unavailable"}
    assert {item["status"] for item in report["eligibility"]} == {"unresolved"}


@pytest.mark.asyncio
@pytest.mark.browser
async def test_html_served_for_the_pdf_keeps_the_source_review_stop(site, tmp_path):
    site["pdf"] = (b"<html>login</html>", "text/html")
    report, records = await _run(site, tmp_path, VIEWER)

    assert records == [] and report["delivery_ready"] is False
    assert {item["status"] for item in report["unresolved"]} == {
        "embedded_pdf_roster_requires_review", "pdf_roster_not_pdf"}


@pytest.mark.asyncio
@pytest.mark.browser
async def test_table_pdf_roster_waits_for_the_name_review_then_delivers(site, tmp_path):
    from server import name_review
    from tests.test_pdf_table_roster import _table_pdf
    site["pdf"] = (_table_pdf(), "application/pdf")

    report, records = await _run(site, tmp_path, VIEWER)

    assert report["delivery_ready"] is False
    assert [item["status"] for item in report["unresolved"]] == [name_review.STATUS]
    assert len(records) == 7 and {row["简介"] for row in records} == {""}
    assert {row["姓名"]: row["职称"] for row in records}["孙三立"] == "讲师（校聘副教授）"
    # The 实验技术人员 section of the same PDF is read too.
    assert {"吴六", "郑七"} <= {row["姓名"] for row in records}

    audit = Path(report["audit_path"])
    [item] = name_review.collect_items(audit)
    name_review.save_decision(audit.parent / name_review.DECISIONS_FILE, item, "person", "Alex Morgan")
    report, records = await _run(site, tmp_path, VIEWER)

    assert report["unresolved"] == [] and report["delivery_ready"] is True
    assert report["total"] == 8 and report["no_email"] == 8
    workbook = load_workbook(report["excel_path"])
    assert workbook["教师列表"].max_row - 1 + workbook["无邮箱教师列表"].max_row - 1 == 8
