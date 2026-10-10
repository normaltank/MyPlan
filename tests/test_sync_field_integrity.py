"""Local browser receipts reject lossy fields before any synchronization."""
import json

import pytest

from server.browser_batch_sync import build_payload
from test_browser_batch_sync import _report


def change_record(path, **fields):
    report = json.loads(path.read_text(encoding='utf-8'))
    records_path = path.parent / 'records.json'
    records = json.loads(records_path.read_text(encoding='utf-8'))
    records[0].update(fields)
    records_path.write_text(json.dumps(records), encoding='utf-8')
    return report


@pytest.mark.parametrize('email', [None, '', '   '])
def test_empty_email_receipt_matches_no_email_counts(tmp_path, email):
    path, root = _report(tmp_path)
    report = change_record(path, 邮箱=email)
    report.update(with_email=0, no_email=1)
    path.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
    payload = build_payload(path, 'worker', 'lease', root)
    assert payload['stats']['with_email'] == 0 and payload['stats']['no_email'] == 1
    assert payload['records'][0]['邮箱'] == email


def test_long_biography_receipt_is_rejected_without_editing_source(tmp_path):
    path, root = _report(tmp_path)
    change_record(path, 简介='甲' * 32768)
    with pytest.raises(ValueError, match='full biography workbook'):
        build_payload(path, 'worker', 'lease', root)
    assert len(json.loads((tmp_path / 'records.json').read_text(encoding='utf-8'))[0]['简介']) == 32768


@pytest.mark.parametrize('invalid', ['\ud800', '\ufffe', '\uffff'])
def test_invalid_xml_receipt_is_rejected_without_editing_source(tmp_path, invalid):
    path, root = _report(tmp_path)
    raw = f'原文{invalid}履历'
    change_record(path, 简介=raw)
    with pytest.raises(ValueError, match='excel_invalid_xml_character'):
        build_payload(path, 'worker', 'lease', root)
    assert json.loads((tmp_path / 'records.json').read_text(encoding='utf-8'))[0]['简介'] == raw
