"""Full run_plan flows in real Chromium: verified content reaches the records intact.

Every request is served from this file (context routing), so no school site
and no model is contacted. Each flow asserts the exact delivered field, not
merely that delivery was blocked.
"""
import json
from pathlib import Path

import pytest

from server import faculty_extractor

ROOT = "https://review.example.edu"
PLAN = {"school": "样本大学", "college": "样本学院", "source_kind": "university_faculty_roster"}


@pytest.fixture(autouse=True)
def no_production_wrapper_cache(monkeypatch):
    # The default L1 executor opens the real wrapper registry and task queue.
    monkeypatch.setattr(faculty_extractor, "get_default_l1_executor", lambda: None)


async def _run(tmp_path, pages, branch):
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import run_plan

    plan = {**PLAN, "branches": [{"url": ROOT + "/roster", "single_page": True, **branch}]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context()

            async def route(request):
                body = pages.get(request.request.url)
                if body is None:
                    await request.abort()
                else:
                    await request.fulfill(body=body, content_type="text/html; charset=utf-8")

            await context.route("**/*", route)
            report = await run_plan(plan, context, tmp_path)
        finally:
            await browser.close()
    records = json.loads(Path(report["records_path"]).read_text(encoding="utf-8"))
    return report, records


ONE_TEACHER_ROSTER = "<main><ul><li><a href='/person'>张三</a><span>教授</span></li></ul></main>"
PROFILE_START = "<article id='profile'><h1>张三</h1><p>教授，主要从事材料科学研究。</p>"


@pytest.mark.asyncio
@pytest.mark.browser
@pytest.mark.parametrize("body, check", [
    # A labelled address that does not spell the name is still the teacher's.
    ("<p>电子邮箱：bluebird@example.edu</p>",
     lambda record: record["邮箱"] == "bluebird@example.edu"),
    # A class name that merely contains 'bottom' is authored content.
    ("<div class='bottom-up-research'><p>研究方向：自底向上的纳米材料组装。</p></div>",
     lambda record: "研究方向：自底向上的纳米材料组装。" in record["简介"]),
    # Inside the verified container, header/footer elements and classes are the author's.
    ("<header>张三，材料学院教授</header><div class='footer'><p>原作者结尾：全部科研成果见附件。</p></div>"
     "<footer>个人主页最后更新：2026 年</footer>",
     lambda record: all(text in record["简介"] for text in
                        ("张三，材料学院教授", "原作者结尾：全部科研成果见附件。", "个人主页最后更新：2026 年"))),
], ids=["non_pinyin_labelled_email", "class_substring_biography", "verified_header_footer_blocks"])
async def test_verified_profile_fields_are_delivered_intact(tmp_path, body, check):
    pages = {ROOT + "/roster": ONE_TEACHER_ROSTER,
             ROOT + "/person": PROFILE_START + body + "</article><footer class='site-footer'>学院版权所有</footer>"}
    report, records = await _run(tmp_path, pages, {"card_selector": "main li",
                                                   "profile_content_selector": "#profile"})
    assert len(records) == 1, report["unresolved"]
    assert check(records[0]), records[0]
    assert "学院版权所有" not in records[0]["简介"]
    assert report["delivery_ready"] is True, report["unresolved"]


@pytest.mark.asyncio
@pytest.mark.browser
async def test_shared_team_page_emails_follow_each_teachers_card(tmp_path):
    roster = ("<main><ul>"
              "<li><a href='/team'>王晓岚</a><span>教授</span></li>"
              "<li><a href='/team'>李强</a><span>副教授</span></li>"
              "<li><a href='/team'>赵敏</a><span>讲师</span></li>"
              "</ul></main>")
    team = ("<main><div class='member'><h3>王晓岚</h3><p>教授 邮箱：sunflower88@163.com</p></div>"
            "<div class='member'><h3>李强</h3><p>副教授</p><p><a href='mailto:strong_li@126.com'>写信</a></p></div>"
            "<div class='member'><h3>赵敏</h3><p>讲师 研究方向：数据库</p></div>"
            "<p>学院办公室：cs-office@example.edu</p></main>")
    report, records = await _run(tmp_path, {ROOT + "/roster": roster, ROOT + "/team": team},
                                 {"card_selector": "main li"})
    emails = {record["姓名"]: record["邮箱"] for record in records}
    assert emails == {"王晓岚": "sunflower88@163.com", "李强": "strong_li@126.com", "赵敏": ""}, report["unresolved"]
    assert report["delivery_ready"] is True, report["unresolved"]


@pytest.mark.asyncio
@pytest.mark.browser
async def test_shared_team_page_address_without_owner_evidence_blocks_delivery(tmp_path):
    roster = ("<main><ul><li><a href='/team'>王晓岚</a></li><li><a href='/team'>李强</a></li></ul></main>")
    team = ("<main><div class='member'><h3>王晓岚</h3><p>教授 sunflower88@163.com</p></div>"
            "<div class='member'><h3>李强</h3><p>副教授</p></div></main>")
    report, records = await _run(tmp_path, {ROOT + "/roster": roster, ROOT + "/team": team},
                                 {"card_selector": "main li"})
    assert {record["姓名"]: record["邮箱"] for record in records} == {"王晓岚": "", "李强": ""}
    assert report["delivery_ready"] is False
    assert any(item.get("status") == "ambiguous_email_assignment" for item in report["unresolved"])

# This component fixture stipulates verified source purpose; purpose integration is unmocked elsewhere.
pytestmark = pytest.mark.verified_source_boundary
