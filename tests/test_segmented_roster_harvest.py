"""End to end: a segmented article roster through plan validation, harvest, review and export."""

from io import BytesIO
import json
from pagination_contract_fixture import completed_result
from pathlib import Path

from PIL import Image
import pytest

import server.image_biography as media
from server.agent_recon import PlanHygieneError, selector_evidence_issues, validate_model_plan
from server.browser_batch_tool import _validate_plan, run_plan
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters

URL = "https://www.example.edu.cn/jxgc/info/1232/15867.htm"
BRANCH = {"url": URL, "kind": "segmented", "container_selector": "div.v_news_content",
          "record_start_selector": "div.v_news_content img", "single_page": True}

TEXT_ARTICLE = """<html><body><div class="v_news_content">
  <p><strong>机械设计系</strong></p>
  <p><img src="/p/1.jpg"></p><p>张伟，男，教授，硕士生导师。</p>
  <p>研究方向：机器人与智能制造。</p><p>邮箱：zhangwei@example.edu.cn</p>
  <p><img src="/p/2.jpg"></p><p>李娜，女，副教授。</p><p>主讲机械原理。</p>
</div></body></html>"""

IMAGE_ARTICLE = """<html><body><div class="v_news_content">
  <p>导师名录</p>
  <p><img src="/__local/a1.png"></p><p>电子邮箱：zhangwei@example.edu.cn</p>
  <p><img src="/__local/a2.png"></p><p>电子邮箱：12345678@qq.com</p>
</div></body></html>"""


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
    def __init__(self, pages, assets):
        self.pages, self.assets = pages, assets
        context = self

        class Request:
            async def get(self, url, **_):
                if url not in context.assets:
                    raise RuntimeError("asset unavailable")

                class Response:
                    ok = True

                    async def body(self):
                        return context.assets[url]
                return Response()
        self.request = Request()

    async def new_page(self):
        return _Page(self)


@pytest.fixture(autouse=True)
def _no_real_ocr_engine(monkeypatch):
    """scan_media is faked below; never load the real OCR model."""
    import sys
    import types
    fake = types.ModuleType("rapidocr_onnxruntime")
    fake.RapidOCR = lambda: object()
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake)


def _png(width, height=60):
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _fake_scan(texts_by_width):
    def scan(data, kind, *, ocr_engine=None):
        width, height = Image.open(BytesIO(data)).size
        lines = [{"text": text, "confidence": confidence,
                  "box": [[2, 2 + 12 * i], [50, 2 + 12 * i], [50, 12 + 12 * i], [2, 12 + 12 * i]]}
                 for i, (text, confidence) in enumerate(texts_by_width.get(width, []))]
        text = "\n".join(line["text"] for line in lines)
        return {"kind": kind, "page_count": 1, "pages_with_text_layer": 0, "pages_ocrd": 1, "text": text,
                "pages": [{"page": 1, "text": text, "lines": lines, "source": "local_ocr"}],
                "image_size": [width, height], "image_format": "png"}
    return scan


def test_plan_validators_accept_only_the_segmented_contract():
    plan = {"school": "样本大学", "college": "机械工程学院", "source_kind": "university_faculty_roster",
            "branches": [dict(BRANCH)]}
    _validate_plan(plan)
    validate_model_plan(plan, school="样本大学", college="机械工程学院")
    for broken in ({**BRANCH, "card_selector": "div img"},
                   {k: v for k, v in BRANCH.items() if k != "record_start_selector"},
                   {**BRANCH, "single_page": False, "next_selector": "a.next"}):
        with pytest.raises((ValueError, PlanHygieneError)):
            validate_model_plan({**plan, "branches": [broken]}, school="样本大学", college="机械工程学院")
    with pytest.raises(ValueError):
        _validate_plan({**plan, "branches": [{**BRANCH, "card_selector": "div img"}]})


def _events(result):
    result = completed_result(result, {"container_selector": BRANCH["container_selector"]})
    call = {"type": "tool/call", "data": {"name": "mcp__playwright__browser_verify_segments", "callId": "c1",
            "arguments": json.dumps({"container_selector": BRANCH["container_selector"],
                                     "record_start_selector": BRANCH["record_start_selector"]})}}
    reply = {"type": "tool/result", "data": {"message": {"content": [
        {"toolCallId": "c1", "content": [{"type": "text", "text": json.dumps(result)}]}]}}}
    return [call, reply]


def test_recon_gate_requires_a_structure_ok_segment_verification():
    from server.segmented_roster import verification_summary
    plan = {"school": "样本大学", "college": "机械工程学院", "branches": [dict(BRANCH)]}
    good = {**verification_summary(IMAGE_ARTICLE, URL, BRANCH["container_selector"],
                                   BRANCH["record_start_selector"]), "pagination": {"total": 0}}
    assert selector_evidence_issues(plan, _events(good)) == []
    assert selector_evidence_issues(plan, _events({**good, "structure_ok": False}))[0]["status"] == "segments_not_verified"
    assert selector_evidence_issues(plan, _events({**good, "pagination": {"total": 2}}))[0]["status"] == "segments_not_verified"
    assert selector_evidence_issues(plan, [])[0]["status"] == "segments_not_verified"


