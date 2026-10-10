"""补充名单（机构设置、导师分专业名录）与主名录的同名合并，以及相关抽取修正。

用户决定（2026-09-30）：补充名单上的名字与学院里唯一一位有个人主页的教师同名时
是同一人，保留主页那条；只出现在补充名单上的人照常收录（生僻姓先复核）；
同名的个人主页超过一个时仍送人工复核。行政人员名单不采（侦察说明）。
"""
from io import BytesIO
import json
from pathlib import Path
import sys
import types

from PIL import Image, ImageDraw
import pytest

from server.browser_batch_tool import run_plan
from server.faculty_extractor import FacultyCandidate, FacultyDirectoryExtractor
from server.faculty_identity import identity_review_issues, merge_same_name_observations
from server.harvest_workflow import (CapturedRosterPage, candidates_inside_cards, observe_roster,
                                     reconcile_rosters)
from server.name_list import chains, is_name_list_page, people_chains, reconcile_name_list
from server.segmented_roster import verification_summary

ORG_URL = "https://sx.example.edu.cn/info/1256/5647.htm"
ORG_PAGE = """<html><body><div id="vsb_content"><div class="v_news_content">
<p>一、数学与计算机科学学院办公室</p>
<p>三、教学系：数学与应用数学系（马万）、计算机科学与技术系（田彦山）</p>
<p>四、教研室：</p>
<p>函数论教研室（徐丽、董白英、马万、职占江、张永勇）</p>
<p>统计学教研室（陈志恩、刘媚、丁芳、田彦山）</p>
<p>宁夏（固原）物联大数据技术创新应用中心</p>
</div></div></body></html>"""
ORG_SELECTOR = "#vsb_content > div.v_news_content > p"


def test_chains_split_people_and_ignore_unit_names():
    runs = people_chains("函数论教研室（徐丽、董白英、职占江、张永勇）")
    assert len(runs) == 1
    assert runs[0].names == ["徐丽", "董白英", "张永勇"] and runs[0].rare == ["职占江"]
    assert people_chains("三、教学系：数学与应用数学系（马万）、计算机科学与技术系（田彦山）") == []
    assert people_chains("宁夏（固原）物联大数据技术创新应用中心") == []
    assert not any(chain.is_people for chain in chains("土木工程、水利工程、道路桥梁"))
    # a padded two-character name inside a list
    assert people_chains("王 奔、张艳艳、赵汉杰")[0].names == ["王奔", "张艳艳", "赵汉杰"]


def test_name_list_page_is_detected_and_every_item_accounted_for():
    assert is_name_list_page(ORG_PAGE, ORG_SELECTOR)
    result = reconcile_name_list(ORG_PAGE, ORG_URL, ORG_SELECTOR)
    assert sorted(c.name for c in result.found) == sorted(
        ["徐丽", "董白英", "马万", "张永勇", "陈志恩", "刘媚", "丁芳", "田彦山"])
    assert [c.name for c in result.review] == ["职占江"]
    assert all(c.url == ORG_URL for c in result.found)
    assert result.unresolved == []
    assert result.observation["kind"] == "name_list" and result.observation["list_cards"] == 2
    # an ordinary one-person-per-card roster is not a name list
    roster = "<ul id='r'>" + "".join(f"<li><a href='/t/{i}.htm'>{n}</a> 教授</li>"
                                     for i, n in enumerate(["王建国", "李明", "张伟"])) + "</ul>"
    assert not is_name_list_page(roster, "#r > li")


