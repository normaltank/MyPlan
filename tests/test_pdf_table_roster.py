"""表格式 PDF 名录: a PDF whose text layer is a 序号|系别|姓名|职称 table.

The layout copies a real college PDF (two staff sections, a table continued on
the next page without its header, a two-line foreign name, a long title cell),
but every person is synthetic; the real original stays outside the repository.
Variants repeat the header on the continuation page, drop the index column or
add a page-number footer.
"""

import json

import pymupdf
import pytest

from server import name_review
from server.pdf_roster import read_pdf_rosters
from tests.test_pdf_roster import PAGE, PDF

FACULTY = [("计算机系", "赵一鸣", "教授"), ("计算机系", "钱二", "副教授"),
           ("软件工程系", "孙三立", "讲师（校聘副教授）"), ("软件工程系", "李四", "讲师"),
           ("智能科学系", ("Alex", "Morgan"), "教授"), ("智能科学系", "周五洋", "讲师")]
LAB = [("吴六", "高级实验师"), ("郑七", "助理实验师")]


def _text(page, x, y, text):
    page.insert_text((x, y), text, fontname="china-s", fontsize=12)


def _table_pdf(faculty=FACULTY, lab=LAB, *, first_heading="一、专任教师", rows_on_first_page=4, skip_index=None,
               repeat_header=False, index_column=True, footer=None):
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    _text(page, 53, 68, first_heading)
    index_header = ((64, "序号"),) if index_column else ()
    header = index_header + ((128, "系别"), (206, "姓名"), (307, "职称"))
    for x, label in header:
        _text(page, x, 86, label)
    y = 86
    for index, (department, name, title) in enumerate(faculty, 1):
        if index == rows_on_first_page + 1:
            if footer:
                _text(page, 280, 800, footer)
            # The table continues on the next page, by default without repeating its header.
            page, y = document.new_page(width=595, height=842), 50
            if repeat_header:
                for x, label in header:
                    _text(page, x, y, label)
                y += 18
        y += 18
        if index_column and index != skip_index:
            _text(page, 73, y, str(index))
        _text(page, 110, y, department)
        if isinstance(name, tuple):
            # A long name wraps onto two lines inside its cell.
            _text(page, 196, y - 6, name[0])
            _text(page, 196, y + 7, name[1])
            y += 8
        else:
            _text(page, 200, y, name)
        _text(page, 301 if len(title) <= 3 else 265, y, title)
    if lab:
        y += 30
        _text(page, 53, y, "二、实验技术人员")
        y += 18
        for x, label in index_header + ((128, "姓名"), (206, "职称")):
            _text(page, x, y, label)
        for index, (name, title) in enumerate(lab, 1):
            y += 18
            if index_column:
                _text(page, 66, y, str(index))
            _text(page, 122, y, name)
            _text(page, 188, y, title)
    return document.tobytes()


async def _read(tmp_path, original):
    async def fetch(_url):
        return original
    return await read_pdf_rosters([{"url": PAGE, "media_urls": [PDF]}], fetch_asset=fetch,
                                  evidence_dir=tmp_path / "pdf", ocr_engine=object(),
                                  name_decisions_path=tmp_path / name_review.DECISIONS_FILE)


@pytest.mark.asyncio
async def test_table_rows_become_records_with_column_fields(tmp_path):
    result = await _read(tmp_path, _table_pdf())

    people = {record.name: record for record in result["records"]}
    # Every staff section is read; its heading stays with each row.
    assert sorted(people) == sorted(["赵一鸣", "钱二", "孙三立", "李四", "周五洋", "吴六", "郑七"])
    assert people["孙三立"].title == "讲师（校聘副教授）" and people["钱二"].title == "副教授"
    assert people["吴六"].title == "高级实验师" and people["吴六"].table_row["section"] == "二、实验技术人员"
    # 跨页续接: rows on page 2 still use page 1's header columns.
    assert people["周五洋"].pages == [2] and people["周五洋"].table_row["section"] == "一、专任教师"
    assert people["赵一鸣"].table_row["cells"] == {"序号": "1", "系别": "计算机系", "姓名": "赵一鸣", "职称": "教授"}
    # A table row is not a biography.
    assert all(record.segment.text == "" and record.name_source == "pdf_table" for record in people.values())

    # The column says the wrapped cell is a name; a person confirms it.
    review = [item for item in result["unresolved"] if item["status"] == name_review.STATUS]
    assert [(item["name"], item["segment"], item["page"]) for item in review] == [("Alex Morgan", 5, 2)]
    assert [item["status"] for item in result["unresolved"]] == [name_review.STATUS]

    observation = result["observations"][PAGE][0]
    assert observation["layout"] == "table"
    assert [(item["heading"], item["rows"]) for item in observation["sections"]] == [
        ("一、专任教师", 6), ("二、实验技术人员", 2)]
    assert observation["records_detected"] == 8 and observation["records_admitted"] == 7
    assert observation["unaccounted_records"] == [5]
    layout = json.loads(open(result["evidence"][0]["layout_path"], encoding="utf-8").read())
    assert {line["record"] for line in layout if line["text"] in {"Alex", "Morgan"}} == {5}


@pytest.mark.asyncio
async def test_confirmed_name_cell_is_admitted_on_the_next_run(tmp_path):
    first = await _read(tmp_path, _table_pdf())
    item = next(item for item in first["unresolved"] if item["status"] == name_review.STATUS)
    name_review.save_decision(tmp_path / name_review.DECISIONS_FILE, item, "person", "Alex Morgan")

    second = await _read(tmp_path, _table_pdf())

    assert second["unresolved"] == []
    record = next(record for record in second["records"] if record.name == "Alex Morgan")
    assert record.name_basis == "human_reviewed_name" and record.title == "教授"
    assert len(second["records"]) == 8


@pytest.mark.asyncio
async def test_missing_index_cell_stays_unresolved(tmp_path):
    gap = await _read(tmp_path, _table_pdf(skip_index=3))
    assert "pdf_roster_table_index_gap" in {item["status"] for item in gap["unresolved"]}


@pytest.mark.asyncio
async def test_section_headings_do_not_decide_who_is_read(tmp_path):
    result = await _read(tmp_path, _table_pdf(first_heading="一、教师名单"))
    assert len(result["records"]) == 7
    assert [item["status"] for item in result["unresolved"]] == [name_review.STATUS]


@pytest.mark.asyncio
@pytest.mark.parametrize(("index_column", "footer"), [(True, None), (False, None), (True, "第 1 页")])
async def test_header_repeated_on_the_next_page_continues_its_section(tmp_path, index_column, footer):
    result = await _read(tmp_path, _table_pdf(repeat_header=True, index_column=index_column, footer=footer))

    # The rows after the repeated header are the same section: none dropped, no index gap.
    assert len(result["records"]) == 7
    assert [item["status"] for item in result["unresolved"]] == [name_review.STATUS]
    observation = result["observations"][PAGE][0]
    assert [(item["heading"], item["rows"]) for item in observation["sections"]] == [
        ("一、专任教师", 6), ("二、实验技术人员", 2)]
    assert observation["skipped_lines"]["repeated_header"] == 1


@pytest.mark.asyncio
async def test_single_table_without_sections_follows_the_branch_source(tmp_path):
    result = await _read(tmp_path, _table_pdf(FACULTY[:4], lab=[], first_heading="名单"))
    assert sorted(record.name for record in result["records"]) == ["孙三立", "李四", "赵一鸣", "钱二"]
    assert result["unresolved"] == []
