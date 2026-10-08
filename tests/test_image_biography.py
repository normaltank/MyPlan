"""Image biographies retain original media and expose local OCR evidence."""

from io import BytesIO
import json
from pathlib import Path

import fitz
import pytest
from PIL import Image

from server.image_biography import audit_profile_media, fetch_browser_asset, media_references, scan_media


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (400, 600), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_media_references_preserve_order_and_pdf_download_link():
    html = ('<article><img src="/cv-1.jpg"><a href="/download?id=2">导师简介.pdf</a>'
            '<img src="/cv-2.jpg"></article>')
    refs = media_references(html, "https://faculty.example.edu/profile")
    assert [(item.kind, item.url) for item in refs] == [
        ("image", "https://faculty.example.edu/cv-1.jpg"),
        ("pdf", "https://faculty.example.edu/download?id=2"),
        ("image", "https://faculty.example.edu/cv-2.jpg"),
    ]


def test_media_references_include_picture_srcset_and_embedded_pdf():
    html = ('<article><picture><source srcset="/cv.webp 1x, /cv-large.webp 2x">'
            '<img src="/fallback.jpg"></picture>'
            '<object data="/bio.pdf" type="application/pdf"></object>'
            '<video><source src="/lecture.mp4" type="video/mp4"></video></article>')
    refs = media_references(html, "https://faculty.example.edu/p")
    assert [(item.kind, item.url) for item in refs] == [
        ("image", "https://faculty.example.edu/cv.webp"),
        ("pdf", "https://faculty.example.edu/bio.pdf"),
    ]


def test_scanned_pdf_is_ocrd_page_by_page_without_a_text_layer():
    pdf = fitz.open()
    for _ in range(2):
        page = pdf.new_page()
        page.insert_image(page.rect, stream=_png())
    calls = []

    def ocr(image_bytes):
        calls.append(image_bytes)
        return [([0, 0, 10, 10], "完整导师履历文字", 0.98)], None

    evidence = scan_media(pdf.tobytes(), "pdf", ocr_engine=ocr)
    assert evidence["page_count"] == 2
    assert evidence["pages_with_text_layer"] == 0
    assert evidence["pages_ocrd"] == 2
    assert len(calls) == 2
    assert evidence["text"].count("完整导师履历文字") == 2
    assert evidence["pages"][0]["lines"][0]["box"] == [0, 0, 10, 10]


def test_normal_text_pdf_uses_text_layer_without_ocr():
    pdf = fitz.open()
    pdf.new_page().insert_text((20, 50), "faculty biography")

    def ocr(_image_bytes):
        raise AssertionError("text PDF must not be rendered for OCR")

    evidence = scan_media(pdf.tobytes(), "pdf", ocr_engine=ocr)
    assert evidence["pages_with_text_layer"] == 1
    assert evidence["pages_ocrd"] == 0
    assert "faculty biography" in evidence["text"]


def test_full_page_scan_with_only_page_number_text_layer_still_uses_ocr():
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_image(page.rect, stream=_png())
    page.insert_text((20, 25), "1")
    calls = []

    def ocr(image_bytes):
        calls.append(image_bytes)
        return [([0, 0, 10, 10], "完整导师履历文字", 0.98)], None

    evidence = scan_media(pdf.tobytes(), "pdf", ocr_engine=ocr)
    assert evidence["pages_ocrd"] == 1
    assert evidence["pages"][0]["source"] == "local_ocr"
    assert "完整导师履历文字" in evidence["text"]
    assert len(calls) == 1


def test_html_error_page_cannot_be_treated_as_pdf_biography():
    with pytest.raises(ValueError, match="PDF signature"):
        scan_media(b"<!DOCTYPE html><title>download gate</title>", "pdf",
                   ocr_engine=lambda _: ([], None))


async def _asset_loader(_url):
    return _png()