def test_supervisor_table_cells_including_a_single_supervisor():
    html = """<table id="t"><tbody><tr class="firstRow"><td>代码</td><td>专业</td><td>导师</td></tr>
    <tr><td>085901</td><td>土木工程</td><td><p>陈厚、尤再进、董平、司炳成</p></td></tr>
    <tr><td>085902</td><td>水利工程</td><td><p>张帅</p></td></tr></tbody></table>"""
    selector = "#t > tbody > tr:not(.firstRow) > td:nth-of-type(3)"
    assert is_name_list_page(html, selector)
    result = reconcile_name_list(html, "https://c.example.edu.cn/info/2284.htm", selector)
    assert sorted(c.name for c in result.found) == sorted(["陈厚", "尤再进", "董平", "司炳成", "张帅"])
    assert result.observation["single_name_cards"] == 1


def test_unrecognized_item_in_a_people_chain_is_reported_not_dropped():
    result = reconcile_name_list("<p id='x'>张伟、李明、王芳、ABCD</p>", "https://c.edu/l.htm", "#x")
    assert [item["item"] for item in result.unresolved] == ["ABCD"]
    assert result.unresolved[0]["status"] == "name_list_item_unrecognized"


def test_reconcile_keeps_separate_same_name_source_observations():
    roster_url = "https://sx.example.edu.cn/szdw/js1.htm"
    roster = ("<table id='r'>" + "".join(
        f"<tr><td><a href='/info/1226/{i}.htm'>{n}</a></td></tr>"
        for i, n in enumerate(["徐丽", "董白英", "马万", "陈志恩", "刘媚", "丁芳", "田彦山"]))
        + "</table>")
    plan = {"school": "样本大学", "college": "数学学院", "branches": [
        {"url": roster_url, "card_selector": "#r tr", "single_page": True},
        {"url": ORG_URL, "card_selector": ORG_SELECTOR, "single_page": True}]}
    result = reconcile_rosters([CapturedRosterPage(roster_url, roster, plan["branches"][0]),
                                CapturedRosterPage(ORG_URL, ORG_PAGE, plan["branches"][1])], plan)
    statuses = [item["status"] for item in result.unresolved]
    assert statuses == ["rare_surname_needs_review"]   # 职占江 only; no card mismatch
    kept, sources, merges = merge_same_name_observations(
        result.candidates, result.candidate_sources, {roster_url, ORG_URL})
    assert len(kept) == 15 and merges == []
    assert sum(c.name == "徐丽" for c in kept) == 2
    assert next(c for c in kept if c.name == "张永勇").url == ORG_URL
    records = [{"姓名": c.name, "主页": c.url} for c in kept]
    assert len(identity_review_issues(records)) == 7



def test_merge_rule_edges():
    list_a, list_b = "https://c.edu/a.htm", "https://c.edu/b.htm"
    two_pages = [FacultyCandidate("张伟", "https://c.edu/p1.htm"), FacultyCandidate("张伟", "https://c.edu/p2.htm"),
                 FacultyCandidate("张伟", list_a)]
    kept, _, merges = merge_same_name_observations(two_pages, {}, {list_a})
    assert len(kept) == 3 and merges == []          # two personal pages: still reviewed
    only_lists = [FacultyCandidate("李明", list_a), FacultyCandidate("李明", list_b)]
    kept, sources, merges = merge_same_name_observations(
        only_lists, {("李明", list_a): {"x"}, ("李明", list_b): {"y"}}, {list_a, list_b})
    assert [(c.name, c.url) for c in kept] == [("李明", list_a)]
    assert sources[("李明", list_a)] == {"x", "y"} and merges[0]["rule"] == "same_name_lists_without_pages"


def test_padded_two_character_names_account_for_their_cards():
    html = "<table><tr class='r'><td>王 奔</td><td>学生事务办公室</td></tr><tr class='r'><td>张艳艳</td></tr></table>"
    people = [FacultyCandidate("王奔", "https://c.edu/x.htm"), FacultyCandidate("张艳艳", "https://c.edu/x.htm")]
    observation = observe_roster(html, "https://c.edu/x.htm", "tr.r", people)
    assert observation["unaccounted_cards"] == []


