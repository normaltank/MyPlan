from server.faculty_extractor import FacultyCandidate
from server.faculty_identity import resolve_broken_profile_aliases


DEAD = 'https://example.edu/info/1012/2537.htm'
LIVE = 'https://example.edu/info/1011/1433.htm'


def test_unique_live_profile_on_official_roster_resolves_repeated_broken_link():
    people = [FacultyCandidate('张三', DEAD, '教师'), FacultyCandidate('张三', LIVE, '教师')]
    errors = [{'url': DEAD, 'status': 'profile_navigation_failed', 'error': 'RuntimeError: HTTP 404'}]
    evidence = {LIVE: {'confirmed': True, 'profile_content_selector': '#bio',
                       'profile_content_start_selector': None}}
    sources = {DEAD: [{'roster_url': 'https://example.edu/one'},
                      {'roster_url': 'https://example.edu/two'}]}
    profiles = {DEAD: None, LIVE: '<article id="bio">张三的个人主页</article>'}
    assert resolve_broken_profile_aliases(people, profiles, errors, evidence, sources) == {DEAD: LIVE}


def test_broken_link_alias_requires_independent_sources_and_verified_identity():
    people = [FacultyCandidate('张三', DEAD, '教师'), FacultyCandidate('张三', LIVE, '教师')]
    errors = [{'url': DEAD, 'status': 'profile_navigation_failed', 'error': 'RuntimeError: HTTP 404'}]
    sources = {DEAD: [{'roster_url': 'https://example.edu/one'}]}
    profiles = {DEAD: None, LIVE: '<article id="bio">张三的个人主页</article>'}
    evidence = {LIVE: {'confirmed': True, 'profile_content_selector': '#bio',
                       'profile_content_start_selector': None}}
    assert not resolve_broken_profile_aliases(people, profiles, errors, evidence, sources)
    sources[DEAD].append({'roster_url': 'https://example.edu/two'})
    profiles[LIVE] = '<article id="bio">其他人的个人主页</article>'
    assert not resolve_broken_profile_aliases(people, profiles, errors, evidence, sources)
