"""Email-name affinity must accept every reading of a polyphonic character.

pypinyin's default (context) reading of many surnames is not the surname
reading: 曾 -> ceng, 单 -> dan, 解 -> jie, 仇 -> chou, 区 -> qu, 查 -> cha,
朴 -> pu, 翟 -> di, 覃 -> tan, 缪 -> mou, 长孙 -> zhang. Real mailboxes use the
surname reading, so matching on the default reading alone rejected them.
"""
import pytest

from server import detail_mecc, faculty_extractor
from server.faculty_extractor import FacultyCandidate, FacultyHarvestPipeline
from email_matching import DomainConventionModel, pinyin_readings

SURNAME_READINGS = [
    ("zengguofan", "曾国藩"),
    ("shanxiongxin", "单雄信"),
    ("xiejin", "解缙"),
    ("qiuying", "仇英"),
    ("ouyong", "区永"),
    ("zhaliangyong", "查良镛"),
    ("piaoshu", "朴树"),
    ("zhaimin", "翟敏"),
    ("qinda", "覃达"),
    ("miaohua", "缪华"),
    ("changsunming", "长孙明"),
]


@pytest.mark.parametrize("local, name", SURNAME_READINGS)
def test_surname_reading_matches_mailbox(local, name):
    assert DomainConventionModel()._pinyin_match(local, name) is True


@pytest.mark.parametrize("local, name", SURNAME_READINGS)
@pytest.mark.parametrize("module", [faculty_extractor, detail_mecc])
def test_personal_email_accepts_surname_reading(module, local, name):
    assert module.is_personal_email_for_scholar(f"{local}@univ.edu.cn", name) is True


@pytest.mark.parametrize("local", ["lvbin", "lubin", "lyubin"])
def test_u_umlaut_spellings_all_match(local):
    assert DomainConventionModel()._pinyin_match(local, "吕斌") is True


def test_context_reading_stays_first():
    assert pinyin_readings("曾国藩")[0] == ["ceng", "guo", "fan"]
    assert ["zeng", "guo", "fan"] in pinyin_readings("曾国藩")


@pytest.mark.parametrize("module", [faculty_extractor, detail_mecc])
def test_other_person_mailbox_is_still_rejected(module):
    assert module.is_personal_email_for_scholar("zhangwei@univ.edu.cn", "曾国藩") is False
    assert module.is_personal_email_for_scholar("dengfan@univ.edu.cn", "曾凡") is False


def test_custom_to_pinyin_is_still_honoured():
    model = DomainConventionModel(to_pinyin=lambda name: ["x", "y"])
    assert model._pinyin_match("xy", "曾国") is True
    assert model._pinyin_match("zengguo", "曾国") is False
    assert DomainConventionModel(to_pinyin=lambda name: None)._pinyin_match("zeng", "曾国") is None


def test_surname_only_mailbox_is_ambiguous_under_surname_reading():
    """Two peers named 曾…: 'zeng@' is the surname alone, whichever reading it uses."""
    shared_url = "https://cs.univ.edu.cn/team.htm"
    html = """
    <html><body><div class="team">
      <div class="member-card"><h3>曾明 教授</h3><p>研究方向：数据库</p></div>
      <div class="member-card"><h3>曾亮 副教授</h3><p>研究方向：算法</p>
        <p>邮箱：zeng@univ.edu.cn</p></div>
    </div></body></html>
    """
    result = FacultyHarvestPipeline().harvest_candidates(
        candidates=[FacultyCandidate(name="曾明", url=shared_url, title="教授"),
                    FacultyCandidate(name="曾亮", url=shared_url, title="副教授")],
        profile_fetcher=lambda url: html,
        base_url="https://cs.univ.edu.cn/roster.htm",
        school="大学",
        college="学院",
    )
    assert any(u.get("status") == "ambiguous_surname_email" for u in result["unresolved"])
    assert result["delivery_ready"] is False