def test_names_outside_the_verified_cards_are_not_roster_entries():
    url = "https://c.edu/dsjs.htm"
    html = ("<div class='nav'><a href='/szdw/dwjs.htm'>师资队伍</a><a href='/dwgz/twfc.htm'>党务工作</a></div>"
            "<table id='t'><tr><td><a href='/info/1.htm'>李星教授介绍</a></td></tr></table>")
    menu = [FacultyCandidate("师资队伍", "https://c.edu/szdw/dwjs.htm")]
    inside, outside = candidates_inside_cards(html, url, "#t tr", menu)
    assert inside == [] and outside == menu
    assert FacultyDirectoryExtractor._clean_name("李星教授介绍") == ("李星", "教授")
    assert FacultyDirectoryExtractor._clean_name("张慧副教授简介") == ("张慧", "副教授")
    assert FacultyDirectoryExtractor._clean_name("王 奔") == ("王奔", "教师")


def test_segmented_heading_that_lists_several_people_is_rejected():
    html = """<div id="c"><table><tr><td><p>陈厚、尤再进、董平、司炳成、赵英</p></td></tr>
    <tr><td><p>吴孟泉、李雪艳、杨继松、石洪源</p></td></tr></table></div>"""
    summary = verification_summary(html, "https://c.edu/2284.htm", "#c", "#c td > p")
    assert summary["multiple_name_lines"] == 2 and summary["structure_ok"] is False


def _image(size, draw):
    image = Image.new("RGB", size, "white")
    draw(ImageDraw.Draw(image))
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_page_shaped_photo_is_not_a_scanned_document():
    from server.image_biography import page_appearance
    page = _image((800, 1130), lambda d: [d.rectangle([60, 80 + 30 * i, 700, 92 + 30 * i], fill="black")
                                          for i in range(25)])
    photo = _image((800, 1200), lambda d: (d.rectangle([0, 0, 800, 1200], fill=(70, 110, 170)),
                                           d.ellipse([220, 200, 580, 640], fill=(225, 170, 130)),
                                           d.rectangle([150, 700, 650, 1200], fill=(40, 40, 60))))
    assert page_appearance(page)["document_like"] is True
    assert page_appearance(photo)["document_like"] is False
    assert page_appearance(b"not an image")["document_like"] is True


def test_sleep_guard_holds_and_releases_only_on_windows(monkeypatch):
    from server.agent_worker import SleepGuard
    calls = []
    kernel = types.SimpleNamespace(SetThreadExecutionState=lambda flags: calls.append(flags) or 1)
    monkeypatch.setitem(sys.modules, "ctypes", types.SimpleNamespace(windll=types.SimpleNamespace(kernel32=kernel)))
    guard = SleepGuard("win32")
    guard.update(True)
    guard.update(True)
    guard.update(False)
    assert calls == [0x80000001, 0x80000000]
    linux = SleepGuard("linux")
    linux.update(True)
    assert linux.held is False and len(calls) == 2


class _Page:
    def __init__(self, context):
        self.context, self.url = context, ""

    async def goto(self, url, **_):
        self.url = url

        class Response:
            status = 200
        return Response()

    async def content(self):
        return self.context.pages[self.url]

    def locator(self, _selector):
        class Locator:
            async def count(self):
                return 0
        return Locator()

    async def close(self):
        pass


class _Context:
    def __init__(self, pages):
        self.pages = pages

    async def new_page(self):
        return _Page(self)


