"""Purpose and member identity stay separate; every branding region must qualify."""
import pytest

from server.source_qualification import witness


@pytest.mark.parametrize('title', ['法学专业建设与发展委员会', '院学术分委员会'])
def test_organizational_source_context_explains_extra_breadcrumb_and_preserves_unknown_members(title):
    html = (f'<title>{title}</title><h1>{title}</h1>'
            '<div class="subpostion">首页 学院概况 机构设置</div>'
            '<main>组织成员原文；此处不提供本人的师资任职证据。</main>'
            '<footer>Copyright 样本大学样本学院</footer>')
    target = {'school': '样本大学', 'college': '样本学院'}
    claim = {'source_role': 'organizational_membership', 'source_container_selector': 'main',
             'role_evidence_selector': 'h1', 'source_context_selector': 'footer,.subpostion'}
    bad = witness(html, 'https://example.edu/organization', claim, target)
    assert bad['source_context_issue'] == 'source_context_not_institutional'
    assert bad['non_institutional_context_count'] == 1
    assert bad['college_bound'] is False and bad['non_faculty_purpose'] is False
    good = witness(html, 'https://example.edu/organization', {**claim, 'source_context_selector': 'footer'}, target)
    assert good['source_context_issue'] is None
    assert good['non_institutional_context_count'] == 0
    assert good['college_bound'] is True and good['non_faculty_purpose'] is True
    # Correct source type supplies no affirmative faculty permission.
    faculty = witness(html, 'https://example.edu/organization', {
        **claim, 'source_context_selector': 'footer', 'source_role': 'faculty_roster'}, target)
    assert faculty['role_supported'] is False
