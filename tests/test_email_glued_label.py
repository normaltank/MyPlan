"""DOM fields supply address boundaries; mixed domain casing does not."""
import pytest

from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline, find_emails
from server.detail_mecc import find_emails as mecc_find_emails, labeled_text_emails


@pytest.mark.parametrize("text,expected", [
    ("Email: zhangsan@hnu.edu.cnTel: 0731", "zhangsan@hnu.edu.cnTel"),
    ("E-mail：li_si@163.comPhone：138", "li_si@163.comPhone"),
    ("wang-wu@x.edu.cnHomepage", "wang-wu@x.edu.cnHomepage"),
    ("MAIL: ZHAO@HNU.EDU.CNTel 0731", "ZHAO@HNU.EDU.CNTel"),
    ("E-mail:ZhangSan@HNU.EDU.CN", "ZhangSan@HNU.EDU.CN"),
    ("x@y.cn2019年", "x@y.cn"),
    ("zhangsan (at) hnu.edu.cn", "zhangsan@hnu.edu.cn"),
])
def test_plain_text_does_not_invent_a_domain_boundary(text, expected):
    assert find_emails(text) == [expected]
    assert mecc_find_emails(text) == [expected]


def test_plain_contact_evidence_does_not_rewrite_the_address():
    assert labeled_text_emails("电话：0731-1234 Email: zhangsan@hnu.edu.cnFax: 0731-5678") == [
        "zhangsan@hnu.edu.cnFax"]


def test_profile_with_inline_contact_fields_delivers_the_real_address():
    url = "https://x.example.edu.cn/p/1.htm"
    html = ("<html><body><div class='content'><h1>李华</h1><p><span>Email: lihua@example.edu.cn</span>"
            "<span>Tel: 0731-8888</span></p><p>李华长期从事教学科研工作。</p></div></body></html>")
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate("李华", url, "教授")], profile_fetcher={url: html}.get,
        base_url="https://x.example.edu.cn/list.htm", school="样本大学", college="样本学院")
    assert [row["邮箱"] for row in result["sheet1_with_email"]] == ["lihua@example.edu.cn"]


@pytest.mark.parametrize('domain', ['example.coM', 'example.neT', 'example.acADEMY'])
def test_mixed_case_domain_is_preserved_verbatim(domain):
    address = 'lihua@' + domain
    assert find_emails('邮箱：' + address) == [address]
    assert mecc_find_emails('邮箱：' + address) == [address]


def test_shared_profiles_use_dom_field_boundaries_without_rewriting_biographies():
    url = 'https://faculty.example.edu/people'
    html = ('<main><article><h2>李华</h2><span>lihua@example.edu.cn</span>'
            '<span>Tel:123</span><p>完整履历甲。</p></article>'
            '<article><h2>王芳</h2><span>wangfang@example.coM</span>'
            '<span>Tel:456</span><p>完整履历乙。</p></article></main>')
    report = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate('李华', url, '教授'), FacultyCandidate('王芳', url, '讲师')],
        profile_fetcher={url: html}.get, base_url='https://faculty.example.edu/list')
    assert report['unresolved'] == []
    assert [r['邮箱'] for r in report['sheet1_with_email']] == [
        'lihua@example.edu.cn', 'wangfang@example.coM']
    assert report['sheet1_with_email'][0]['简介'] == '李华\nlihua@example.edu.cnTel:123\n完整履历甲。'
