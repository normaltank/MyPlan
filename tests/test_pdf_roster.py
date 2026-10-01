"""多人 PDF 名录: one browser-observed PDF cut into per-person records deterministically.

Every PDF here is generated in the test (synthetic people, example.edu.cn
addresses); no real teacher data is stored in the repository.
"""

from io import BytesIO
import json

from PIL import Image
import pymupdf
import pytest

from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
from server.pdf_roster import read_pdf_rosters
from server.recovery_policy import classify_issues

PAGE = "https://college.example.edu.cn/info/1132/1.htm"
PDF = "https://college.example.edu.cn/files/mentors.pdf"
HEAD, BODY = 16, 11


def _pdf(pages):
    """pages: a list of line lists [(text, size)], or "scan" for a raster-only page."""
    document = pymupdf.open()
    for lines in pages:
        page = document.new_page(width=595, height=842)
        if lines == "scan":
            buffer = BytesIO()
            Image.new("RGB", (595, 842), "white").save(buffer, format="PNG")
            page.insert_image(page.rect, stream=buffer.getvalue())
            continue
        y = 40
        for text, size, *at in lines:
            if at:
                page.insert_text((60, at[0]), text, fontname="china-s", fontsize=size)
                continue
            y += size + 8
            page.insert_text((60, y), text, fontname="china-s", fontsize=size)
    return document.tobytes()


class _Engine:
    """Fake RapidOCR: one prepared line list per rendered scanned page, in order."""

    def __init__(self, *pages):
        self.pages = list(pages)

    def __call__(self, _image):
        lines = self.pages.pop(0)
        return [[[[40, 60 + 40 * i], [600, 60 + 40 * i], [600, 60 + 40 * i + height], [40, 60 + 40 * i + height]],
                 text, confidence] for i, (text, confidence, height) in enumerate(lines)], 0.1


async def _read(tmp_path, original, *, engine=None, media=PDF, source_kind="university_faculty_roster"):
    async def fetch(url):
        assert url == media
        return original
    return await read_pdf_rosters([{"url": PAGE, "media_urls": [media], "source_kind": source_kind}],
                                  fetch_asset=fetch, evidence_dir=tmp_path / "pdf", ocr_engine=engine or object(),
                                  review_decisions_path=tmp_path / "image_bio_reviews.json")


def _by_name(result):
    return {record.name: record for record in result["records"]}


TEXT_PAGES = [
    [("某某学院研究生导师简介", BODY, 30),
     ("研究生导师简介", HEAD + 4), ("学院办公室邮箱：office@example.edu.cn", BODY),
     ("张伟，男，教授，硕士生导师", HEAD),
     ("研究方向：机器人与智能制造。", BODY),
     ("主持国家自然科学基金项目两项。", BODY),
     ("电子邮箱：zhangwei@example.edu.cn", BODY),
     ("李娜，女，副教授", HEAD),
     ("主讲机械原理，发表论文十余篇。", BODY),
     ("- 1 -", BODY, 820)],
    [("某某学院研究生导师简介", BODY, 30),
     ("曾获省级教学成果奖。邮箱：lina@example.edu.cn", BODY),
     ("王强，男，讲师", HEAD),
     ("研究方向：材料成型。", BODY),
     ("- 2 -", BODY, 820)],
]


@pytest.mark.asyncio
async def test_text_layer_pdf_splits_people_across_pages_with_full_text(tmp_path):
    result = await _read(tmp_path, _pdf(TEXT_PAGES))

    people = _by_name(result)
    assert list(people) == ["张伟", "李娜", "王强"]
    assert result["unresolved"] == []
    # 跨页续接: 李娜's record continues on page 2; page numbers and the
    # running header repeated on both pages are layout, not biography.
    assert people["李娜"].pages == [1, 2]
    assert people["李娜"].segment.text == ("李娜，女，副教授\n主讲机械原理，发表论文十余篇。\n"
                                          "曾获省级教学成果奖。邮箱：lina@example.edu.cn")
    assert "主持国家自然科学基金项目两项。" in people["张伟"].segment.text
    assert all("- 1 -" not in record.segment.text and "某某学院" not in record.segment.text
               for record in result["records"])
    assert people["张伟"].email == "zhangwei@example.edu.cn"
    assert people["李娜"].email == "lina@example.edu.cn"
    # The preamble office address belongs to nobody.
    assert people["王强"].email == "" and people["王强"].title == "讲师"
    assert people["张伟"].name_source == "pdf_text_layer"

    observation = result["observations"][PAGE][0]
    assert observation["records_detected"] == observation["records_admitted"] == 3
    assert observation["unaccounted_records"] == []
    assert observation["excluded_lines"] == {"page_number": 2, "running_header_footer": 2}
    assert observation["pages_with_text_layer"] == 2 and observation["pages_ocrd"] == 0
    evidence = result["evidence"][0]
    assert evidence["read_methods"] == {"1": "pdf_text_layer", "2": "pdf_text_layer"}
    layout = json.loads(open(evidence["layout_path"], encoding="utf-8").read())
    assert {line["record"] for line in layout if line["text"].startswith("曾获")} == {2}
    assert all(line["bbox"] for line in layout)
    assert open(evidence["original_path"], "rb").read().startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_scanned_and_text_pages_in_one_pdf_use_text_layer_then_local_ocr(tmp_path):
    original = _pdf([TEXT_PAGES[0], "scan"])
    engine = _Engine([("刘洋，男，教授", 0.97, 30), ("研究方向：图像处理。", 0.95, 20),
                      ("邮箱：liuyang@example.edu.cn", 0.96, 20),
                      ("陈静，女，副教授", 0.97, 30), ("研究方向：信号处理。", 0.95, 20)])

    result = await _read(tmp_path, original, engine=engine)

    people = _by_name(result)
    assert list(people) == ["张伟", "李娜", "刘洋"]
    assert people["刘洋"].name_source == "pdf_local_ocr"
    assert people["刘洋"].name_basis == "confident_ocr+full_name_pinyin_email"
    assert people["刘洋"].segment.text == "刘洋，男，教授\n研究方向：图像处理。\n邮箱：liuyang@example.edu.cn"
    # Confident OCR, but nothing in the record cross-checks the name: a person
    # confirms the exact line on the existing review page.
    assert [(item["status"], item["segment"], item["page"], item["line_index"])
            for item in result["unresolved"]] == [("segment_name_needs_review", 4, 2, 3)]
    assert result["name_evidence"][0]["kind"] == "pdf"
    assert result["observations"][PAGE][0]["unaccounted_records"] == [4]
    assert result["evidence"][0]["read_methods"] == {"1": "pdf_text_layer", "2": "local_ocr"}


