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


from server.faculty_identity import resolve_duplicate_profile_aliases  # noqa: E402

COPY = 'https://example.edu/info/1326/6049.htm'
BODY = '<header>导航</header><article id="bio">张三，教授，从事材料研究。</article>'
PROVEN = {LIVE: {'confirmed': True, 'profile_content_selector': '#bio', 'profile_content_start_selector': None}}


def test_same_profile_body_under_two_urls_is_one_person():
    people = [FacultyCandidate('张三', COPY, '教师'), FacultyCandidate('张三', LIVE, '教师')]
    profiles = {COPY: '<div>另一个栏目</div>' + BODY, LIVE: BODY}
    assert resolve_duplicate_profile_aliases(people, profiles, PROVEN, set()) == {COPY: LIVE}


def test_different_bodies_or_unproven_selector_keep_the_identity_for_review():
    people = [FacultyCandidate('张三', COPY, '教师'), FacultyCandidate('张三', LIVE, '教师')]
    other = '<article id="bio">张三，讲师，从事化学研究。</article>'
    assert not resolve_duplicate_profile_aliases(people, {COPY: other, LIVE: BODY}, PROVEN, set())
    assert not resolve_duplicate_profile_aliases(people, {COPY: '<div>张三</div>', LIVE: BODY}, PROVEN, set())
    assert not resolve_duplicate_profile_aliases(people, {COPY: BODY, LIVE: BODY}, {}, set())


def test_a_page_shared_with_another_name_is_not_merged():
    people = [FacultyCandidate('张三', COPY, '教师'), FacultyCandidate('张三', LIVE, '教师'),
              FacultyCandidate('李四', COPY, '教师')]
    assert not resolve_duplicate_profile_aliases(people, {COPY: BODY, LIVE: BODY}, PROVEN, set())
