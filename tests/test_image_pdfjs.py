"""PDF viewer references must resolve only explicit DOM-observed resources."""

import pytest

from server.image_biography import media_references


def test_pdfjs_iframe_resolves_encoded_pdf_resource():
    html = '<iframe src="/system/pdfjs/viewer.html?file=%2Fassets%2Ffaculty.pdf"></iframe>'

    refs = media_references(html, "https://faculty.example.edu/info/article.htm")

    assert [(ref.kind, ref.url, ref.position, ref.linked) for ref in refs] == [
        ("pdf", "https://faculty.example.edu/assets/faculty.pdf", 1, False),
    ]


def test_pdfjs_file_path_spaces_are_escaped_after_query_decoding():
    html = '<iframe src="/pdfjs/viewer.html?file=%2Fassets%2Ffaculty%20cv.pdf"></iframe>'

    refs = media_references(html, "https://faculty.example.edu/article")

    assert [ref.url for ref in refs] == [
        "https://faculty.example.edu/assets/faculty%20cv.pdf",
    ]


def test_pdfjs_relative_resource_resolves_against_viewer_and_keeps_escapes():
    html = (
        '<iframe src="/tools/pdfjs/viewer.html?file=..%2Ffiles%2Fstaff%2520cv.pdf'
        '%3Fdownload%3D1%26token%3Da%252Bb&amp;zoom=page-width"></iframe>'
    )

    refs = media_references(html, "https://faculty.example.edu/info/article.htm")

    assert [ref.url for ref in refs] == [
        "https://faculty.example.edu/tools/files/staff%20cv.pdf?download=1&token=a%2Bb",
    ]


def test_pdfjs_external_resource_preserves_dom_order_and_deduplicates_direct_link():
    html = (
        '<img src="/photo.jpg">'
        '<iframe src="/pdfjs/viewer.html?file=https%3A%2F%2Fcdn.example.edu%2Fbio.pdf"></iframe>'
        '<a href="https://cdn.example.edu/bio.pdf">PDF</a>'
        '<object data="/other.pdf" type="application/pdf"></object>'
    )

    refs = media_references(html, "https://faculty.example.edu/p")

    assert [(ref.kind, ref.url, ref.position, ref.linked) for ref in refs] == [
        ("image", "https://faculty.example.edu/photo.jpg", 1, False),
        ("pdf", "https://cdn.example.edu/bio.pdf", 2, False),
        ("pdf", "https://faculty.example.edu/other.pdf", 3, False),
    ]


@pytest.mark.parametrize("source", [
    "/pdfjs/viewer.html",
    "/pdfjs/viewer.html?file=",
    "/pdfjs/viewer.html?file=%2Fassets%2Fphoto.jpg",
    "/pdfjs/viewer.html?file=javascript%3Abio.pdf",
    "/pdfjs/viewer.html?file=file%3A%2F%2F%2Fbio.pdf",
    "/pdfjs/viewer.html?file=data%3Aapplication%2Fpdf%3Bbase64%2Cfake.pdf",
    "/pdfjs/viewer.html?file=ftp%3A%2F%2Ffiles.example.edu%2Fbio.pdf",
    "javascript:viewer.html?file=https%3A%2F%2Ffiles.example.edu%2Fbio.pdf",
])
def test_pdfjs_missing_non_pdf_and_non_http_resources_are_ignored(source):
    assert media_references(f'<iframe src="{source}"></iframe>',
                            "https://faculty.example.edu/p") == []


def test_pdfjs_malformed_resource_url_is_ignored():
    html = '<iframe src="/pdfjs/viewer.html?file=https%3A%2F%2F%5Bbroken%2Fbio.pdf"></iframe>'

    assert media_references(html, "https://faculty.example.edu/p") == []