@pytest.mark.asyncio
async def test_run_plan_resolves_supplementary_identity_only_with_review(tmp_path):
    base = "https://sx.example.edu.cn"
    roster_url = base + "/szdw/js1.htm"
    people = ["徐丽", "董白英", "马万", "陈志恩", "刘媚", "丁芳", "田彦山"]
    roster = ("<html><body><table id='r'>" + "".join(
        f"<tr><td><a href='/info/1226/{i}.htm'>{n}</a></td></tr>" for i, n in enumerate(people))
        + "</table></body></html>")
    pages = {roster_url: roster, ORG_URL: ORG_PAGE.replace("职占江、", "")}
    for i, name in enumerate(people):
        pages[f"{base}/info/1226/{i}.htm"] = (f"<html><body><article><h1>{name}</h1><p>{name}，教授，"
                                              f"研究方向：数学分析。</p></article></body></html>")
    plan = {"school": "样本大学", "college": "数学学院", "branches": [
        {"url": roster_url, "card_selector": "#r tr", "single_page": True},
        {"url": ORG_URL, "card_selector": ORG_SELECTOR, "single_page": True}]}
    from server import identity_review
    first = await run_plan(plan, _Context(pages), tmp_path / "attempt_1")
    items = identity_review.collect_items(Path(first["audit_path"]))
    assert len(items) == 7 and first["total"] == 15
    for item in items:
        primary = next(o["url"] for o in item["observations"] if o["url"] != ORG_URL)
        identity_review.save_decision(tmp_path / "attempt_2" / identity_review.DECISIONS_FILE,
                                      item, "same", primary, reviewed_by=1)
    result = await run_plan(plan, _Context(pages), tmp_path / "attempt_2")
    assert not [u for u in result["unresolved"] if u["status"] == "distinct_profile_identity_unverified"]
    records = {row["姓名"]: row for row in json.loads(Path(result["records_path"]).read_text(encoding="utf-8"))}
    assert set(records) == set(people) | {"张永勇"}
    assert records["张永勇"]["主页"] == ORG_URL
    assert records["徐丽"]["主页"].startswith(base + "/info/1226/")
    assert len(result["identity_reviews"]) == 7 and result["identity_merges"] == []


def test_rare_name_on_a_list_needs_no_review_when_the_teacher_has_a_page():
    roster_url = "https://sx.example.edu.cn/szdw/js1.htm"
    roster = ("<table id='r'>" + "".join(
        f"<tr><td><a href='/info/1226/{i}.htm'>{n}</a></td></tr>"
        for i, n in enumerate(["徐丽", "董白英", "职占江", "马万"])) + "</table>")
    plan = {"school": "样本大学", "college": "数学学院", "branches": [
        {"url": roster_url, "card_selector": "#r tr", "single_page": True},
        {"url": ORG_URL, "card_selector": ORG_SELECTOR, "single_page": True}]}
    pages = [CapturedRosterPage(roster_url, roster, plan["branches"][0]),
             CapturedRosterPage(ORG_URL, ORG_PAGE, plan["branches"][1])]
    # 职占江 has a page on the main roster (admitted there by linked-card evidence
    # or a person's earlier decision); the list mention is the same teacher.
    from server import name_review
    listed = FacultyDirectoryExtractor.extract_with_review(roster, roster_url)
    decisions = {}
    for person in listed[1]:
        item = name_review.pending_item(roster_url, person)
        decisions[item["id"]] = {"id": item["id"], "verdict": "person", "verified_name": person.name}
    result = reconcile_rosters(pages, plan, name_decisions=decisions)
    assert not [u for u in result.unresolved if u["status"] == "rare_surname_needs_review"]


