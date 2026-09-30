"""Public extraction contracts: preserve source content and explain uncertainty."""
import pytest

from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline, FacultyProfileExtractor
from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
from server import name_review


@pytest.mark.parametrize("class_name", ["bottom-up-research", "menu-research"])
def test_authored_biography_is_not_deleted_by_class_substring(class_name):
    html = ("<html><body><article><h1>张三</h1><p>教授，主要从事材料科学研究。</p>"
            f"<div class='{class_name}'><p>研究方向：自底向上的纳米材料组装。</p></div>"
            "</article><footer>网站页脚</footer></body></html>")
    profile = FacultyProfileExtractor.extract(html, "https://example.edu/person", fallback_name="张三")
    assert "研究方向：自底向上的纳米材料组装。" in profile.full_bio
    assert "网站页脚" not in profile.full_bio


def test_verified_profile_boundary_preserves_every_authored_block():
    html = ("<html><body><article id='profile'><header>张三，教授</header>"
            "<div class='footer'>原作者结尾：全部科研成果。</div></article></body></html>")
    profile = FacultyProfileExtractor.extract(
        html, "https://example.edu/person", fallback_name="张三", preserve_content=True)
    assert "张三，教授" in profile.full_bio
    assert "原作者结尾：全部科研成果。" in profile.full_bio


@pytest.mark.parametrize("contact, expected_email, ready", [
    ("<p>电子邮箱：bluebird@example.edu</p>", "bluebird@example.edu", True),
    ("<p>电子邮箱：bluebird@example.edu；second@example.edu</p>", "", False),
    ("<p>参考资料 bluebird@example.edu</p>", "", False),
    ("", "", True),
])
def test_profile_contact_field_or_explicit_uncertainty(contact, expected_email, ready):
    url = "https://example.edu/person"
    html = ("<html><body><article><h1>张三</h1><p>教授，主要从事材料科学研究。</p>"
            + contact + "</article><footer>办公室邮箱 office@example.edu</footer></body></html>")
    result = FacultyHarvestPipeline.harvest_candidates(
        [FacultyCandidate("张三", url)], lambda _url: html,
        base_url="https://example.edu/roster")
    rows = result["sheet1_with_email"] + result["sheet2_no_email"]
    assert rows[0]["邮箱"] == expected_email
    assert result["delivery_ready"] is ready
    assert bool(result["unresolved"]) is not ready


def test_verified_roster_cards_are_the_extraction_boundary():
    side = "".join(f"<li><a href='/side/{i}'>{name}</a></li>" for i, name in enumerate(
        ["王一", "李二", "张三", "刘四", "陈五", "杨六"]))
    teachers = "".join(f"<li><a href='/people/{i}'>{name}</a><span>教授</span></li>"
                       for i, name in enumerate(["赵七", "吴八", "周九"]))
    html = (f"<html><body><section><ul>{side}</ul></section>"
            f"<main><ul id='faculty'>{teachers}</ul></main></body></html>")
    result = reconcile_rosters([CapturedRosterPage("https://example.edu/roster", html,
                               {"card_selector": "#faculty > li"})], {})
    assert [(c.name, c.url) for c in result.candidates] == [
        (name, f"https://example.edu/people/{i}") for i, name in enumerate(["赵七", "吴八", "周九"])]
    assert result.unresolved == []


def test_unrecognized_verified_name_uses_existing_review_and_binds_to_evidence(tmp_path):
    url = "https://example.edu/roster"
    html = "<ul id='faculty'><li><a href='/person'>维诺尔科娃</a></li></ul>"
    page = CapturedRosterPage(url, html, {"card_selector": "#faculty > li"})
    result = reconcile_rosters([page], {})
    assert [item['status'] for item in result.unresolved] == [name_review.STATUS]
    item = result.unresolved[0]
    assert item['name'] == '维诺尔科娃' and item['profile_url'] == 'https://example.edu/person'
    path = tmp_path / name_review.DECISIONS_FILE
    name_review.save_decision(path, item, 'person', '维诺尔科娃')
    result = reconcile_rosters([page], {}, name_decisions=name_review.load_decisions(path))
    assert [(c.name, c.url) for c in result.candidates] == [('维诺尔科娃', 'https://example.edu/person')]
    assert result.unresolved == []
    changed = CapturedRosterPage(url, html.replace('</a>', '</a><span>新标签</span>'), page.branch)
    result = reconcile_rosters([changed], {}, name_decisions=name_review.load_decisions(path))
    assert [item['status'] for item in result.unresolved] == [name_review.STATUS]


def test_two_person_links_inside_one_card_cannot_pass_one_person_count():
    page = CapturedRosterPage('https://example.edu/roster',
        "<li id='card'><a href='/one'>张三</a><a href='/two'>李四</a></li>",
        {'card_selector': '#card'})
    result = reconcile_rosters([page], {})
    assert {c.name for c in result.candidates} == {'张三', '李四'}
    assert any(item['status'] == 'roster_card_mismatch' for item in result.unresolved)


def test_name_link_is_used_instead_of_an_earlier_photo_resource():
    page = CapturedRosterPage('https://example.edu/roster',
        "<li id='card'><a href='/photo.jpg'><img src='/photo.jpg'></a>"
        "<a href='/person'>4 丁保扬</a></li>", {'card_selector': '#card'})
    result = reconcile_rosters([page], {})
    assert [(c.name, c.url) for c in result.candidates] == [('丁保扬', 'https://example.edu/person')]
    assert result.unresolved == []
