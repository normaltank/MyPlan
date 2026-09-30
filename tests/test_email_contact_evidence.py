"""Contact-field evidence on every email path: own profile, shared page, roster card.

An address is a teacher's when it sits in a contact field (a label, a table
label cell, a mailto link) inside that teacher's own content, or when it spells
the teacher's name. An address present without such evidence, labelled on
several teachers' cards, or spelling a colleague is kept as an explicit review
item, never dropped silently into the no-email sheet.
"""
import pytest

from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline, labeled_text_emails

SHARED = "https://cs.univ.edu.cn/all.htm"
ROSTER = "https://cs.univ.edu.cn/roster.htm"


def _rows(result):
    return {row["姓名"]: row["邮箱"] for row in result["sheet1_with_email"] + result["sheet2_no_email"]}


def _statuses(result):
    return sorted((item.get("姓名"), item["status"]) for item in result["unresolved"])


def single(contact, name="张三"):
    url = "https://example.edu/person"
    html = (f"<html><body><article><h1>{name}</h1><p>教授，主要从事材料科学研究。</p>"
            + contact + "</article><footer>办公室邮箱 office@example.edu</footer></body></html>")
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate(name, url)], lambda _url: html, base_url="https://example.edu/roster")
    return _rows(result)[name], _statuses(result)


@pytest.mark.parametrize("contact", [
    "<table><tr><td>电子邮箱</td><td>bluebird@163.com</td></tr></table>",
    "<table><tr><th>电子邮箱</th><td>bluebird@163.com</td></tr></table>",
    "<dl><dt>邮箱</dt><dd>bluebird@163.com</dd></dl>",
    "<ul><li>研究方向：材料</li><li>邮箱：bluebird@163.com</li></ul>",
    "<p><span>邮箱：</span><span>bluebird@163.com</span></p>",
    "<p><a href='mailto:bluebird@163.com'>bluebird@163.com</a></p>",
    "<p><a href='mailto:bluebird@163.com?subject=hi'>发送邮件</a></p>",
    "<p>电话：0731-1234567 邮箱：bluebird@163.com</p>",
    "<p>办公地点：理科楼 个人邮箱：bluebird@163.com</p>",
])
def test_profile_contact_field_shapes_are_positive_evidence(contact):
    assert single(contact) == ("bluebird@163.com", [])


def test_profile_multi_address_contact_field_stays_a_review_item():
    assert single("<p>邮箱：bluebird@163.com；bb2@qq.com</p>") == (
        "", [("张三", "ambiguous_email_assignment")])


def test_profile_table_cell_is_a_label_only_when_it_is_only_a_label():
    email, statuses = single("<table><tr><td>发表论文请联系邮件组</td><td>bluebird@163.com</td></tr></table>")
    assert email == "" and statuses == [("张三", "ambiguous_email_assignment")]


def shared(cards, candidates=None):
    page = "<html><body><div class='list'>" + "".join(
        f"<div class='card'><h3>{name}</h3><p>{body}</p></div>" for name, body in cards) + "</div></body></html>"
    candidates = candidates or [FacultyCandidate(name=name, url=SHARED) for name, _ in cards]
    return FacultyHarvestPipeline().harvest_candidates(
        candidates=candidates, profile_fetcher=lambda _url: page, base_url=ROSTER, school="U", college="C")


def test_shared_page_card_owns_its_labelled_address_whatever_its_spelling():
    result = shared([("王晓岚", "教授 邮箱：sunflower88@163.com"), ("李强", "副教授 邮箱：liqiang@univ.edu.cn")])
    assert _rows(result) == {"王晓岚": "sunflower88@163.com", "李强": "liqiang@univ.edu.cn"}
    assert result["unresolved"] == [] and result["delivery_ready"] is True


def test_shared_page_mailto_and_split_label_are_evidence():
    page = ("<html><body><table>"
            "<tr><td>王晓岚</td><td>教授</td><td>邮箱</td><td>sunflower88@163.com</td></tr>"
            "<tr><td>李强</td><td>副教授</td><td><a href='mailto:strong_li@126.com'>写信</a></td></tr>"
            "</table></body></html>")
    candidates = [FacultyCandidate(name="王晓岚", url=SHARED), FacultyCandidate(name="李强", url=SHARED)]
    result = FacultyHarvestPipeline().harvest_candidates(
        candidates=candidates, profile_fetcher=lambda _url: page, base_url=ROSTER, school="U", college="C")
    assert _rows(result) == {"王晓岚": "sunflower88@163.com", "李强": "strong_li@126.com"}
    assert result["unresolved"] == []


def test_shared_page_address_labelled_on_several_cards_is_not_anyones():
    result = shared([("王晓岚", "教授 邮箱：sunflower88@163.com"), ("李强", "副教授 邮箱：cs-team@univ.edu.cn"),
                     ("赵敏", "讲师 邮箱：cs-team@univ.edu.cn")])
    assert _rows(result) == {"王晓岚": "sunflower88@163.com", "李强": "", "赵敏": ""}
    assert _statuses(result) == [("李强", "ambiguous_shared_email"), ("赵敏", "ambiguous_shared_email")]
    assert result["delivery_ready"] is False


def test_shared_page_unlabelled_address_without_evidence_is_a_review_item():
    result = shared([("王晓岚", "教授 sunflower88@163.com"), ("李强", "副教授 liqiang@univ.edu.cn")])
    assert _rows(result) == {"王晓岚": "", "李强": "liqiang@univ.edu.cn"}
    assert _statuses(result) == [("王晓岚", "ambiguous_email_assignment")]