def test_photo_with_scene_text_is_not_an_unowned_biography():
    import asyncio
    from server import image_biography as media
    photo = _image((1200, 900), lambda d: (d.rectangle([0, 0, 1200, 900], fill=(60, 140, 60)),
                                           d.ellipse([500, 200, 700, 450], fill=(225, 170, 130))))
    lines = [{"text": text, "confidence": 0.95, "box": [[0, i], [9, i], [9, i + 9], [0, i + 9]]}
             for i, text in enumerate(["树神", "在海南的黎族，多视大板根树为神", "Hamadryed"])]
    scanned = {"kind": "image", "page_count": 1, "pages_with_text_layer": 0, "pages_ocrd": 1,
               "text": "\n".join(line["text"] for line in lines),
               "pages": [{"page": 1, "text": "\n".join(line["text"] for line in lines), "lines": lines,
                          "source": "local_ocr"}], "image_size": [1200, 900], "image_format": "png"}

    async def fetch(_url):
        return photo

    import tempfile
    with tempfile.TemporaryDirectory() as out:
        original = media.scan_media
        media.scan_media = lambda *_args, **_kwargs: dict(scanned)
        try:
            audit = asyncio.run(media.audit_profile_media(
                {"https://c.edu/p.htm": "<article><p>张伟，教授。</p><img src='/a.png'></article>"},
                {"https://c.edu/p.htm": {"张伟"}}, Path(out), fetch_asset=fetch, ocr_engine=object()))
        finally:
            media.scan_media = original
    assert audit["unresolved"] == []
    assert audit["evidence"][0]["classified_as"] == "photo_with_scene_text"


@pytest.mark.asyncio
async def test_profile_proof_is_retried_when_the_page_had_not_settled(monkeypatch):
    import hashlib
    from server import harvest_workflow, profile_structure
    html = "<html><body><div class='v'>张伟，教授</div></body></html>"
    proofs = [{"confirmed": False, "reason": "network_not_idle"},
              {"confirmed": True, "html_sha256": hashlib.sha256(html.encode()).hexdigest()}]

    async def verify(page, selector, start, *, local_capture=None, recurring_failures=None,
                     recurring_missing_media=None, allow_empty=False):
        assert allow_empty is True
        local_capture["html"] = html
        return dict(proofs.pop(0))
    monkeypatch.setattr(profile_structure, "verify_profile_container", verify)

    class Page:
        url = "https://c.edu/p.htm"

        async def goto(self, url, **_):
            return types.SimpleNamespace(status=200)

        async def wait_for_load_state(self, *_args, **_kwargs):
            pass

        async def content(self):
            return html

        async def close(self):
            pass

    class Context:
        async def new_page(self):
            return Page()

    evidence = {}
    pages, unresolved = await harvest_workflow.capture_profiles(
        Context(), ["https://c.edu/p.htm"], 1,
        profile_content_selectors={"https://c.edu/p.htm": {(".v", None)}},
        profile_content_evidence=evidence)
    assert unresolved == [] and proofs == [] and evidence["https://c.edu/p.htm"]["confirmed"] is True


def test_word_hidden_form_markers_do_not_block_the_profile():
    from server.profile_content import scope_profile_html
    html = ("<div class='v'><p>郑骥，教授，研究方向：古典文献学。</p>"
            "<p><span style=\"font-family: 宋体;display: none;mso-hide: all\">窗体顶端</span></p>"
            "<p><span style='display:none'>课程组成员名单</span></p></div>")
    _, _, issue = scope_profile_html(html, ".v", "https://c.edu/p.htm")
    assert issue["status"] == "profile_content_selector_hidden"      # real hidden Chinese text still blocks
    html = html.replace("<p><span style='display:none'>课程组成员名单</span></p>", "")
    scoped, evidence, issue = scope_profile_html(html, ".v", "https://c.edu/p.htm")
    assert issue is None and "窗体顶端" not in scoped and "古典文献学" in scoped


def test_review_only_issues_do_not_start_a_reconnaissance_repair():
    from server.agent_worker import NOT_STRUCTURAL
    from server.name_review import STATUS
    assert STATUS in NOT_STRUCTURAL
    assert {"image_bio_low_confidence", "image_bio_ocr_incomplete", "image_bio_owner_unresolved"} <= NOT_STRUCTURAL
    assert "roster_card_mismatch" not in NOT_STRUCTURAL and "profile_content_selector_hidden" not in NOT_STRUCTURAL

# This component fixture stipulates verified source purpose; purpose integration is unmocked elsewhere.
pytestmark = pytest.mark.verified_source_boundary
