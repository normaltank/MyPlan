"""A card whose only link opens its photo file has no profile page.

Real case: 平顶山学院马拉加工程学院 师资队伍 (captured 2026-10-01) wraps each teacher's
photo in '<a href="../images/x.jpg">' and has no personal pages. The photo URL was
treated as a profile, so every person failed profile identity and the portrait went
through image-biography OCR.
"""
from server.faculty_extractor import FacultyDirectoryExtractor

URL = 'https://fixture.example.edu/szdw/dqgc.htm'


def card(photo, caption, href=None):
    return (f'<td align="center"><a href="{href or "../images/" + photo}" target="_blank">'
            f'<img src="../images/{photo}" alt="{caption}"></a><p>{caption}</p></td>')


def test_photo_file_link_is_not_a_profile():
    html = ('<div class="content-main"><table><tbody><tr>'
            + card('wangfang.jpg', '王芳 电气工程博士 教授')
            + card('liming.JPG', '李明 电气工程博士 副教授')
            + card('zhangwei.png', '张伟 工学硕士 讲师')
            + card('chenjing.jpg', '陈静 工学博士 讲师', href='../info/1001/2001.htm')
            + '</tr></tbody></table></div>')
    found, review = FacultyDirectoryExtractor.extract_with_review(
        html, URL, card_selector='div.content-main td:has(> a > img)')
    assert not review
    assert [(person.name, person.url) for person in found] == [
        ('王芳', URL), ('李明', URL), ('张伟', URL),
        ('陈静', 'https://fixture.example.edu/info/1001/2001.htm')]
