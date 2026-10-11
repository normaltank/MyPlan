"""A person settles a roster link whose page never names the teacher (集美大学师范学院).

赵红霞's roster link opens the CMS prompt '您无权访问此页面'. Without a review kind the
college showed 待人工复核 with nothing to review.
"""
import json
from pathlib import Path

import pytest

from server import profile_page_review as review, review_registry
from server.faculty_extractor import FacultyCandidate

ROSTER = "https://teacher.x.edu.cn/szdw/jys.htm"
PAGE = "https://teacher.x.edu.cn/content.jsp?wbnewsid=5505"
DENIED = ('<html><head><title>系统提示</title></head><body><table><tr><td class="prompt">'
          '<div>系统提示</div><div>抱歉</div><p>您无权访问此页面</p></td></tr></table></body></html>')


def _audit(tmp_path, html=DENIED):
    attempt = tmp_path / "attempt-1"
    snapshots = attempt / "x_snapshots"
    snapshots.mkdir(parents=True)
    (snapshots / "profile_001.html").write_text(html, encoding="utf-8")
    path = attempt / "x_audit.json"
    path.write_text(json.dumps({
        "unresolved": [{"url": PAGE, "name": "赵红霞", "status": review.STATUS}],
        # Written on the Windows server; only the file name and its folder are used here.
        "snapshots": [{"kind": "profile", "url": PAGE,
                       "path": "D:\\outputs\\tasks\\t\\attempt-1\\x_snapshots\\profile_001.html"}],
        "eligibility": [{"name": "赵红霞", "profile_url": PAGE, "roster_context": "2022-01-17赵红霞"}],
        "coverage": {"profile_sources": {PAGE: [ROSTER]}},
    }, ensure_ascii=False), encoding="utf-8")
    return path


def test_item_shows_the_card_and_the_page_that_does_not_name_the_teacher(tmp_path):
    path = _audit(tmp_path)
    [item] = review.collect_items(path)
    assert item["name"] == "赵红霞" and item["roster_url"] == ROSTER
    assert item["card_text"] == "2022-01-17赵红霞" and "您无权访问此页面" in item["page_text"]
    assert review_registry.has_review(path) and not review_registry.review_complete(path)


def test_a_decision_completes_the_review_and_page_text_change_voids_it(tmp_path):
    path = _audit(tmp_path)
    [item] = review.collect_items(path)
    review.save_decision(path.parent / review.DECISIONS_FILE, item, "roster_only")
    assert review_registry.review_complete(path)
    decisions = review.load_decisions(path.parent / review.DECISIONS_FILE)
    changed = DENIED.replace("您无权访问此页面", "页面已迁移")
    assert review.review_id(PAGE, "赵红霞", review.page_digest(changed)) not in decisions
    # Markup outside the visible text (a visit counter id) does not void it.
    assert review.page_digest(DENIED.replace("<table>", '<table id="c1">')) == review.page_digest(DENIED)


def test_roster_only_collects_from_the_roster_and_releases_the_page(tmp_path):
    path = _audit(tmp_path)
    [item] = review.collect_items(path)
    review.save_decision(path.parent / review.DECISIONS_FILE, item, "roster_only")
    others = FacultyCandidate("陈旭", "https://teacher.x.edu.cn/info/7262.htm", "教授", "陈旭")
    candidates = [FacultyCandidate("赵红霞", PAGE, "教师", "2022-01-17赵红霞"), others]
    sources = {("赵红霞", PAGE): {"university_faculty_roster"}, ("陈旭", others.url): {"university_faculty_roster"}}
    kept, kept_sources, released, applied = review.apply_decisions(
        candidates, sources, {PAGE: DENIED}, review.load_decisions(path.parent / review.DECISIONS_FILE))
    assert [(c.name, c.url) for c in kept] == [("赵红霞", ROSTER), ("陈旭", others.url)]
    assert kept_sources[("赵红霞", ROSTER)] == {"university_faculty_roster"}
    assert released == {PAGE} and applied[0]["verdict"] == "roster_only"


def test_not_teacher_leaves_the_name_out(tmp_path):
    path = _audit(tmp_path)
    [item] = review.collect_items(path)
    review.save_decision(path.parent / review.DECISIONS_FILE, item, "not_teacher")
    kept, sources, released, _ = review.apply_decisions(
        [FacultyCandidate("赵红霞", PAGE, "教师", "")], {("赵红霞", PAGE): {"r"}}, {PAGE: DENIED},
        review.load_decisions(path.parent / review.DECISIONS_FILE))
    assert kept == [] and sources == {} and released == {PAGE}


def test_a_flagged_name_without_its_saved_page_cannot_be_completed(tmp_path):
    path = _audit(tmp_path)
    (path.parent / "x_snapshots" / "profile_001.html").unlink()
    assert review.collect_items(path) == []
    assert not review_registry.review_complete(path)


def test_review_page_offers_both_verdicts(tmp_path):
    from server.image_bio_review import render_review_page
    items = review.collect_items(_audit(tmp_path))
    page = render_review_page([], profile_page_items=items, profile_page_decision_url="/api/x")
    assert "按名录收录" in page and "不是本院教师" in page and "您无权访问此页面" in page


@pytest.mark.asyncio
@pytest.mark.browser
async def test_reviewed_link_is_delivered_from_the_roster(tmp_path):
    from playwright.async_api import async_playwright
    from server.browser_batch_tool import run_plan

    good = "https://teacher.x.edu.cn/info/7262.htm"
    pages = {ROSTER: f'<ul><li><a href="{PAGE}">赵红霞</a></li><li><a href="{good}">陈旭</a></li></ul>',
             PAGE: DENIED,
             good: '<div class="v_news_content"><p>陈旭，教授，从事课程与教学论研究。</p></div>'}
    plan = {"school": "样本大学", "college": "师范学院", "source_kind": "university_faculty_roster",
            "branches": [{"url": ROSTER, "single_page": True, "card_selector": "li",
                          "profile_content_selector": "div.v_news_content"}]}

    async def harvest(out):
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                context = await browser.new_context()

                async def serve(route):
                    await route.fulfill(body=pages.get(route.request.url, ""),
                                        content_type="text/html; charset=utf-8")

                await context.route("**/*", serve)
                return await run_plan(plan, context, out)
            finally:
                await browser.close()

    first = await harvest(tmp_path / "attempt-1")
    audit_path = Path(first["audit_path"])
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert {u["status"] for u in audit["unresolved"]} >= {review.STATUS}
    [item] = review.collect_items(audit_path)
    review.save_decision(tmp_path / "attempt-2" / review.DECISIONS_FILE, item, "roster_only")
    second = await harvest(tmp_path / "attempt-2")
    audit = json.loads(Path(second["audit_path"]).read_text(encoding="utf-8"))
    assert audit["unresolved"] == [] and audit["total"] == 2
    records = json.loads(Path(audit["records_path"]).read_text(encoding="utf-8"))
    row = next(r for r in records if r["姓名"] == "赵红霞")
    assert row["主页"] == '' and row["简介"] == ""
    assert any(source['profile_url'] == ROSTER for source in audit['coverage']['field_sources'])
    assert audit["profile_page_reviews"][0]["verdict"] == "roster_only"

# This component fixture stipulates verified source purpose; purpose integration is unmocked elsewhere.
pytestmark = pytest.mark.verified_source_boundary
