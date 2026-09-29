"""分段平铺名录 (segmented article roster): one article, many people, no card per person."""

import asyncio
from io import BytesIO
import json
import re

from PIL import Image

from server.segmented_roster import (OcrName, admit_ocr_name, branch_valid, choose_email,
                                     full_name_email, name_image_segments, ocr_name,
                                     reconcile_page, split_segments, verification_summary)

URL = "https://www.example.edu.cn/jxgc/info/1232/15867.htm"

# Image-resume variant: names exist only inside the images; the email of the
# second person shares a <p> with the third person's image.
IMAGE_ARTICLE = """<html><body><div class="nav"><a href="/">首页</a></div>
<div class="v_news_content">
  <p>导师名录</p><p>学院办公室：jxgc@example.edu.cn</p>
  <p style="text-align:center"><img src="/__local/a1.jpg" width="600"></p>
  <p>电子邮箱：zhangwei@example.edu.cn</p>
  <p><img src="/__local/a2.jpg"></p>
  <p>电子邮箱：lina2019@163.com<img src="/__local/a3.jpg"></p>
  <p>电子邮箱：12345678@qq.com</p>
</div></body></html>"""

# Text variant: a department article with portrait + prose per teacher.
TEXT_ARTICLE = """<html><body><div class="v_news_content">
  <p><strong>机械设计系</strong></p>
  <p><img src="/p/1.jpg"></p><p>张伟，男，教授，硕士生导师。</p>
  <p>研究方向：机器人与智能制造。</p><p>邮箱：zhangwei@example.edu.cn</p>
  <p><img src="/p/2.jpg"></p><p>李娜，女，副教授。</p><p>主讲机械原理。</p>
  <p><img src="/p/3.jpg"></p><p>王芳，女，讲师。</p><p>邮箱：wf@example.edu.cn</p>
</div></body></html>"""


def test_positional_cut_keeps_text_with_the_record_it_follows():
    page = split_segments(IMAGE_ARTICLE, URL, "div.v_news_content", "div.v_news_content img")
    assert [len(s.images) for s in page.segments] == [1, 1, 1]
    assert [s.emails for s in page.segments] == [["zhangwei@example.edu.cn"],
                                                  ["lina2019@163.com"], ["12345678@qq.com"]]
    assert [s.start_image.rsplit("/", 1)[1] for s in page.segments] == ["a1.jpg", "a2.jpg", "a3.jpg"]
    assert page.preamble_emails == ["jxgc@example.edu.cn"]
    assert "jxgc@example.edu.cn" in page.shared_emails


def test_text_segments_become_records_with_positional_email():
    branch = {"kind": "segmented", "url": URL, "container_selector": "div.v_news_content",
              "record_start_selector": "div.v_news_content img", "single_page": True}
    assert branch_valid(branch)
    result = reconcile_page(TEXT_ARTICLE, URL, branch)
    assert result.unresolved == []
    assert [(r.name, r.title, r.email) for r in result.records] == [
        ("张伟", "教授", "zhangwei@example.edu.cn"), ("李娜", "副教授", ""), ("王芳", "讲师", "wf@example.edu.cn")]
    assert result.records[0].email_basis == "segment_position+full_name_pinyin"
    assert result.records[2].email_basis == "segment_position"
    assert "机械原理" in result.records[1].segment.text
    assert result.observation["visible_segments"] == 3 and result.observation["text_named"] == 3


def test_misplaced_start_selector_is_caught_not_merged():
    branch = {"kind": "segmented", "url": URL, "container_selector": "div.v_news_content",
              "record_start_selector": "div.v_news_content img[src$='1.jpg'], div.v_news_content img[src$='3.jpg']",
              "single_page": True}
    result = reconcile_page(TEXT_ARTICLE, URL, branch)
    assert [item["status"] for item in result.unresolved] == ["segment_multiple_names"]
    assert [r.name for r in result.records] == ["王芳"]


def test_image_only_records_wait_for_ocr_and_shared_email_is_not_assigned():
    branch = {"kind": "segmented", "url": URL, "container_selector": "div.v_news_content",
              "record_start_selector": "div.v_news_content img", "single_page": True}
    result = reconcile_page(IMAGE_ARTICLE, URL, branch)
    assert result.records == [] and len(result.pending_ocr) == 3
    assert result.unresolved == []
    assert result.observation["image_named_pending"] == 3


