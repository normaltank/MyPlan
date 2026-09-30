"""The fallback profile path honors the same CSS boundaries as the MECC path."""
from server import faculty_extractor as faculty


def test_fallback_profile_preserves_inline_elements_with_block_layout(monkeypatch):
    monkeypatch.setattr(faculty, 'DETAIL_MECC_AVAILABLE', False)
    html = ('<article><h1>张富有</h1><span style="display: block">邮箱：1234567890@example.edu</span>'
            '<span style="display: block">电话：123456789</span>'
            '<span style="display: block">完整教育与任职经历。</span></article>')
    result = faculty.FacultyProfileExtractor.extract(html, 'https://faculty.example.edu/p/1', '张富有')
    assert result.full_bio == '张富有\n邮箱：1234567890@example.edu\n电话：123456789\n完整教育与任职经历。'
    assert result.email == '1234567890@example.edu'


def test_fallback_email_uses_field_boundaries_while_bio_keeps_inline_text(monkeypatch):
    monkeypatch.setattr(faculty, 'DETAIL_MECC_AVAILABLE', False)
    html = ('<article><h1>张富有</h1><p><span>Email:1234567890@example.coM</span>'
            '<span>Tel:12345</span></p><p>完整教育经历。</p></article>')
    result = faculty.FacultyProfileExtractor.extract(html, 'https://faculty.example.edu/p/1', '张富有')
    assert result.email == '1234567890@example.coM'
    assert result.emails == ('1234567890@example.coM',)
    assert result.full_bio == '张富有\nEmail:1234567890@example.coMTel:12345\n完整教育经历。'
