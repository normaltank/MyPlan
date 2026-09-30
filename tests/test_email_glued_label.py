"""An address followed directly by the next field's English label is cut at the label.

Inline markup such as ``<span>Email: a@b.edu.cn</span><span>Tel: …</span>`` has
no block boundary, so the text stream reads ``a@b.edu.cnTel``. The domain
pattern took ``cnTel`` as the top-level domain and delivered a broken address
in Sheet 1. A top-level domain never switches from lower to upper case, so the
capital that starts the next word ends the address; all-capital domains stay.
"""
import pytest

from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline, find_emails
from server.detail_mecc import find_emails as mecc_find_emails, labeled_text_emails


@pytest.mark.parametrize("text,expected", [
    ("Email: zhangsan@hnu.edu.cnTel: 0731", "zhangsan@hnu.edu.cn"),
    ("E-mail：li_si@163.comPhone：138", "li_si@163.com"),
    ("wang-wu@x.edu.cnHomepage", "wang-wu@x.edu.cn"),
    ("MAIL: ZHAO@HNU.EDU.CNTel 0731", "ZHAO@HNU.EDU.CN"),
    ("E-mail:ZhangSan@HNU.EDU.CN", "ZhangSan@HNU.EDU.CN"),
    ("x@y.cn2019年", "x@y.cn"),
    ("zhangsan (at) hnu.edu.cn", "zhangsan@hnu.edu.cn"),
])
def test_address_ends_where_the_next_label_starts(text, expected):
    assert find_emails(text) == [expected]
    assert mecc_find_emails(text) == [expected]


def test_labelled_address_keeps_its_label_evidence_after_the_cut():
    assert labeled_text_emails("电话：0731-1234 Email: zhangsan@hnu.edu.cnFax: 0731-5678") == [
        "zhangsan@hnu.edu.cn"]


def test_profile_with_inline_contact_fields_delivers_the_real_address():
    url = "https://x.example.edu.cn/p/1.htm"
    html = ("<html><body><div class='content'><h1>李华</h1><p><span>Email: lihua@example.edu.cn</span>"
            "<span>Tel: 0731-8888</span></p><p>李华长期从事教学科研工作。</p></div></body></html>")
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate("李华", url, "教授")], profile_fetcher={url: html}.get,
        base_url="https://x.example.edu.cn/list.htm", school="样本大学", college="样本学院")
    assert [row["邮箱"] for row in result["sheet1_with_email"]] == ["lihua@example.edu.cn"]
