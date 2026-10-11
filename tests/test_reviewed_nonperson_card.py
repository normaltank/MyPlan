"""An explicitly reviewed introduction card is accounted for without inventing a teacher."""
import pytest

from server import name_review
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters


URL = 'https://faculty.example.edu/roster'
INTRO = 'https://faculty.example.edu/info/1358/1339.htm'
BRANCH = {'url': URL, 'single_page': True, 'card_selector': 'main ul > li'}
HTML = ('<main><ul><li><a href="/p/1">张富有</a></li><li><a href="/p/2">刘文伟</a></li>'
        '<li><a href="/p/3">孙小明</a></li><li><span>2020-06-10</span>'
        '<a href="/info/1358/1339.htm">行政法教研室简介</a></li></ul></main>')


def test_reviewed_department_introduction_accounts_for_its_card(tmp_path):
    page = CapturedRosterPage(URL, HTML, BRANCH)
    initial = reconcile_rosters([page], {})
    item = next(i for i in initial.unresolved if i.get('profile_url') == INTRO)
    name_review.save_decision(tmp_path / name_review.DECISIONS_FILE, item, 'not_person')
    final = reconcile_rosters([page], {}, name_decisions=name_review.load_decisions(tmp_path / name_review.DECISIONS_FILE))
    assert not final.unresolved
    assert len(final.candidates) == 3
    observation = final.observations[0]
    assert observation['visible_cards'] == 4 and observation['extracted_candidates'] == 3
    assert not observation['unaccounted_cards']
    assert observation['reviewed_non_person_cards'][0]['review_id'] == item['id']


@pytest.mark.parametrize('changed', [
    HTML.replace('2020-06-10', '2021-06-10'),
    HTML.replace('/info/1358/1339.htm', '/info/1358/1340.htm'),
    HTML.replace('</ul>', '<li><a href="/intro/2">刑法教研室简介</a></li></ul>'),
])
def test_changed_or_additional_unreviewed_cards_remain_blocked(tmp_path, changed):
    initial = reconcile_rosters([CapturedRosterPage(URL, HTML, BRANCH)], {})
    item = next(i for i in initial.unresolved if i.get('profile_url') == INTRO)
    path = tmp_path / name_review.DECISIONS_FILE
    name_review.save_decision(path, item, 'not_person')
    final = reconcile_rosters([CapturedRosterPage(URL, changed, BRANCH)], {},
                              name_decisions=name_review.load_decisions(path))
    assert final.unresolved
    assert all(person.name != '行政法教研室简介' for person in final.candidates)


def test_reviewed_nonpersons_alone_do_not_prove_zero_teacher_roster(tmp_path):
    html = '<main><ul><li><span>2020-06-10</span><a href="/info/1358/1339.htm">行政法教研室简介</a></li></ul></main>'
    page = CapturedRosterPage(URL, html, BRANCH)
    initial = reconcile_rosters([page], {})
    item = next(i for i in initial.unresolved if i.get('profile_url') == INTRO)
    path = tmp_path / name_review.DECISIONS_FILE
    name_review.save_decision(path, item, 'not_person')
    final = reconcile_rosters([page], {}, name_decisions=name_review.load_decisions(path))
    assert not final.candidates
    assert any(i['status'] == 'no_candidates_on_roster_page' for i in final.unresolved)


@pytest.mark.asyncio
@pytest.mark.verified_source_boundary
async def test_nonperson_decision_allows_batch_without_fetching_introduction(tmp_path):
    import json
    from server.browser_batch_tool import run_plan
    from test_browser_batch_tool import FakeContext, profile
    initial = reconcile_rosters([CapturedRosterPage(URL, HTML, BRANCH)], {})
    item = next(i for i in initial.unresolved if i.get('profile_url') == INTRO)
    name_review.save_decision(tmp_path / name_review.DECISIONS_FILE, item, 'not_person')
    context = FakeContext({URL: HTML, **{'https://faculty.example.edu/p/' + str(i): profile(name, '')
        for i, name in enumerate(('张富有', '刘文伟', '孙小明'), 1)}})
    result = await run_plan({'school': '测试大学', 'college': '测试学院',
        'source_kind': 'university_faculty_roster', 'branches': [BRANCH]}, context, tmp_path)
    assert result['delivery_ready'] and result['total'] == 3
    assert INTRO not in context.visits
    rows = json.loads((tmp_path / '测试大学_测试学院_records.json').read_text(encoding='utf-8'))
    assert len(rows) == 3 and all(row['姓名'] != '行政法教研室简介' for row in rows)
