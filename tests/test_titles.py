"""One title vocabulary for the whole project, including clinical and engineering titles."""
import pytest

from server import detail_mecc, faculty_extractor, segmented_roster, titles
from server.faculty_extractor import FacultyDirectoryExtractor
from server.titles import resolve_title


def test_every_module_shares_the_one_vocabulary():
    assert faculty_extractor.TITLE_KEYWORDS is titles.TITLE_KEYWORDS
    assert segmented_roster.TITLE_KEYWORDS is titles.TITLE_KEYWORDS
    assert detail_mecc.resolve_title is titles.resolve_title


@pytest.mark.parametrize("header, expected", [
    ("张三，主任医师，博士生导师", "主任医师"),
    ("李四 副主任医师 硕士生导师", "副主任医师"),
    ("王五，主治医师，讲师", "主治医师"),
    ("赵六，正高级工程师", "正高级工程师"),
    ("钱七，教授级高级工程师，硕士生导师", "教授级高级工程师"),
    ("孙八，高级工程师", "高级工程师"),
    ("周九，主任护师", "主任护师"),
    ("吴十，高级讲师", "高级讲师"),
    # Existing behaviour: earliest title wins, longer match at the same position.
    ("郑一，教授，主任医师", "教授"),
    ("陈二，副教授", "副教授"),
    ("Associate Professor of Physics", "副教授"),
    ("研究方向：数据库", "教师"),
])
def test_resolve_title(header, expected):
    assert resolve_title(header) == expected


@pytest.mark.parametrize("raw, expected", [
    ("李明主任医师", ("李明", "主任医师")),
    ("王芳（副主任医师）", ("王芳", "副主任医师")),
    ("张伟 高级工程师", ("张伟", "高级工程师")),
    ("刘洋（教授）", ("刘洋", "教授")),
])
def test_clean_name_strips_new_titles(raw, expected):
    assert FacultyDirectoryExtractor._clean_name(raw) == expected
