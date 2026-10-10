"""PDF authors and page breaks do not define biography ownership boundaries."""

import pymupdf
import pytest

from server.image_biography import audit_profile_media, scan_media

OWNER = "张富有"
PEER = "刘圣洁"
URL = "https://faculty.example.edu/p/1"


@pytest.mark.asyncio
async def test_owner_mentioned_only_as_an_author_does_not_prove_owner_section(tmp_path):
    data = document(
        [[(f"{OWNER}, {PEER}, 一项研究成果。", 12), ("科研论文正文。", 12)]]
    )
    result = await audit(data, tmp_path)
    assert not result["biographies"]
    assert any(
        item["status"] == "image_bio_owner_unresolved" for item in result["unresolved"]
    )


def document(pages):
    with pymupdf.open() as pdf:
        for lines in pages:
            page = pdf.new_page()
            for index, (text, size) in enumerate(lines):
                page.insert_text(
                    (60, 70 + index * 25), text, fontsize=size, fontname="china-s"
                )
        return pdf.tobytes()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [PEER, "哔甲"])
async def test_explicit_profile_introduction_identifies_another_person_without_font_emphasis(
    tmp_path, name
):
    data = document(
        [
            [(OWNER + " 教授", 16), ("完整个人履历。", 12)],
            [(f"【个人简介】{name} 教授", 12), ("另一个人的履历。", 12)],
        ]
    )
    result = await audit(data, tmp_path, names={OWNER})
    assert not result["biographies"]
    assert any(
        item["status"] == "image_bio_owner_unresolved" for item in result["unresolved"]
    )


def test_previously_verified_rare_owner_keeps_its_own_intro_field():
    from server.pdf_biography_owner import inspect_owner_scope

    data = document([[("【个人简介】哔甲 教授", 12), ("完整个人履历。", 12)]])
    scanned = scan_media(data, "pdf")
    assert (
        inspect_owner_scope(data, scanned, "哔甲", {"哔甲"}, set())["status"]
        == "single_owner"
    )


@pytest.mark.asyncio
async def test_centered_person_heading_does_not_turn_left_section_into_person(tmp_path):
    with pymupdf.open() as pdf:
        first = pdf.new_page()
        first.insert_text(
            ((first.rect.width - len(OWNER) * 16) / 2, 70),
            OWNER,
            fontsize=16,
            fontname="china-s",
        )
        for index in (1, 2):
            first.insert_text(
                (60, 70 + index * 25), "完整个人履历。", fontsize=12, fontname="china-s"
            )
        second = pdf.new_page()
        second.insert_text((60, 70), "荣誉", fontsize=16, fontname="china-s")
        for index in (1, 2):
            second.insert_text(
                (60, 70 + index * 25), "完整奖励记录。", fontsize=12, fontname="china-s"
            )
        data = pdf.tobytes()
    result = await audit(data, tmp_path, names={OWNER})
    assert result["biographies"][URL] == scan_media(data, "pdf")["text"].strip()
    assert not result["unresolved"]


async def audit(data, tmp_path, *, names=None):
    async def fetch(_):
        return data

    return await audit_profile_media(
        {
            URL: '<article><object data="/cv.pdf" type="application/pdf"></object></article>'
        },
        {URL: {OWNER}},
        tmp_path,
        fetch_asset=fetch,
        ocr_engine=lambda _: pytest.fail("native PDF must not invoke OCR"),
        known_names=names if names is not None else {OWNER, PEER},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "continuation",
    [
        f"[1] {OWNER}, {PEER}, 一项研究成果，期刊，2022。",
        f"{PEER}, {OWNER}, 一项研究成果，期刊，2022。",
        f"与{PEER}合作完成科研项目，保留完整原文。",
    ],
)
async def test_continuation_authors_and_collaborators_preserve_whole_pdf(
    tmp_path, continuation
):
    data = document(
        [
            [(OWNER + " 教授", 16), ("完整个人经历。", 12)],
            [(continuation, 12), ("后续完整内容。", 12)],
        ]
    )
    result = await audit(data, tmp_path)
    scanned = scan_media(data, "pdf", ocr_engine=lambda _: pytest.fail("OCR"))
    assert result["biographies"][URL] == scanned["text"].strip()
    assert not result["unresolved"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other,known,second_page",
    [
        (PEER + " 教授", {OWNER, PEER}, True),
        ("王芳 教授", {OWNER}, True),
        ("姓名：王芳", {OWNER}, False),
        ("哔甲 教授", {OWNER}, False),
        (PEER, {OWNER, PEER}, False),
    ],
)
async def test_actual_person_sections_anywhere_are_not_assigned(
    tmp_path, other, known, second_page
):
    first = [(OWNER + " 教授", 16), ("完整个人经历。", 12)]
    following = [("普通正文继续。", 12)] * 8 + [
        (other, 16),
        ("职称：教授", 12),
        ("另一个人的履历。", 12),
    ]
    data = document([first, following] if second_page else [first + following])
    result = await audit(data, tmp_path, names=known)
    assert not result["assigned_biographies"]
    assert any(
        x["status"] == "image_bio_owner_unresolved" for x in result["unresolved"]
    )


