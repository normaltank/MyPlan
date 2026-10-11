"""Pure affiliation checks for records entering a college delivery receipt."""
from __future__ import annotations

from typing import Iterable

from server.faculty_identity import ALIASES

STANDARD_FIELDS = ('序号', '姓名', '职称', '邮箱', '学校', '学院', '采集人', '采集日期', '简介', '主页')
FIELD_ALIASES = {**ALIASES, '学校': ('school',), '学院': ('college',)}


def project_personal_homepages(records: list[dict], profileless_urls: Iterable[str]) -> list[dict]:
    """Publish personal pages without rewriting the retained source records.

    A roster URI is still useful for reading, attribution and identity review;
    those steps keep it until this projection at the publishing boundary.
    """
    source_urls = set(profileless_urls)
    return [{**record, '主页': '' if record.get('主页') in source_urls else record.get('主页', '')}
            for record in records]


def export_field_value(record: dict, field: str):
    """Choose a nonempty alias without changing its original characters."""
    for key in (field, *FIELD_ALIASES.get(field, ())):
        value = record.get(key)
        if value is not None and str(value).strip():
            return value
    return ''


def alias_issues(records: list[dict]) -> list[dict]:
    """Reject contradictory nonempty aliases instead of silently choosing one."""
    issues = []
    for index, record in enumerate(records):
        for field, aliases in FIELD_ALIASES.items():
            values = [str(record[key]) for key in (field, *aliases)
                      if record.get(key) is not None and str(record[key]).strip()]
            # Biography whitespace is part of the copied source text.
            compared = values if field == '简介' else [value.strip() for value in values]
            if len(set(compared)) > 1:
                issues.append({'status': 'record_field_alias_conflict',
                               'record_index': index, 'field': field})
    return issues


def normalize_export_records(records: list[dict], *, school: str = '', college: str = '') -> list[dict]:
    """Produce ten-column export copies; never mutate identity observations or receipts."""
    issues = alias_issues(records)
    if issues:
        raise ValueError(f'delivery field validation failed: {issues}')
    normalized = []
    for record in records:
        copy = {field: export_field_value(record, field) for field in STANDARD_FIELDS}
        copy['学校'] = copy['学校'] or school
        copy['学院'] = copy['学院'] or college
        normalized.append(copy)
    return normalized


def scope_issues(records: list[dict], *, school: str, college: str | None = None) -> list[dict]:
    """Check every explicit alias; absent affiliations retain the legacy queue contract.

    This does not establish scope for legacy records that omit it. Canonical
    file-based browser receipts separately require all ten fields and scope.
    """
    issues = []
    for index, record in enumerate(records):
        for field, keys, expected in (
            ('学校', ('学校', 'school'), school),
            ('学院', ('学院', 'college'), college),
        ):
            if expected is None:
                continue
            for key in keys:
                value = record.get(key)
                if value is not None and str(value).strip() and str(value).strip() != expected.strip():
                    issues.append({'status': 'record_scope_mismatch', 'record_index': index, 'field': field})
                    break
    return issues