@pytest.mark.asyncio
async def test_reviewed_ocr_line_admits_the_record_on_the_next_run(tmp_path):
    from server.image_bio_review import collect_review_items, save_review_decision
    original = _pdf(["scan"])
    lines = [("赵敏，女，副教授", 0.93, 30), ("研究方向：数据挖掘。", 0.62, 20),
             ("钱进，男，讲师", 0.95, 30), ("研究方向：嵌入式系统。", 0.96, 20)]

    first = await _read(tmp_path, original, engine=_Engine(lines))
    assert {item["status"] for item in first["unresolved"]} == {"image_bio_low_confidence",
                                                                "segment_name_needs_review"}
    assert first["records"] == []

    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"unresolved": first["unresolved"],
                                 "image_biography_evidence": first["evidence"],
                                 "segment_name_evidence": first["name_evidence"]}), encoding="utf-8")
    items = collect_review_items(audit)
    assert {(item["page"], item["line_index"]) for item in items} == {(1, 1), (1, 0), (1, 2)}
    assert all(item["crop_bytes"].startswith(b"\x89PNG") for item in items)
    for item in items:
        save_review_decision(tmp_path / "image_bio_reviews.json", item, item["ocr_text"])

    second = await _read(tmp_path, original, engine=_Engine(lines))
    assert second["unresolved"] == []
    assert [record.name_basis for record in second["records"]] == ["human_reviewed_ocr_line"] * 2


@pytest.mark.asyncio
async def test_ambiguous_segments_stay_unresolved_without_losing_other_people(tmp_path):
    original = _pdf([[
        ("张伟，男，教授", HEAD), ("研究方向：机器人。", BODY),
        # An emphasised heading the name reader cannot place.
        ("Smith John", HEAD), ("Visiting professor of robotics.", BODY),
        ("李娜，女，副教授", HEAD), ("邮箱：office@example.edu.cn", BODY),
        ("王强，男，讲师", HEAD), ("邮箱：office@example.edu.cn", BODY),
        ("李娜，女，教授", HEAD), ("另一位同名教师。", BODY),
        ("周涛，男，教授", HEAD), ("邮箱：zhoutao@example.edu.cn；tz@example.edu.cn", BODY),
    ]])

    result = await _read(tmp_path, original)

    statuses = sorted((item["status"], item.get("segment")) for item in result["unresolved"])
    assert statuses == [("pdf_roster_heading_unrecognized", 1), ("pdf_roster_shared_email", 2),
                        ("pdf_roster_shared_email", 3), ("segment_duplicate_name", None)]
    # 张伟 holds another heading, both 李娜 records are held: nobody is merged or
    # silently dropped, and the rest are reconciled.
    assert sorted(_by_name(result)) == ["周涛", "王强"]
    assert _by_name(result)["周涛"].email == "zhoutao@example.edu.cn"
    assert _by_name(result)["王强"].email == ""
    assert result["observations"][PAGE][0]["unaccounted_records"] == [1, 2, 4]
    recovery = classify_issues(result["unresolved"])
    assert recovery.structure == [] and len(recovery.review) == 4


@pytest.mark.asyncio
async def test_error_page_served_at_the_pdf_address_is_not_read_as_a_roster(tmp_path):
    result = await _read(tmp_path, b"<html><body>login required</body></html>")
    assert result["records"] == [] and result["read_pages"] == set()
    assert [item["status"] for item in result["unresolved"]] == ["pdf_roster_not_pdf"]


@pytest.mark.asyncio
async def test_same_original_seen_twice_is_counted_once(tmp_path):
    original = _pdf(TEXT_PAGES)

    async def fetch(_url):
        return original
    result = await read_pdf_rosters(
        [{"url": PAGE, "media_urls": [PDF, PDF + "?download=1"]}], fetch_asset=fetch,
        evidence_dir=tmp_path / "pdf", ocr_engine=object())
    assert len(result["records"]) == 3
    assert result["observations"][PAGE][1]["duplicate_of"] == PDF


def test_download_link_card_without_people_is_a_pdf_roster_source():
    html = '<div id="content"><p><a href="/files/mentors.pdf">导师名单下载</a></p></div>'
    result = reconcile_rosters([CapturedRosterPage(PAGE, html, {"card_selector": "#content"})], {})
    assert result.candidates == []
    assert result.pdf_rosters == [{"url": PAGE, "selector": "#content", "media_urls": [PDF],
                                   "source_kind": "university_faculty_roster"}]