@pytest.mark.asyncio
async def test_profile_image_bio_uses_original_asset_and_keeps_local_evidence(tmp_path):
    url = "https://faculty.example.edu/p/1"
    html = '<article><h1>张富有</h1><img src="/cv.png"></article>'
    lines = ["张富有 教授", "教育经历：完整的学习经历和任职信息。", "研究方向：多学科科研项目与学术成果。"]

    def ocr(_data):
        return [([0, 0, 10, 10], line, 0.99) for line in lines], None

    result = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path,
                                       fetch_asset=_asset_loader, ocr_engine=ocr)
    assert result["unresolved"] == []
    assert "研究方向" in result["biographies"][url]
    evidence = result["evidence"][0]
    assert Path(evidence["original_path"]).read_bytes() == _png()
    assert Path(evidence["original_path"]).suffix == ".png"
    assert Path(evidence["ocr_path"]).is_file()


@pytest.mark.asyncio
async def test_portrait_with_no_body_text_does_not_become_a_biography(tmp_path):
    url = "https://faculty.example.edu/p/1"
    html = '<article><h1>张富有</h1><img src="/portrait.png"></article>'
    result = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path,
                                       fetch_asset=_asset_loader,
                                       ocr_engine=lambda _: ([([0, 0, 10, 10], "张富有", 0.99)], None))
    assert result["biographies"] == {}
    assert result["unresolved"] == []


@pytest.mark.asyncio
async def test_full_html_biography_does_not_treat_linked_research_pdf_as_resume(tmp_path):
    url = "https://faculty.example.edu/p/1"
    paragraph = "完整学习经历、任职经历与科研成果均在本页正文呈现。" * 10
    html = (f'<article><h1>张富有</h1><p>{paragraph}</p><p>{paragraph}</p>'
            '<a href="/paper.pdf">论文原文.pdf</a></article>')

    async def unexpected_fetch(_url):
        raise AssertionError("linked paper is not a biography source")

    result = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path,
                                       fetch_asset=unexpected_fetch, ocr_engine=lambda _: ([], None))
    assert result["unresolved"] == []
    assert result["evidence"] == []


@pytest.mark.asyncio
async def test_unreadable_document_page_blocks_image_biography(tmp_path):
    url = "https://faculty.example.edu/p/1"
    pdf = fitz.open()
    pdf.new_page().insert_image(fitz.Rect(0, 0, 595, 842), stream=_png())
    result = await audit_profile_media(
        {url: '<article><h1>张富有</h1><a href="/bio.pdf">导师简介.pdf</a></article>'},
        {url: {"张富有"}}, tmp_path,
        fetch_asset=lambda _: _pdf_bytes(pdf), ocr_engine=lambda _: ([], None))
    assert result["biographies"] == {}
    assert any(item["status"] == "image_bio_ocr_incomplete" for item in result["unresolved"])


def _page_ocr(read, detected):
    """A RapidOCR stand-in: recognised lines, or detector boxes alone."""
    def engine(_data, use_det=True, use_cls=True, use_rec=True):
        if not use_rec:
            return [[[x, y], [x + w, y], [x + w, y + h], [x, y + h]] for x, y, w, h in detected], None
        return [([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], text, 0.99)
                for (x, y, w, h), text in read], None
    return engine


async def _page_audit(tmp_path, engine):
    url = "https://faculty.example.edu/p/1"
    buffer = BytesIO()
    Image.new("RGB", (800, 1130), "white").save(buffer, format="PNG")

    async def fetch(_url):
        return buffer.getvalue()
    return await audit_profile_media({url: '<article><h1>张富有</h1><img src="/a.png"></article>'},
                                     {url: {"张富有"}}, tmp_path, fetch_asset=fetch, ocr_engine=engine)


@pytest.mark.asyncio
async def test_page_shaped_portrait_on_a_plain_backdrop_is_not_an_unread_document(tmp_path):
    # The detector outlines a face, a jacket and a caption, none of them text lines left unread.
    caption = (300, 1050, 200, 40)
    engine = _page_ocr([(caption, "张富有")], [(320, 200, 160, 190), (250, 500, 300, 400), caption,
                                              (40, 40, 12, 14), (60, 70, 9, 11)])
    result = await _page_audit(tmp_path, engine)
    assert result["unresolved"] == [] and result["biographies"] == {}
    assert result["evidence"][0]["unread_text_lines"] == 0


