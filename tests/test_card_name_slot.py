"""A card the reader cannot name is read where the other cards keep their names."""
from __future__ import annotations

from pathlib import Path

from server.faculty_extractor import FacultyDirectoryExtractor

ROSTER_URL = "https://art.x.edu.cn/szjs/2.htm"
PAGE = Path(__file__).parent / "fixtures" / "legacy_pages" / "xafa_youhua_szjs_page2.html"


def _roster(names):
    # Name and summary share one link, so the whole link text is never a name.
    cards = "".join(
        f'<li><span><i>{i}</i>2020-10</span><a href="../info/{i}.htm"><h2>{name}</h2>'
        f'<p>1971年出生于陕西。毕业于本校并留校任教，中国美协会员，作品多次入选全国美展。</p></a></li>'
        for i, name in enumerate(names))
    return f"<html><body><ul class='list'>{cards}</ul></body></html>"


def _read(html, selector="ul.list li"):
    found, review = FacultyDirectoryExtractor._verified_card_candidates(html, ROSTER_URL, selector)
    return [person.name for person in found], [person.name for person in review]


def test_rare_surname_card_is_read_from_the_shared_name_slot():
    found, review = _read(_roster(["王建国", "李明", "张伟", "强世军"]))
    assert found == ["王建国", "李明", "张伟", "强世军"]
    assert review == []


def test_summary_words_are_not_taken_for_a_name():
    # '中国美协' sits in the summary; only the agreed slot is read, so no word
    # from the summary is admitted in place of the card's name.
    found, _ = _read(_roster(["王建国", "李明", "张伟", "强世军"]))
    assert not any("中" in name for name in found)


def test_without_three_agreeing_cards_the_slot_is_not_trusted():
    found, review = _read(_roster(["王建国", "李明", "强世军"]))
    assert found == ["王建国", "李明"]
    assert review == []


def test_saved_roster_page_accounts_for_every_card():
    html = PAGE.read_text(encoding="utf-8")
    found, review = FacultyDirectoryExtractor._verified_card_candidates(
        html, "https://youhua.xafa.edu.cn/szjs/2.htm", "li[id^=line_u9_]")
    assert len(found) == 10 and review == []
    assert any(person.name == "强世军" and person.url.endswith("/info/1173/2801.htm") for person in found)
