"""Replay anonymised contact markup from all eight batch4 official profiles.

Fixtures retain tags, styling, inline address splits and NBSP spacing; identities
and addresses are synthetic. Full captures and source hashes stay in scratch.
"""
import json
from pathlib import Path

import pytest

from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline, FacultyProfileExtractor
from server.detail_mecc import labeled_text_emails
from server import html_email_review

CASES = json.loads((Path(__file__).parent / 'fixtures/email_batch4_contacts.json').read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', CASES, ids=[c['姓名'] for c in CASES])
def test_contact_field_recognizes_all_published_addresses(case):
    url, name = case['主页'], case['姓名']
    html = f'<article><h1>{name}</h1>{case["contact_html"]}</article>'
    profile = FacultyProfileExtractor.extract(html, url, fallback_name=name, preserve_content=True)
    assert list(profile.contact_emails) == case['matches']
    candidate = FacultyCandidate(name, url)
    result = FacultyHarvestPipeline.harvest_candidates(
        candidates=[candidate], profile_fetcher=lambda _: html,
        base_url='https://fixture.example.edu/roster', school='样本大学', college='样本学院',
        verified_profile_urls={url})
    records = result['sheet1_with_email'] + result['sheet2_no_email']
    assert len(records) == 1
    if len(case['matches']) == 1:
        assert records[0]['邮箱'] == case['matches'][0]
        assert result['is_conserved'] is True
        assert not result['unresolved']
    else:
        assert records[0]['邮箱'] == ''
        assert [issue['status'] for issue in result['unresolved']] == ['ambiguous_email_assignment']
        [item] = html_email_review.build_items(result['unresolved'], records, [candidate], {url: html})
        assert len(item['candidates']) == 2 and all(c['selectable'] for c in item['candidates'])
        assert item['none_allowed'] is False


@pytest.mark.parametrize('label', ['邮 箱', '电 子 邮 箱', '联 系 邮 箱', '电 子 邮 件', '信 箱 地 址'])
def test_spaced_contact_labels_work_in_running_text(label):
    assert labeled_text_emails(f'电话：12345 {label}：nickname@163.com') == ['nickname@163.com']


def test_spacing_does_not_turn_a_qualified_public_contact_into_a_personal_label():
    for label in ['学院公共邮 箱', '课题组邮 箱', '他人电 子 邮 件']:
        assert labeled_text_emails(f'{label}：office@example.edu') == []


def test_user_selected_first_published_email_releases_only_the_bound_conflict(tmp_path):
    case = CASES[-1]
    url, name = case['主页'], case['姓名']
    html = f'<article><h1>{name}</h1>{case["contact_html"]}</article>'
    candidate = FacultyCandidate(name, url)
    result = FacultyHarvestPipeline.harvest_candidates(
        candidates=[candidate], profile_fetcher=lambda _: html,
        base_url='https://fixture.example.edu/roster', school='样本大学', college='样本学院',
        verified_profile_urls={url})
    records = result['sheet1_with_email'] + result['sheet2_no_email']
    [item] = html_email_review.build_items(result['unresolved'], records, [candidate], {url: html})
    out = tmp_path / 'tasks' / 'fixture-task' / 'attempt-1'
    # Account 1 is a test reviewer in an isolated task, never a production identity.
    html_email_review.save_decision(out / html_email_review.DECISIONS_FILE, item, 'select',
                                    'sample7@example.edu', task_id='fixture-task', reviewed_by=1)
    bio = records[0]['简介']
    remaining, pending, certificates = html_email_review.apply_decisions(
        result['unresolved'], records, [candidate], {url: html}, out)
    assert remaining == pending == []
    assert records[0]['邮箱'] == 'sample7@example.edu'
    assert records[0]['简介'] == bio and 'alternate7@163.com' in bio
    assert certificates[0]['email'] == records[0]['邮箱']
