"""Do not mistake shared authored biography structure for site chrome."""
from pathlib import Path

import pytest

from server.detail_mecc import DetailMECCExtractor


@pytest.mark.parametrize("container", ["article", "div"])
def test_similar_sibling_biographies_keep_all_authored_blocks(container):
    paragraphs = [
        "于2001年至2005年学习计算机科学，获学士学位。",
        "于2006年至2010年开展图结构研究，获博士学位。",
        "主持项目甲，编号A-2026；发表论文乙；承担课程丙。",
        "完整履历末段：研究资料将在2027年持续更新。",
    ]
    pages = []
    for index, name in enumerate(["张富有", "刘圣洁", "杨国才"], 1):
        email = f"000000000{index}@example.edu"
        biography = "".join(f"<p>{name}{paragraph}</p>" for paragraph in paragraphs)
        html = (f"<html><body><nav>网站导航</nav><{container}>"
                f"<div><h1>{name}</h1><p>教授</p></div><div><p>邮箱：{email}</p></div>"
                f"<div>{biography}</div></{container}><footer>网站页脚</footer></body></html>")
        pages.append((f"https://example.edu/p{index}", html, name))
    details = DetailMECCExtractor.extract_cluster(pages)
    for index, detail in enumerate(details, 1):
        assert detail.full_bio.splitlines() == [detail.name, "教授", f"邮箱：000000000{index}@example.edu",
                                               *[detail.name + p for p in paragraphs]]
        assert detail.email == f"000000000{index}@example.edu"
        assert detail.title == "教授"


def test_acceptance_profile_cluster_preserves_original_article_text():
    from bs4 import BeautifulSoup
    fixture_dir = Path(__file__).parent / "fixtures" / "harvest_acceptance" / "ordinary_full_biography"
    pages = [(f"https://example.edu/p{index}", (fixture_dir / filename).read_text(encoding="utf-8"), name)
             for index, (filename, name) in enumerate([
                 ("02_01.html", "张富有"), ("03_01.html", "刘圣洁"), ("04_01.html", "孙洁")], 1)]
    details = DetailMECCExtractor.extract_cluster(pages)
    expected_emails = ["0000000001@example.edu", "0000000002@example.edu", ""]
    for (_, html, _), detail, expected_email in zip(pages, details, expected_emails):
        expected_blocks = [tag.get_text() for tag in BeautifulSoup(html, "lxml").select("article > h1, article > p")]
        assert detail.full_bio.splitlines() == expected_blocks
        assert detail.email == expected_email
        if expected_email:
            assert expected_email in detail.full_bio


def test_cluster_container_choice_rejects_separate_shared_footer_region():
    names = ["张伟", "李娜", "王强", "赵敏", "陈刚"]
    pages = []
    for index, name in enumerate(names):
        contact = "<p>邮箱：wq@x.edu.cn</p>" if index == 2 else ""
        html = (f"<html><body><div class='x1'><h2>T</h2><p>教授</p>{contact}</div>"
                "<div class='x9'>地址：某路1号 邮箱：xyb@x.edu.cn</div></body></html>")
        pages.append((f"https://x.edu.cn/t/{index}.htm", html, name))
    details = DetailMECCExtractor.extract_cluster(pages)
    assert [d.email for d in details] == ["", "", "wq@x.edu.cn", "", ""]
    assert details[2].full_bio.splitlines() == ["T", "教授", "邮箱：wq@x.edu.cn"]
