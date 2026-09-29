"""Field-found recall gaps in the rank prior and name-shape gate.

Two 湖南师范大学 colleges independently hit the same class of silent recall loss
during the 2026-09-24 batch:

* 历史文化学院 dropped 傅新球 / 粟壬波 / 邸宏霆 — surnames absent from
  Opus/iso_mining._SURNAMES, so name_prob() returned the 0.08 rare-surname
  default and server/faculty_extractor.py discarded the record at the p < 0.5 gate.
* 生命科学学院 dropped 银海强 / 容明强 / 穰杰 / 禹琪芳 the same way, and 张国进
  because the roster template pads the name with a zero-width space (U+200B),
  which matched no name shape and scored 0.03.

Both audits surfaced the loss as roster_card_mismatch and blocked delivery, so
the repair belongs in the prior and the shape normalisation, not in a plan.
"""
from Opus.iso_mining import name_prob
from server.faculty_extractor import FacultyDirectoryExtractor

FIELD_NAMES = ["傅新球", "粟壬波", "邸宏霆", "银海强", "容明强", "穰杰", "禹琪芳"]
ZWSP_NAME = "张\u200b国进"
ROSTER = FIELD_NAMES + ["王伟", "李静", "张涛", "刘洋", "陈晨", "杨帆", "黄磊", "周敏"]


def test_added_surnames_do_not_admit_common_words():
    """Precision guard: the prior admits any listed surname, so a surname whose
    lead character starts ordinary words would silently inject non-people into a
    roster. These were deliberately left out of the tuple."""
    for word in ["宿舍", "索引", "薄膜", "韶山", "怀念", "劳动", "融合", "幸福",
                 "湛蓝", "茅草", "裘皮", "钮扣", "糜烂", "莘莘", "宦官", "贡献"]:
        assert name_prob(word) < 0.5, f"{word} would be admitted as a person"


def test_verified_field_surnames_clear_the_name_prior():
    for name in FIELD_NAMES:
        assert name_prob(name) >= 0.5, f"{name} still below the extraction gate"


def test_zero_width_padded_name_is_still_a_name():
    assert name_prob(ZWSP_NAME) >= 0.5


def test_roster_cards_with_rare_surnames_are_all_recalled():
    names = ROSTER[:6] + [ZWSP_NAME] + ROSTER[6:]
    cards = "".join(
        f'<li class="card"><a href="/info/{i}.htm"><h3>{n}</h3><p>教授</p></a></li>'
        for i, n in enumerate(names)
    )
    html = f'<html><body><div class="wrapper"><ul class="list">{cards}</ul></div></body></html>'
    candidates = FacultyDirectoryExtractor.extract(html, "http://ls.example.edu.cn/szdw/js.htm")
    found = {c.name for c in candidates}
    missing = [n for n in names if n.replace("\u200b", "") not in found]
    assert not missing, f"cards dropped by the name gate: {missing}"
    assert len(candidates) == len(names)
    assert "张国进" in found, "the zero-width pad must be normalised out of the stored name"
    assert not any("\u200b" in name for name in found), "stored names must carry no invisible padding"


def test_unlisted_surname_in_repeated_faculty_cards_uses_group_evidence():
    assert name_prob("亓文轩") < 0.5
    names = ["王伟", "李静", "亓文轩", "张涛", "刘洋"]
    cards = "".join(
        f'<li class="faculty"><a href="/profile/{i}.htm"><h3>{name}</h3><p>教授</p></a></li>'
        for i, name in enumerate(names)
    )
    noise = "".join(f'<li><a href="/{i}.htm">{text}</a></li>'
                    for i, text in enumerate(["学院概况", "招生信息", "联系我们"]))
    html = f'<html><body><nav><ul>{noise}</ul></nav><main><ul>{cards}</ul></main></body></html>'

    found = FacultyDirectoryExtractor.extract(html, "https://faculty.example.edu/roster")

    assert {person.name for person in found} == set(names)