def test_container_and_start_errors_block():
    assert split_segments(IMAGE_ARTICLE, URL, "p", "img").issues[0]["status"] == "segment_container_not_unique"
    assert split_segments(IMAGE_ARTICLE, URL, "div.v_news_content", "video").issues[0]["status"] == "segment_start_empty"
    nested = split_segments(IMAGE_ARTICLE, URL, "div.v_news_content", "div.v_news_content p, div.v_news_content img")
    assert nested.issues[0]["status"] == "segment_start_nested"


def test_full_name_pinyin_is_required_for_email_cross_check():
    assert full_name_email("zhangwei@x.cn", "张伟")
    assert full_name_email("weizhang@x.cn", "张伟")
    assert full_name_email("zwei2020@x.cn", "张伟")
    assert full_name_email("zhangweilab@x.cn", "张伟")
    assert full_name_email("ouyangming@x.cn", "欧阳明")
    assert full_name_email("zengfan@x.cn", "曾凡")          # heteronym reading
    assert not full_name_email("zw@x.cn", "张伟")           # initials do not confirm the given name
    assert not full_name_email("zhang@x.cn", "张伟")        # surname only
    assert not full_name_email("12345678@qq.com", "张伟")
    assert not full_name_email("zhangqiang@x.cn", "张伟")


def test_choose_email_prefers_the_single_affine_address_and_flags_ambiguity():
    page = split_segments(TEXT_ARTICLE, URL, "div.v_news_content", "div.v_news_content img")
    segment = page.segments[0]
    segment.emails = ["office@x.cn", "zhangwei@x.cn"]
    assert choose_email(segment, "张伟", set())[0] == "zhangwei@x.cn"
    segment.emails = ["a@x.cn", "b@x.cn"]
    assert choose_email(segment, "张伟", set())[2]["status"] == "segment_email_ambiguous"
    assert choose_email(segment, "张伟", {"a@x.cn"})[0] == "b@x.cn"


def _scan(lines):
    return {"pages": [{"page": 1, "lines": [{"text": t, "confidence": c, "box": [[0, 0], [9, 0], [9, 9], [0, 9]]}
                                            for t, c in lines], "source": "local_ocr"}]}


def test_ocr_name_gate():
    confident = ocr_name(_scan([("个人简历", .99), ("姓名", .99), ("张伟", .97), ("性别 男", .99)]), set())
    assert (confident.name, confident.line_index) == ("张伟", 2)
    assert admit_ocr_name(confident, ["zhangwei@x.cn"]) == ("张伟", "confident_ocr+full_name_pinyin_email")
    assert admit_ocr_name(confident, ["zw@x.cn"]) == (None, "segment_name_needs_review")
    assert admit_ocr_name(confident, []) == (None, "segment_name_needs_review")
    shaky = ocr_name(_scan([("张伟 教授", .72)]), set())
    assert admit_ocr_name(shaky, ["zhangwei@x.cn"]) == (None, "segment_name_needs_review")
    reviewed = ocr_name(_scan([("张伟 教授", .72)]), {(1, 0)})
    assert admit_ocr_name(reviewed, []) == ("张伟", "human_reviewed_ocr_line")
    unreadable_name = ocr_name(_scan([("彐伟", .5), ("研究方向 机器人", .99)]), set())
    assert unreadable_name == OcrName(None, 1, 0, .5, False)
    assert admit_ocr_name(unreadable_name, [])[1] == "segment_name_needs_review"
    assert admit_ocr_name(ocr_name(_scan([("Curriculum Vitae", .99)]), set()), [])[1] == "segment_name_unreadable"


class _FakeOcr:
    def __init__(self, by_size):
        self.by_size = by_size
        self.calls = 0

    def __call__(self, data):
        self.calls += 1
        width = Image.open(BytesIO(data)).size[0]
        return ([[[[0, 0], [9, 0], [9, 9], [0, 9]], text, conf] for text, conf in self.by_size[width]], 0.1)


