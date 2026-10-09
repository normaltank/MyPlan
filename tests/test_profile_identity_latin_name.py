"""The profile identity check compares the roster name and the page without whitespace.

集美大学海洋文化与法律学院: 'KHASKHELI MUHAMMAD BILAWAL' opens his own page, but the
page text was compared with its spaces removed while the name kept them, so a Latin
name could never be found and the college was blocked by profile_identity_not_found.
"""
from server.browser_batch_tool import page_names_person

PAGE = ('<div class="v_news_content"><p>KHASKHELI MUHAMMAD BILAWAL，男，巴基斯坦籍，法学博士。'
        '集美大学海洋文化与法律学院副教授。</p></div>')


def test_latin_name_with_spaces_is_found_on_its_page():
    assert page_names_person("KHASKHELI MUHAMMAD BILAWAL", PAGE)


def test_name_split_by_markup_or_no_break_space_is_found():
    assert page_names_person("林静", "<h1>林 <span>静</span></h1>")


def test_image_biography_text_still_counts():
    assert page_names_person("王芳", "<div><img src='bio.png'></div>", "王 芳，女，教授")


def test_another_persons_page_is_still_not_this_person():
    assert not page_names_person("赵红霞", "<table><tr><td>系统提示 您无权访问此页面</td></tr></table>")
    assert not page_names_person("KHASKHELI MUHAMMAD BILAWAL", "<p>MUHAMMAD ALI，副教授</p>")
