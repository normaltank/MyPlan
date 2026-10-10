"""Lossless full biographies in one workbook, without duplicating people rows."""
from __future__ import annotations

import hashlib

SHEET = '完整简介'
HEADERS = ['记录', '学校', '学院', '姓名', '主页', '片段序号', '片段总数',
           '起始字符', '结束字符', '简介片段', '全文SHA256']
CELL_LIMIT = 32767


def cell_units(text: str) -> int:
    return len(text.encode('utf-16-le')) // 2


def split_text(text: str) -> list[str]:
    """Split at Unicode character boundaries within the Excel cell limit."""
    chunks, start, units = [], 0, 0
    for index, char in enumerate(text):
        size = 2 if ord(char) > 0xffff else 1
        if units + size > CELL_LIMIT:
            chunks.append(text[start:index])
            start, units = index, 0
        units += size
    chunks.append(text[start:])
    return chunks


def prepare(records: list[dict]) -> tuple[list[dict], list[list], list[dict]]:
    """Export copies point to complete ordered original text in a support sheet."""
    copied, rows, receipts = [], [], []
    for index, original in enumerate(records, 1):
        row = dict(original)
        bio = row.get('简介') or ''
        if cell_units(bio) > CELL_LIMIT:
            parts = split_text(bio)
            digest = hashlib.sha256(bio.encode('utf-8')).hexdigest()
            first_row, offset = len(rows) + 2, 0
            for number, part in enumerate(parts, 1):
                rows.append([index, row['学校'], row['学院'], row['姓名'], row['主页'],
                             number, len(parts), offset, offset + len(part), part, digest])
                offset += len(part)
            row['简介'] = f'完整简介（{len(bio)}字符，{len(parts)}段），点击查看'
            row['_biography_row'] = first_row
            receipts.append({'record_index': index, 'characters': len(bio), 'parts': len(parts),
                             'sha256': digest, 'sheet': SHEET, 'first_row': first_row})
        copied.append(row)
    return copied, rows, receipts


def verify(workbook, records: list[dict]) -> list[dict]:
    """Reassemble from the written artifact, rejecting missing or changed text."""
    copies, expected_rows, receipts = prepare(records)
    if not receipts:
        if SHEET in workbook.sheetnames:
            raise ValueError('unexpected full biography sheet')
        return []
    if SHEET not in workbook.sheetnames:
        raise ValueError('full biography sheet missing')
    actual = list(workbook[SHEET].values)
    if tuple(actual[0]) != tuple(HEADERS) or len(actual) != len(expected_rows) + 1:
        raise ValueError('full biography sheet structure differs')
    for expected, saved in zip(expected_rows, actual[1:]):
        # Empty strings are empty cells; text fragments must retain every char.
        values = tuple(None if value == '' else value for value in expected)
        if values != tuple(saved):
            raise ValueError('full biography fragment or attribution differs')
    for receipt in receipts:
        rows = actual[receipt['first_row'] - 1:receipt['first_row'] - 1 + receipt['parts']]
        text = ''.join(row[9] for row in rows)
        if hashlib.sha256(text.encode('utf-8')).hexdigest() != receipt['sha256']:
            raise ValueError('full biography reconstruction differs')
    expected_links = []
    for row in copies:
        if '_biography_row' not in row:
            continue
        expected_links.append((row['学校'], row['学院'], row['姓名'], row['主页'],
                               row['简介'], f"'{SHEET}'!A{row['_biography_row']}"))
    actual_links = []
    for name in ('教师列表', '无邮箱教师列表'):
        for row in workbook[name].iter_rows(min_row=2):
            cell = row[8]
            if cell.hyperlink and cell.hyperlink.location:
                actual_links.append((row[4].value, row[5].value, row[1].value, row[9].value or '',
                                     cell.value, cell.hyperlink.location))
    if sorted(expected_links) != sorted(actual_links):
        raise ValueError('full biography main-sheet attribution differs')
    return receipts
