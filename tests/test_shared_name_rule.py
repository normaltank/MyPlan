"""One person-name rule for every roster shape: 、-list pages and segmented article rosters."""
import pytest

from server import name_list, pdf_roster, segmented_roster
from server.name_list import _person_name, _single_name
from server.segmented_roster import line_name

NAMES = ["张伟", "欧阳晓明", "李四", "热依拉·买买提", "迪丽热巴•迪力木拉提",
         "买买提·艾", "艾·买买提"]
NOT_NAMES = ["王", "教授", "张伟李明王奔赵", "热依拉·", "·买买提", "TBD"]


def test_segmented_roster_has_no_copy_of_its_own():
    assert segmented_roster._name_like is name_list._person_name
    assert pdf_roster._name_like is name_list._person_name
    assert segmented_roster.NAME_PRIOR_FLOOR is name_list.NAME_PRIOR_FLOOR
    assert not hasattr(segmented_roster, "_MINORITY_NAME")


@pytest.mark.parametrize("text", NAMES + NOT_NAMES)
def test_list_cell_and_record_heading_read_a_name_the_same_way(text):
    expected = text if text in NAMES else None
    assert _person_name(text) == expected
    assert _single_name(text) == expected
    assert line_name(text) == expected
    assert line_name(f"姓名：{text}") == expected


def test_padded_two_character_name_and_trailing_stop():
    assert _single_name("王 奔") == "王奔"
    assert line_name("姓名：王奔") == "王奔"
    assert _person_name("李四。") == _person_name("李四.") == "李四"
    assert line_name("李四。") == "李四"


def test_independent_full_names_are_never_joined_into_one():
    assert _person_name("张伟 李明") is None


def test_dotted_names_on_a_list_page_and_a_segmented_page_agree():
    html = "<p id='staff'>教师：热依拉·买买提、艾·买买提、张伟</p>"
    found = {c.name for c in name_list.reconcile_name_list(
        html, "https://fixture.example.edu/staff", "#staff").found}
    assert found == {"热依拉·买买提", "艾·买买提", "张伟"}
    assert {line_name(f"{name} 教授") for name in found} == found
