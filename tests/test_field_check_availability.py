"""Advisory source checks must reveal missing evidence instead of looking clean."""
from server.field_checks import check_records


def test_missing_source_page_is_reported_as_unavailable_evidence():
    rows = [{'姓名': '张三', '主页': 'https://example.edu/person', '职称': '教授',
             '邮箱': '', '简介': '完整简介'}]
    result = check_records(rows, {})
    assert result['counts'] == {'source_page_unavailable': 1}
    assert result['flagged_records'] == 1
