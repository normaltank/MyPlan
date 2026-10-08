"""Roster name chains preserve every independent item across DOM spacing."""
from server.name_list import is_name_list_page, reconcile_name_list


def test_multiple_complete_names_in_one_whitespace_item_are_not_truncated():
    html = "<p id='staff'><span>教师：</span><span>张伟 李明、王</span><span> 奔、赵汉杰</span></p>"
    assert is_name_list_page(html, "#staff")
    result = reconcile_name_list(html, "https://fixture.example.edu/staff", "#staff")
    assert {c.name for c in result.found} == {"张伟", "李明", "王奔", "赵汉杰"}
    assert result.unresolved == []


def test_unrecognized_whitespace_item_remains_an_explicit_review_issue():
    html = "<p id='staff'>教师：张伟 李明 TBD、王奔</p>"
    result = reconcile_name_list(html, "https://fixture.example.edu/staff", "#staff")
    assert {c.name for c in result.found} == {"张伟", "李明", "王奔"}
    assert [issue["item"] for issue in result.unresolved] == ["TBD"]


def test_role_lines_are_not_lists_and_title_annotations_are_not_members():
    html = ("<main><p>党政办公室主任：陈刚</p><p>教研室主任：赵强</p>"
            "<p>教师：张伟 教授、李明 博士、王奔</p></main>")
    result = reconcile_name_list(html, "https://fixture.example.edu/staff", "main > p")
    assert {c.name for c in result.found} == {"张伟", "李明", "王奔"}
    assert result.observation["list_cards"] == 1
    assert result.unresolved == []
    # Role-holders are not list members, and are not silently dropped either.
    assert [(c.name, c.name_review_reason) for c in result.review] == [
        ("陈刚", "name_list_role_line"), ("赵强", "name_list_role_line")]


def test_four_separate_han_characters_are_ambiguous_and_not_invented_one_person():
    html = "<p id='staff'>教师：张 三 李 四、王奔、李明</p>"
    result = reconcile_name_list(html, "https://fixture.example.edu/staff", "#staff")
    assert {c.name for c in result.found} == {"王奔", "李明"}
    assert [issue["item"] for issue in result.unresolved] == ["张 三 李 四"]


def test_double_spaces_and_nbsp_separate_complete_names_but_preserve_padding():
    html = "<p id='staff'>教师：张伟\u00a0\u00a0李明  王\u00a0\u00a0奔、赵汉杰</p>"
    assert is_name_list_page(html, "#staff")
    result = reconcile_name_list(html, "https://fixture.example.edu/staff", "#staff")
    assert {c.name for c in result.found} == {"张伟", "李明", "王奔", "赵汉杰"}
    assert result.unresolved == []