def test_shared_page_unlabelled_address_on_every_card_is_a_public_contact():
    result = shared([("王晓岚", "教授 学院办公室 cs-office@univ.edu.cn"),
                     ("李强", "副教授 学院办公室 cs-office@univ.edu.cn")])
    assert _rows(result) == {"王晓岚": "", "李强": ""}
    assert result["unresolved"] == [] and result["delivery_ready"] is True


def test_shared_page_card_with_two_labelled_addresses_is_a_review_item():
    result = shared([("王晓岚", "教授 邮箱：sunflower88@163.com；xl_w@qq.com"), ("李强", "副教授")])
    assert _rows(result) == {"王晓岚": "", "李强": ""}
    assert _statuses(result) == [("王晓岚", "ambiguous_email_assignment")]


def roster_only(*cards):
    candidates = [FacultyCandidate(name=name, url=ROSTER, card_context=f"{name} {body}") for name, body in cards]
    return FacultyHarvestPipeline().harvest_candidates(
        candidates=candidates, profile_fetcher=None, base_url=ROSTER, school="U", college="C")


def test_roster_card_owns_its_labelled_address():
    result = roster_only(("王晓岚", "教授 邮箱：sunflower88@163.com"), ("李强", "副教授 E-mail: strong_li@126.com"))
    assert _rows(result) == {"王晓岚": "sunflower88@163.com", "李强": "strong_li@126.com"}
    assert result["unresolved"] == []


def test_roster_card_unlabelled_address_without_evidence_is_a_review_item():
    result = roster_only(("王晓岚", "教授 sunflower88@163.com"), ("李强", "副教授"))
    assert _rows(result) == {"王晓岚": "", "李强": ""}
    assert _statuses(result) == [("王晓岚", "ambiguous_email_assignment")]


def test_roster_card_address_labelled_on_several_cards_is_not_anyones():
    result = roster_only(("王晓岚", "教授 邮箱：cs-team@univ.edu.cn"), ("李强", "副教授 邮箱：cs-team@univ.edu.cn"))
    assert _rows(result) == {"王晓岚": "", "李强": ""}
    assert _statuses(result) == [("李强", "ambiguous_shared_email"), ("王晓岚", "ambiguous_shared_email")]


def test_roster_card_that_names_a_colleague_is_not_trusted_for_labels():
    # A card that also carries a colleague's name is not this teacher's alone.
    result = roster_only(("王晓岚", "教授 课题组联系人 李强 邮箱：strong_li@126.com"), ("李强", "副教授"))
    assert _rows(result)["王晓岚"] == ""


def test_labelled_text_addresses_follow_a_label_or_a_labelled_list():
    text = "王晓岚 教授 电子邮箱：sunflower88@163.com；xl_w@qq.com 电话：0731 课题组 lab@univ.edu.cn"
    assert labeled_text_emails(text) == ["sunflower88@163.com", "xl_w@qq.com"]
    assert labeled_text_emails("Email:  a.b@univ.edu.cn") == ["a.b@univ.edu.cn"]
    assert labeled_text_emails("联系 lab@univ.edu.cn") == []
    assert labeled_text_emails("学院公共邮箱: office@univ.edu.cn 课题组邮箱：lab@univ.edu.cn") == []
    assert labeled_text_emails("电话：0731-1234567邮箱：a@univ.edu.cn") == ["a@univ.edu.cn"]


def profiles(pages):
    candidates = [FacultyCandidate(name=name, url=url) for url, (name, _) in pages.items()]
    return FacultyHarvestPipeline.harvest_candidates(
        candidates, lambda url: pages[url][1], base_url=ROSTER, school="U", college="C")


def profile_page(name, *blocks, template=""):
    # The site template repeats its contact line inside the article body itself.
    body = "".join(f"<p>{block}</p>" for block in blocks)
    return (f"<html><body><div class='article'><h2>{name}</h2><p>教授，研究方向为数据库。</p>{body}"
            f"<p>{template}</p></div></body></html>")


@pytest.mark.parametrize("template", [
    "地址：某路1号 邮箱：office@univ.edu.cn",
    "<p>邮箱：office@univ.edu.cn</p>",
    "学院地址：某路1号 office@univ.edu.cn",
])
def test_mailbox_on_every_sibling_profile_is_the_colleges_labelled_or_not(template):
    names = ["王晓岚", "李强", "赵敏", "周健"]
    pages = {f"https://cs.univ.edu.cn/p/{i}.htm": (name, profile_page(
        name, *(["邮箱：strong_li@126.com"] if name == "李强" else []), template=template))
        for i, name in enumerate(names)}
    result = profiles(pages)
    assert _rows(result) == {"王晓岚": "", "李强": "strong_li@126.com", "赵敏": "", "周健": ""}
    assert result["unresolved"] == [] and result["delivery_ready"] is True


def test_colleague_address_quoted_on_a_profile_stays_with_the_colleague():
    quoted = "课题组联系人：李强 邮箱：liqiang@univ.edu.cn"
    pages = {"https://cs.univ.edu.cn/p/1.htm": ("王晓岚", profile_page("王晓岚", quoted)),
             "https://cs.univ.edu.cn/p/2.htm": ("李强", profile_page("李强", "邮箱：liqiang@univ.edu.cn"))}
    assert _rows(profiles(pages)) == {"王晓岚": "", "李强": "liqiang@univ.edu.cn"}
    # Quoted only on the other page, it is still the colleague's, not the page owner's.
    pages["https://cs.univ.edu.cn/p/2.htm"] = ("李强", profile_page("李强"))
    result = profiles(pages)
    assert _rows(result) == {"王晓岚": "", "李强": ""}
