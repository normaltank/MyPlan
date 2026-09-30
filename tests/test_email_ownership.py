"""An address belongs to the teacher whose own card or profile body shows it.

Domain and spelling do not decide ownership: many teachers publish a personal
163.com / qq.com address or a nickname that spells nothing.
"""
from server.faculty_extractor import (FacultyCandidate, FacultyDirectoryExtractor, FacultyHarvestPipeline,
                                      own_text_emails)

BASE = "https://cs.univ.edu.cn/faculty.htm"


def profile(name, *lines):
    body = "".join(f"<p>{line}</p>" for line in lines)
    return f"<html><body><div class='article'><h1>{name}</h1><p>从事数据库研究。</p>{body}</div></body></html>"


def harvest(pages, candidates=None):
    candidates = candidates or [FacultyCandidate(name=name, url=url, title="教授") for url, (name, _) in pages.items()]
    result = FacultyHarvestPipeline().harvest_candidates(
        candidates=candidates, profile_fetcher=lambda url: pages[url][1], base_url=BASE,
        school="测试大学", college="计科院")
    assert result["is_conserved"] is True
    return {row["姓名"]: row["邮箱"] for row in result["sheet1_with_email"] + result["sheet2_no_email"]}


def test_personal_address_on_own_profile_is_kept_whatever_its_domain():
    pages = {"https://cs.univ.edu.cn/p/1.htm": ("王晓岚", profile("王晓岚", "联系邮箱：sunflower88@163.com")),
             "https://cs.univ.edu.cn/p/2.htm": ("李强", profile("李强", "E-mail: 20251314@qq.com")),
             "https://cs.univ.edu.cn/p/3.htm": ("赵敏", profile("赵敏", "邮箱：lab-office@univ.edu.cn"))}
    assert harvest(pages) == {"王晓岚": "sunflower88@163.com", "李强": "20251314@qq.com",
                              "赵敏": "lab-office@univ.edu.cn"}


def test_address_on_several_teachers_pages_is_a_shared_contact():
    office = "学院办公室：cs-office@univ.edu.cn"
    pages = {"https://cs.univ.edu.cn/p/1.htm": ("王晓岚", profile("王晓岚", office)),
             "https://cs.univ.edu.cn/p/2.htm": ("李强", profile("李强", office, "邮箱：strong_li@126.com"))}
    assert harvest(pages) == {"王晓岚": "", "李强": "strong_li@126.com"}


def test_colleague_address_quoted_on_another_page_stays_with_the_colleague():
    pages = {"https://cs.univ.edu.cn/p/1.htm": ("王晓岚", profile("王晓岚", "课题组联系人：李强 liqiang@univ.edu.cn")),
             "https://cs.univ.edu.cn/p/2.htm": ("李强", profile("李强", "邮箱：liqiang@univ.edu.cn"))}
    assert harvest(pages) == {"王晓岚": "", "李强": "liqiang@univ.edu.cn"}
    # Quoted only on the other page, it is still the colleague's, not the page owner's.
    pages["https://cs.univ.edu.cn/p/2.htm"] = ("李强", profile("李强"))
    assert harvest(pages) == {"王晓岚": "", "李强": ""}


def test_own_address_that_spells_the_name_is_listed_first():
    pages = {"https://cs.univ.edu.cn/p/1.htm": ("王晓岚", profile(
        "王晓岚", "邮箱：sunny2008@qq.com", "工作邮箱：wangfang@univ.edu.cn"))}
    assert harvest(pages) == {"王晓岚": "wangfang@univ.edu.cn"}


def test_card_on_a_shared_page_owns_its_address():
    page = ("<html><body><div class='list'>"
            "<div class='card'><h3>王晓岚</h3><p>教授 邮箱：sunflower88@163.com</p></div>"
            "<div class='card'><h3>李强</h3><p>副教授 邮箱：cs-team@univ.edu.cn</p></div>"
            "<div class='card'><h3>赵敏</h3><p>讲师 邮箱：cs-team@univ.edu.cn</p></div>"
            "</div></body></html>")
    shared = "https://cs.univ.edu.cn/all.htm"
    candidates = [FacultyCandidate(name="王晓岚", url=shared, card_context="王晓岚 教授 邮箱：sunflower88@163.com"),
                  FacultyCandidate(name="李强", url=shared, card_context="李强 副教授 邮箱：cs-team@univ.edu.cn"),
                  FacultyCandidate(name="赵敏", url=shared, card_context="赵敏 讲师 邮箱：cs-team@univ.edu.cn")]
    result = FacultyHarvestPipeline().harvest_candidates(
        candidates=candidates, profile_fetcher=lambda url: page, base_url=BASE, school="测试大学", college="计科院")
    emails = {row["姓名"]: row["邮箱"] for row in result["sheet1_with_email"] + result["sheet2_no_email"]}
    assert emails == {"王晓岚": "sunflower88@163.com", "李强": "", "赵敏": ""}


def test_biography_text_addresses_skip_only_a_colleagues_spelling():
    text = "联系方式：sunflower88@163.com；课题组：liqiang@univ.edu.cn"
    assert own_text_emails(text, "王晓岚", {"王晓岚", "李强"}) == ["sunflower88@163.com"]
    assert own_text_emails(text, "王晓岚") == ["sunflower88@163.com", "liqiang@univ.edu.cn"]


def test_degree_after_a_name_is_not_part_of_it():
    assert FacultyDirectoryExtractor._clean_name("林海 博士") == ("林海", "教师")
    assert FacultyDirectoryExtractor._clean_name("欧阳明日博士") == ("欧阳明日", "教师")
    assert FacultyDirectoryExtractor._clean_name("师资博士后")[0] == "师资博士后"


def test_card_named_by_its_own_text_when_every_link_says_view_details():
    names = ["韩立新", "罗雪梅", "周健", "玛丽亚·安娜·维加", "邓启明"]
    cards = "".join(
        f'<li><dl><dt><img src="/p{i}.jpg"/></dt><dd><p><strong>姓 名：</strong>{name}</p>'
        f'<p><strong>职 称：</strong>教授</p><p><strong>研究方向：</strong>声乐演唱{i}</p>'
        f'<p><a href="../info/1033/{i}.htm">查看详情 &gt;&gt;</a></p></dd></dl></li>'
        for i, name in enumerate(names))
    html = f"<html><body><div class='nytu_list2'><ul>{cards}</ul></div></body></html>"
    found, review = FacultyDirectoryExtractor.extract_with_review(html, "https://a.edu.cn/szdw/js.htm")
    assert [candidate.name for candidate in found + review] == names
    assert found[0].url == "https://a.edu.cn/info/1033/0.htm"
