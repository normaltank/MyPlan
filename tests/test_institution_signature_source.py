"""Complete same-page university/college signatures, without substring aliases."""
from pathlib import Path

import pytest

from server.source_qualification import witness

URL = 'http://my.czu.edu.cn/xygk/xrld.htm'
TARGET = {'school': '池州学院', 'college': '马克思主义学院'}
CLAIM = {'source_role': 'organizational_membership', 'source_container_selector': '.list-main-right',
         'role_evidence_selector': '.lm_name', 'source_context_selector': '.logo, .footer'}
HTML = Path('tests/fixtures/source_disposition/chizhou-current-leaders.html').read_text(encoding='utf8')
TITLE = '现任领导-池州学院马克思主义学院'


@pytest.mark.parametrize('heading', ['.lm_name', '.lm_name h2'])
def test_complete_signature_on_real_original_is_bound_to_both_institutions(heading):
    value = witness(HTML, URL, {**CLAIM, 'role_evidence_selector': heading}, TARGET)
    assert value['document_title_branding'] == {'school': True, 'college': True}
    assert value['college_bound'] and value['non_faculty_purpose']


@pytest.mark.parametrize('title,target', [
    ('现任领导-其它大学马克思主义学院', TARGET),
    ('现任领导-池州学院商学院', TARGET),
    ('现任领导-访问池州学院马克思主义学院', TARGET),
    ('现任领导-池州学院马克思主义学院校友网', TARGET),
    (TITLE, {**TARGET, 'school': '其它大学'}),
    (TITLE, {**TARGET, 'college': '商学院'}),
])
def test_wrong_or_partial_signature_cannot_replace_institution_context(title, target):
    value = witness(HTML.replace(TITLE, title, 1), URL, CLAIM, target)
    assert not value['college_bound'] and not value['non_faculty_purpose']


def test_leaders_do_not_become_a_faculty_roster_and_body_is_not_branding():
    assert not witness(HTML, URL, {**CLAIM, 'source_role': 'faculty_roster'}, TARGET)['role_supported']
    value = witness(HTML, URL, {**CLAIM, 'source_context_selector': 'body'}, TARGET)
    assert not value['college_bound'] and not value['non_faculty_purpose']
