"""An <img> without any source address is not a picture of the profile.

Real case: 武汉大学数学与统计学院 personal pages on jszy.whu.edu.cn (captured
2026-10-01) carry template placeholders such as <img src=""/>. The empty
address resolved to the profile page itself, which was downloaded as an
'image' and failed, blocking 68 of 104 profiles as image_bio_asset_failed.
"""
from server.image_biography import media_references

PAGE = "https://jszy.example.edu.cn/teacher/zh_CN/index.htm"


def test_image_without_a_source_address_is_not_a_media_reference():
    html = ('<div><img src=""/><img alt="" id="u_pic"/><img src="  ">'
            '<img src="/_resources/resume.png"></div>')
    refs = media_references(html, PAGE)
    assert [(ref.kind, ref.url) for ref in refs] == [
        ("image", "https://jszy.example.edu.cn/_resources/resume.png")]


def test_lazy_image_with_empty_src_keeps_its_data_src():
    refs = media_references('<img src="" data-src="/lazy/cv.jpg">', PAGE)
    assert [ref.url for ref in refs] == ["https://jszy.example.edu.cn/lazy/cv.jpg"]