def test_reconcile_rosters_turns_text_segments_into_profileless_candidates():
    page = CapturedRosterPage(URL, TEXT_ARTICLE, dict(BRANCH))
    result = reconcile_rosters([page], {"school": "样本大学", "college": "机械工程学院", "branches": [BRANCH]})
    assert [(c.name, c.url, c.title) for c in result.candidates] == [("张伟", URL, "教授"), ("李娜", URL, "副教授")]
    assert result.unresolved == [] and result.observations[0]["visible_segments"] == 2


@pytest.mark.asyncio
async def test_text_segmented_article_is_delivered_with_article_text_and_positional_email(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "scan_media", _fake_scan({}))       # portraits carry no text
    base = "https://www.example.edu.cn"
    browser = _Context({URL: TEXT_ARTICLE}, {base + "/p/1.jpg": _png(120), base + "/p/2.jpg": _png(121)})
    plan = {"school": "样本大学", "college": "机械工程学院", "branches": [dict(BRANCH)]}
    result = await run_plan(plan, browser, tmp_path)
    assert result["unresolved"] == [] and result["delivery_ready"] is True
    records = {row["姓名"]: row for row in json.loads(Path(result["records_path"]).read_text(encoding="utf-8"))}
    assert records["张伟"]["邮箱"] == "zhangwei@example.edu.cn" and records["张伟"]["职称"] == "教授"
    assert "研究方向：机器人与智能制造。" in records["张伟"]["简介"] and "李娜" not in records["张伟"]["简介"]
    assert records["李娜"]["邮箱"] == "" and "主讲机械原理。" in records["李娜"]["简介"]
    assert all(row["主页"] == '' for row in records.values())
    assert all(source['profile_url'] == URL for source in result['coverage']['field_sources'])
    sources = result["coverage"]["field_sources"]
    assert {s["attribution_scope"] for s in sources} == {"segmented_article_record"}


@pytest.mark.asyncio
async def test_image_only_names_are_cross_checked_or_reviewed_before_delivery(tmp_path, monkeypatch):
    from server.image_bio_review import collect_review_items, save_review_decision
    from server.review_registry import has_review

    resume = [("学习经历：某大学机械工程博士", .99), ("研究方向：智能制造与机器人", .99),
              ("主持国家自然科学基金项目", .99)]
    monkeypatch.setattr(media, "scan_media", _fake_scan({
        300: [("张伟", .98), *resume], 301: [("王芳", .97), *resume]}))
    base = "https://www.example.edu.cn/__local/"
    browser = _Context({URL: IMAGE_ARTICLE}, {base + "a1.png": _png(300), base + "a2.png": _png(301)})
    plan = {"school": "样本大学", "college": "机械工程学院", "branches": [dict(BRANCH)]}

    first = await run_plan(plan, browser, tmp_path)
    assert first["delivery_ready"] is False
    assert [item["status"] for item in first["unresolved"]] == ["segment_name_needs_review"]
    audit_path = Path(first["audit_path"])
    assert has_review(audit_path)
    items = collect_review_items(audit_path)
    assert [(item["purpose"], item["ocr_text"]) for item in items] == [("segment_name", "王芳")]
    save_review_decision(tmp_path / "image_bio_reviews.json", items[0], "王芳")

    second = await run_plan(plan, browser, tmp_path)
    assert second["unresolved"] == [] and second["delivery_ready"] is True
    records = {row["姓名"]: row for row in json.loads(Path(second["records_path"]).read_text(encoding="utf-8"))}
    assert records["张伟"]["邮箱"] == "zhangwei@example.edu.cn"
    assert records["王芳"]["邮箱"] == "12345678@qq.com"
    assert records["王芳"]["简介"].startswith("王芳\n学习经历")          # resume text first, as on the page
    assert records["王芳"]["简介"].endswith("电子邮箱：12345678@qq.com")
    bases = {s["segment_record"]["name_basis"] for s in second["coverage"]["field_sources"]}
    assert bases == {"confident_ocr+full_name_pinyin_email", "human_reviewed_ocr_line"}


@pytest.mark.asyncio
async def test_ocr_name_repeating_a_text_name_blocks_instead_of_merging(tmp_path, monkeypatch):
    article = """<html><body><div class="v_news_content">
      <p><img src="/p/1.png"></p><p>张伟，男，教授</p><p>邮箱：zhangwei@example.edu.cn</p>
      <p><img src="/p/2.png"></p><p>电子邮箱：zhangwei2@example.edu.cn</p>
    </div></body></html>"""
    resume = [("张伟", .98), ("学习经历：某大学机械工程博士", .99), ("研究方向：智能制造与机器人", .99)]
    monkeypatch.setattr(media, "scan_media", _fake_scan({400: [], 401: resume}))
    base = "https://www.example.edu.cn/p/"
    browser = _Context({URL: article}, {base + "1.png": _png(400), base + "2.png": _png(401)})
    result = await run_plan({"school": "样本大学", "college": "机械工程学院", "branches": [dict(BRANCH)]},
                            browser, tmp_path)
    assert result["delivery_ready"] is False
    assert "segment_duplicate_name" in {item["status"] for item in result["unresolved"]}

# Source verification is a separately protected integration boundary.
pytestmark = pytest.mark.verified_source_boundary