def test_ocr_without_physical_line_evidence_stays_unknown():
    from server.pdf_biography_owner import inspect_owner_scope

    data = document([[(OWNER + " 教授", 16)]])
    text = OWNER + " 教授"
    scanned = {
        "pages": [
            {
                "page": 1,
                "source": "local_ocr",
                "text": text,
                "lines": [{"text": text, "confidence": 0.99}],
            }
        ]
    }
    proof = inspect_owner_scope(data, scanned, OWNER, {OWNER}, set())
    assert proof["status"] == "unknown"


@pytest.mark.asyncio
async def test_metadata_field_before_title_is_not_another_person_heading(tmp_path):
    data = document(
        [
            [
                (OWNER + " 教授", 16),
                ("学历：博士", 12),
                ("职称：教授", 12),
                ("完整科研履历。", 12),
            ]
        ]
    )
    result = await audit(data, tmp_path)
    assert result["biographies"][URL] == scan_media(data, "pdf")["text"].strip()
    assert not result["unresolved"]


@pytest.mark.asyncio
async def test_supervisor_field_is_part_of_owners_biography(tmp_path):
    data = document(
        [[(OWNER + " 教授", 16), (f"导师：{PEER} 教授", 12), ("完整科研履历。", 12)]]
    )
    result = await audit(data, tmp_path)
    assert result["biographies"][URL] == scan_media(data, "pdf")["text"].strip()
    assert not result["unresolved"]


def test_partial_page_evidence_cannot_prove_single_owner():
    from server.pdf_biography_owner import inspect_owner_scope

    data = document([[(OWNER + " 教授", 16)], [(PEER + " 教授", 16)]])
    scanned = scan_media(data, "pdf")
    scanned["pages"] = scanned["pages"][:1]
    proof = inspect_owner_scope(data, scanned, OWNER, {OWNER}, set())
    assert proof["status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("other", ["王芳", "哔甲"])
async def test_matching_person_heading_template_cannot_merge_unknown_owner(
    tmp_path, other
):
    data = document(
        [
            [(OWNER, 16), ("完整个人履历。", 12), ("科研内容。", 12)],
            [(other, 16), ("另一个人的完整履历。", 12), ("科研内容。", 12)],
        ]
    )
    result = await audit(data, tmp_path, names={OWNER})
    assert not result["biographies"]
    assert any(
        item["status"] == "image_bio_owner_unresolved" for item in result["unresolved"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "other,confidence,expected",
    [
        (f"{PEER}, {OWNER}, 一项科研成果。", 0.99, None),
        (PEER + " 教授", 0.99, "image_bio_owner_unresolved"),
        (f"{PEER}, {OWNER}, 一项科研成果。", 0.4, "image_bio_low_confidence"),
    ],
)
async def test_scanned_pdf_roles_and_confidence_remain_separate(
    tmp_path, other, confidence, expected
):
    pages = [[OWNER + " 教授", "完整个人经历。"], [other, "后续完整内容。"]]
    native = document([[(line, 12) for line in lines] for lines in pages])
    with pymupdf.open(stream=native, filetype="pdf") as source, pymupdf.open() as pdf:
        for page in source:
            pdf.new_page(width=page.rect.width, height=page.rect.height).insert_image(
                page.rect, stream=page.get_pixmap().tobytes("png")
            )
        data = pdf.tobytes()
    remaining = iter(pages)

    def ocr(_):
        return [
            (
                [
                    [60, 70 + i * 25],
                    [350, 70 + i * 25],
                    [350, 85 + i * 25],
                    [60, 85 + i * 25],
                ],
                line,
                confidence if line == other else 0.99,
            )
            for i, line in enumerate(next(remaining))
        ], None

    async def fetch(_):
        return data

    result = await audit_profile_media(
        {
            URL: '<article><object data="/cv.pdf" type="application/pdf"></object></article>'
        },
        {URL: {OWNER}},
        tmp_path,
        fetch_asset=fetch,
        ocr_engine=ocr,
        known_names={OWNER, PEER},
    )
    if expected:
        assert not result["biographies"]
        assert any(item["status"] == expected for item in result["unresolved"])
    else:
        assert not result["unresolved"]
        assert result["biographies"][URL] == "\n\n".join(
            "\n".join(lines) for lines in pages
        )
