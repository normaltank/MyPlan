"""Contact contradictions survive source reconciliation and admission decisions."""
import pytest

from server import name_review
from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline
from server.faculty_identity import merge_same_name_observations
from server.harvest_eligibility import classify_candidates
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters


ROSTER = 'https://example.edu/roster'
CONFLICT = (('wangfang@example.edu', 'wrong@example.edu'),)


def assert_blocked(candidates):
    result = FacultyHarvestPipeline.harvest_candidates(
        candidates, None, base_url=ROSTER, profileless_urls={c.url for c in candidates})
    assert result['delivery_ready'] is False
    assert any(item['status'] == 'mailto_address_conflict' for item in result['unresolved'])
    assert all(not row['邮箱'] for row in result['sheet1_with_email'] + result['sheet2_no_email'])


@pytest.mark.parametrize('with_profile', [True, False])
def test_merging_supplementary_observation_preserves_contact_conflict(with_profile):
    keeper = FacultyCandidate('王芳', 'https://example.edu/person' if with_profile else ROSTER)
    listed = FacultyCandidate('王芳', ROSTER + '/extra', mailto_conflicts=CONFLICT)
    candidates, _, merges = merge_same_name_observations(
        [keeper, listed], {}, {ROSTER, listed.url})
    assert len(candidates) == len(merges) == 1
    assert_blocked(candidates)


def test_verified_hospital_appointment_preserves_contact_conflict():
    person = FacultyCandidate('王芳', 'https://example.edu/doctor', mailto_conflicts=CONFLICT)
    quote = '王芳现为样本大学教授'
    admitted, _, unresolved = classify_candidates(
        [person], {(person.name, person.url): {'hospital_doctor_roster'}},
        {person.url: f'<article>{quote}</article>'},
        {'school': '样本大学', 'college': '医学院', 'verified_appointments': [
            {'name': person.name, 'profile_url': person.url, 'quote': quote, 'academic_title': '教授'}]})
    assert unresolved == [] and admitted[0].title == '教授'
    assert_blocked(admitted)


def test_confirming_unknown_name_does_not_confirm_its_contact(tmp_path):
    html = ('<main><li><a href="/person">维诺尔科娃</a><p>邮箱：'
            '<a href="mailto:wrong@example.edu">wangfang@example.edu</a></p></li></main>')
    page = CapturedRosterPage(ROSTER, html, {'card_selector': 'main li'})
    initial = reconcile_rosters([page], {})
    item = next(item for item in initial.unresolved if item['status'] == name_review.STATUS)
    path = tmp_path / name_review.DECISIONS_FILE
    name_review.save_decision(path, item, 'person', '维诺尔科娃')
    reviewed = reconcile_rosters([page], {}, name_decisions=name_review.load_decisions(path))
    assert [person.name for person in reviewed.candidates] == ['维诺尔科娃']
    assert_blocked(reviewed.candidates)


def test_repeated_roster_observation_cannot_replace_conflicted_candidate():
    card = '<main><li><span>王芳</span><p>教授</p>{}</li></main>'
    conflict = '<p>邮箱：<a href="mailto:wrong@example.edu">wangfang@example.edu</a></p>'
    pages = [CapturedRosterPage(ROSTER, card.format(contact), {'card_selector': 'main li'})
             for contact in (conflict, '')]
    reconciled = reconcile_rosters(pages, {})
    assert len(reconciled.candidates) == 1
    assert_blocked(reconciled.candidates)


def test_supplementary_name_chain_cannot_discard_card_contact_conflict():
    html = ('<ul><li>王芳、李强<p>邮箱：'
            '<a href="mailto:wrong@example.edu">wangfang@example.edu</a></p></li></ul>')
    result = reconcile_rosters([CapturedRosterPage(ROSTER, html, {'card_selector': 'li'})], {})
    assert {person.name for person in result.candidates} == {'王芳', '李强'}
    assert any(item['status'] == 'mailto_address_conflict' for item in result.unresolved)