@pytest.mark.asyncio
async def test_page_whose_text_lines_ocr_cannot_read_blocks_image_biography(tmp_path):
    engine = _page_ocr([], [(80, 80 + 36 * i, 640, 26) for i in range(5)])
    result = await _page_audit(tmp_path, engine)
    assert [item["status"] for item in result["unresolved"]] == ["image_bio_ocr_incomplete"]
    assert result["unresolved"][0]["unread_text_lines"] == 5
    # vertical writing is read the same way
    engine = _page_ocr([], [(700 - 40 * i, 80, 26, 640) for i in range(3)])
    result = await _page_audit(tmp_path / "vertical", engine)
    assert result["unresolved"][0]["status"] == "image_bio_ocr_incomplete"


@pytest.mark.asyncio
async def test_page_that_cannot_be_measured_stays_on_the_review_path(tmp_path):
    def engine(_data, use_det=True, use_cls=True, use_rec=True):
        if not use_rec:
            raise RuntimeError("detector failed")
        return [], None
    result = await _page_audit(tmp_path, engine)
    assert result["unresolved"][0]["status"] == "image_bio_ocr_incomplete"
    assert result["unresolved"][0]["unread_text_lines"] is None


@pytest.mark.asyncio
async def test_pdf_link_returning_captcha_html_is_a_blocking_asset_failure(tmp_path):
    url = "https://faculty.example.edu/p/1"

    async def blocked(_url):
        return b"<!DOCTYPE html><title>attachment download</title>"

    result = await audit_profile_media(
        {url: '<article><a href="/cv.pdf">导师简介.pdf</a></article>'},
        {url: {"张富有"}}, tmp_path, fetch_asset=blocked,
        ocr_engine=lambda _: ([], None))
    assert result["biographies"] == {}
    assert any(item["status"] == "image_bio_asset_failed"
               and "PDF signature" in item["error"] for item in result["unresolved"])


@pytest.mark.asyncio
async def test_browser_asset_fetch_retries_transient_socket_failures():
    attempts = []

    class Request:
        async def get(self, _url, **_kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionError("socket hang up")

            class Response:
                ok = True

                async def body(self):
                    return b"original image"

            return Response()

    class Context:
        request = Request()

    assert await fetch_browser_asset(Context(), "https://faculty.example.edu/cv.png") == b"original image"
    assert len(attempts) == 3


@pytest.mark.asyncio
async def test_unchanged_original_reuses_ocr_after_live_asset_revalidation(tmp_path):
    url = "https://faculty.example.edu/p"
    html = '<article><img src="/cv.png"></article>'
    scans = []
    fetches = []

    async def fetch(_url):
        fetches.append(1)
        return _png()

    def ocr(_bytes):
        scans.append(1)
        lines = ("张富有 教授", "完整学习经历与任职信息", "研究项目与学术成果详述")
        return [([0, 0, 10, 10], line, 0.99) for line in lines], None

    first = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path,
                                      fetch_asset=fetch, ocr_engine=ocr)
    second = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path,
                                       fetch_asset=fetch, ocr_engine=ocr)
    assert first["biographies"] == second["biographies"]
    assert len(fetches) == 2
    assert len(scans) == 1
    assert second["evidence"][0]["ocr_reused"] is True


@pytest.mark.asyncio
async def test_same_original_reuses_ocr_across_task_output_directories(tmp_path):
    url = "https://faculty.example.edu/p"
    html = '<article><img src="/cv.png"></article>'
    scans = []
    fetches = []

    async def fetch(_url):
        fetches.append(1)
        return _png()

    def ocr(_bytes):
        scans.append(1)
        lines = ("张富有 教授", "完整学习经历与任职信息", "研究项目与学术成果详述")
        return [([0, 0, 10, 10], line, 0.99) for line in lines], None

    first = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path / "task-a",
                                      fetch_asset=fetch, ocr_engine=ocr,
                                      cache_dir=tmp_path / "shared-cache")
    second = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path / "task-b",
                                       fetch_asset=fetch, ocr_engine=ocr,
                                       cache_dir=tmp_path / "shared-cache")
    assert first["biographies"] == second["biographies"]
    assert len(fetches) == 2
    assert len(scans) == 1
    assert second["evidence"][0]["ocr_reused"] is True
    assert Path(second["evidence"][0]["original_path"]).is_file()
    assert Path(second["evidence"][0]["ocr_path"]).is_file()

    changed = BytesIO()
    Image.new("RGB", (400, 600), "gray").save(changed, format="PNG")

    async def fetch_changed(_url):
        return changed.getvalue()

    third = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path / "task-c",
                                      fetch_asset=fetch_changed, ocr_engine=ocr,
                                      cache_dir=tmp_path / "shared-cache")
    assert len(scans) == 2
    assert third["evidence"][0]["ocr_reused"] is False