def _png(width):
    buffer = BytesIO()
    Image.new("RGB", (width, 40), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_name_image_segments_admits_cross_checked_names_and_routes_the_rest_to_review(tmp_path):
    branch = {"kind": "segmented", "url": URL, "container_selector": "div.v_news_content",
              "record_start_selector": "div.v_news_content img", "single_page": True}
    result = reconcile_page(IMAGE_ARTICLE, URL, branch)
    assets = {"a1.jpg": _png(101), "a2.jpg": _png(102), "a3.jpg": _png(103)}
    engine = _FakeOcr({101: [("张伟", .98), ("教授", .99)],
                       102: [("李娜", .96), ("副教授", .99)],       # lina2019 spells the name
                       103: [("王芳", .97)]})                       # numeric mailbox: no cross-check

    async def fetch(url):
        return assets[url.rsplit("/", 1)[1]]

    outcome = asyncio.run(name_image_segments(
        result.pending_ocr, fetch_asset=fetch, evidence_dir=tmp_path / "names",
        cache_dir=tmp_path / "cache", review_decisions_path=tmp_path / "image_bio_reviews.json",
        shared_emails={URL: result.shared_emails}, ocr_engine=engine))
    assert [(r.name, r.email, r.name_basis) for r in outcome["admitted"]] == [
        ("张伟", "zhangwei@example.edu.cn", "confident_ocr+full_name_pinyin_email"),
        ("李娜", "lina2019@163.com", "confident_ocr+full_name_pinyin_email")]
    assert [(item["status"], item["segment"], item["line_index"]) for item in outcome["unresolved"]] == [
        ("segment_name_needs_review", 3, 0)]
    assert {item["purpose"] for item in outcome["evidence"]} == {"segment_name"}

    # A reviewer confirms the exact OCR line; the next run admits it without OCR again.
    from server.image_bio_review import _item_id, _text_digest
    third = next(item for item in outcome["evidence"] if item["segment"] == 3)
    decision = {"id": _item_id(third["media_url"], third["sha256"], 1, 0, "王芳"),
                "media_url": third["media_url"], "source_sha256": third["sha256"], "page": 1,
                "line_index": 0, "ocr_text_sha256": _text_digest("王芳"), "verified_text": "王芳"}
    (tmp_path / "image_bio_reviews.json").write_text(json.dumps({"schema": 1, "decisions": [decision]}),
                                                     encoding="utf-8")
    calls = engine.calls
    rerun = reconcile_page(IMAGE_ARTICLE, URL, branch)
    again = asyncio.run(name_image_segments(
        rerun.pending_ocr, fetch_asset=fetch, evidence_dir=tmp_path / "names2",
        cache_dir=tmp_path / "cache", review_decisions_path=tmp_path / "image_bio_reviews.json",
        shared_emails={URL: rerun.shared_emails}, ocr_engine=engine))
    assert engine.calls == calls                     # shared OCR cache reused
    assert [(r.name, r.name_basis) for r in again["admitted"]][-1] == ("王芳", "human_reviewed_ocr_line")
    assert again["unresolved"] == []


def test_verification_summary_carries_structure_only():
    summary = verification_summary(IMAGE_ARTICLE, URL, "div.v_news_content", "div.v_news_content img")
    assert summary["segment_count"] == 3 and summary["image_only"] == 3 and summary["structure_ok"]
    assert summary["preamble_emails"] == 1
    text = json.dumps(summary, ensure_ascii=False)
    assert "@" not in text and not re.search(r"[一-鿿]", text)
    wrong = verification_summary(TEXT_ARTICLE, URL, "div.v_news_content",
                                 "div.v_news_content img[src$='1.jpg']")
    assert wrong["multiple_name_lines"] == 1 and not wrong["structure_ok"]


def test_prose_lines_with_surname_like_words_are_not_new_people():
    article = """<div class="c"><p><img src="/p/1.jpg"></p><p>高明，男，教授</p>
      <p>主讲课程：</p><p>高等数学、线性代数</p><p>黄河流域</p>
      <p><img src="/p/2.jpg"></p><p>周琳，女，讲师</p><p>周期信号处理</p></div>"""
    branch = {"kind": "segmented", "url": URL, "container_selector": "div.c",
              "record_start_selector": "div.c img", "single_page": True}
    result = reconcile_page(article, URL, branch)
    assert result.unresolved == []
    assert [r.name for r in result.records] == ["高明", "周琳"]
    merged = reconcile_page(article, URL, {**branch, "record_start_selector": "div.c img[src$='1.jpg']"})
    assert [item["status"] for item in merged.unresolved] == ["segment_multiple_names"]