@pytest.mark.asyncio
async def test_matching_human_review_clears_only_the_low_confidence_line(tmp_path):
    from server.image_bio_review import collect_review_items, save_review_decision

    url = "https://faculty.example.edu/p"
    html = '<article><img src="/cv.png"></article>'
    lines = (("张富有 教授", .99), ("完整学习经历与任职信息", .98),
             ("研究项目与学术成果详述", .55))

    def ocr(_bytes):
        return [([[5, 5 + index * 40], [150, 5 + index * 40],
                  [150, 30 + index * 40], [5, 30 + index * 40]], value, confidence)
                for index, (value, confidence) in enumerate(lines)], None

    first = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path,
                                      fetch_asset=_asset_loader, ocr_engine=ocr)
    assert any(issue["status"] == "image_bio_low_confidence" for issue in first["unresolved"])
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(json.dumps({"image_biography_evidence": first["evidence"],
                                      "unresolved": first["unresolved"]}, ensure_ascii=False),
                          encoding="utf-8")
    item = collect_review_items(audit_path)[0]
    decisions = tmp_path / "image_bio_reviews.json"
    save_review_decision(decisions, item, "研究项目与学术成果完整详述")

    second = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path,
                                       fetch_asset=_asset_loader, ocr_engine=ocr,
                                       review_decisions_path=decisions)
    assert second["unresolved"] == []
    assert "研究项目与学术成果完整详述" in second["biographies"][url]
    assert second["evidence"][0]["human_reviewed_lines"] == 1
    original_ocr = json.loads(Path(first["evidence"][0]["ocr_path"]).read_text(encoding="utf-8"))
    assert original_ocr["pages"][0]["lines"][2]["text"] == "研究项目与学术成果详述"


@pytest.mark.asyncio
async def test_shared_document_is_not_assigned_to_one_of_several_owners(tmp_path):
    url = "https://faculty.example.edu/mentors"
    html = '<article><a href="/mentors.pdf">导师简介.pdf</a></article>'
    lines = ("张富有 教授", "完整学习经历与任职信息", "研究项目与学术成果详述")
    pdf = fitz.open()
    pdf.new_page().insert_image(fitz.Rect(0, 0, 595, 842), stream=_png())
    result = await audit_profile_media({url: html}, {url: {"张富有", "刘圣洁"}}, tmp_path,
                                       fetch_asset=lambda _: _pdf_bytes(pdf),
                                       ocr_engine=lambda _: ([([0, 0, 10, 10], line, 0.99)
                                                              for line in lines], None))
    assert result["biographies"] == {}
    assert any(item["status"] == "image_bio_owner_unresolved" for item in result["unresolved"])


@pytest.mark.asyncio
async def test_pdf_with_another_faculty_heading_on_later_page_is_not_assigned(tmp_path):
    url = "https://faculty.example.edu/p/1"
    pdf = fitz.open()
    for _ in range(2):
        pdf.new_page().insert_image(fitz.Rect(0, 0, 595, 842), stream=_png())
    names = iter(("张富有", "刘圣洁"))

    def ocr(_data):
        name = next(names)
        lines = (f"{name} 教授", "完整学习经历与任职信息", "研究项目与学术成果详述")
        return [([0, 0, 10, 10], line, 0.99) for line in lines], None

    result = await audit_profile_media(
        {url: '<article><a href="/cv.pdf">导师简介.pdf</a></article>'},
        {url: {"张富有"}}, tmp_path, fetch_asset=lambda _: _pdf_bytes(pdf),
        ocr_engine=ocr, known_names={"张富有", "刘圣洁"})
    assert result["biographies"] == {}
    assert any(item["status"] == "image_bio_owner_unresolved" for item in result["unresolved"])


@pytest.mark.asyncio
async def test_separate_roster_images_match_distinct_people_without_cross_assignment(tmp_path):
    url = "https://faculty.example.edu/mentors"
    html = '<article><img src="/a.png"><img src="/b.png"></article>'
    assets = {"https://faculty.example.edu/a.png": b"a",
              "https://faculty.example.edu/b.png": b"b"}

    async def fetch(asset_url):
        return assets[asset_url]

    def scan(data, _kind, *, ocr_engine=None):
        name = "张富有" if data == b"a" else "刘圣洁"
        lines = [f"{name} 教授", "完整学习经历与任职信息", "研究项目与学术成果详述"]
        text = "\n".join(lines)
        return {"page_count": 1, "pages_with_text_layer": 0, "pages_ocrd": 1,
                "text": text, "pages": [{"page": 1, "text": text,
                "lines": [{"text": line, "confidence": 0.99} for line in lines],
                "source": "local_ocr"}], "image_size": [1200, 1800]}

    from unittest.mock import patch
    with patch("server.image_biography.scan_media", scan):
        result = await audit_profile_media({url: html}, {url: {"张富有", "刘圣洁"}},
                                           tmp_path, fetch_asset=fetch)
    assert result["assigned_biographies"][(url, "张富有")].startswith("张富有")
    assert result["assigned_biographies"][(url, "刘圣洁")].startswith("刘圣洁")
    assert result["unresolved"] == []


@pytest.mark.asyncio
async def test_other_faculty_named_later_in_citation_does_not_override_image_heading(tmp_path):
    url = "https://faculty.example.edu/mentors"
    lines = ("张富有 教授", "完整学习经历与任职信息", "研究项目与学术成果详述", "合作成果涉及刘圣洁")
    result = await audit_profile_media(
        {url: '<article><img src="/a.png"></article>'}, {url: {"张富有", "刘圣洁"}},
        tmp_path, fetch_asset=_asset_loader,
        ocr_engine=lambda _: ([([0, 0, 10, 10], line, 0.99) for line in lines], None))
    assert (url, "张富有") in result["assigned_biographies"]
    assert (url, "刘圣洁") not in result["assigned_biographies"]
    assert result["unresolved"] == []


@pytest.mark.asyncio
async def test_ocr_engine_is_reused_across_images(tmp_path, monkeypatch):
    url = "https://faculty.example.edu/p"
    from unittest.mock import patch
    created = []
    passed = []

    class Engine:
        def __init__(self):
            created.append(self)

    def scan(_data, _kind, *, ocr_engine=None):
        passed.append(ocr_engine)
        return {"page_count": 1, "pages_ocrd": 1, "text": "", "pages": [
            {"page": 1, "text": "", "lines": [], "source": "local_ocr"}],
            "image_size": [400, 600]}

    with patch("server.image_biography.scan_media", scan):
        monkeypatch.setattr("rapidocr_onnxruntime.RapidOCR", Engine)
        await audit_profile_media({url: '<article><img src="/a.png"><img src="/b.png"></article>'},
                                  {url: {"张富有"}}, tmp_path,
                                  fetch_asset=lambda _: _asset_loader(_))
    assert len(created) == 1
    assert passed == [created[0], created[0]]


@pytest.mark.asyncio
async def test_verified_outer_container_keeps_media_after_nested_article(tmp_path):
    url = "https://faculty.example.edu/p"
    html = ('<html><body><div id="verified"><article><img src="/a.png"></article>'
            '<img src="/b.png"></div></body></html>')
    result = await audit_profile_media({url: html}, {url: {"张富有"}}, tmp_path,
                                       fetch_asset=_asset_loader,
                                       ocr_engine=lambda _: ([], None), scoped_urls={url})
    assert len(result["evidence"]) == 2


async def _pdf_bytes(pdf):
    return pdf.tobytes()
