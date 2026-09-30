"""A PDF viewer is a source needing local reconciliation, not one faculty card."""

from server.harvest_workflow import CapturedRosterPage, reconcile_rosters
from server.recovery_policy import classify_issues


def test_pdf_viewer_requires_source_review_instead_of_repeated_selector_repair():
    url = 'https://college.example.edu/mentors.htm'
    html = ('<nav><ul><li><a href="/about">学院概况</a></li>'
            '<li><a href="/faculty">师资队伍</a></li></ul></nav>'
            '<div id="content"><iframe src="/pdfjs/viewer.html?file=%2Ffiles%2Fmentors.pdf"></iframe></div>')
    result = reconcile_rosters([CapturedRosterPage(url, html, {'card_selector': '#content'})], {})
    assert result.candidates == []
    assert [item['status'] for item in result.unresolved] == ['embedded_pdf_roster_requires_review']
    issue = result.unresolved[0]
    assert issue['media_urls'] == ['https://college.example.edu/files/mentors.pdf']
    recovery = classify_issues(result.unresolved)
    assert recovery.review == result.unresolved
    assert recovery.structure == []


def test_linked_pdf_attachment_does_not_turn_a_regular_teacher_card_into_a_pdf_roster():
    url = 'https://college.example.edu/faculty.htm'
    html = ('<ul id="cards"><li><a href="/person/1">张三</a>'
            '<a href="/files/material.pdf">Attachment</a></li>'
            '<li><a href="/person/2">李四</a></li></ul>')
    result = reconcile_rosters([CapturedRosterPage(url, html, {'card_selector': '#cards > li'})], {})
    assert len(result.candidates) == 2
    assert not any(item['status'] == 'embedded_pdf_roster_requires_review' for item in result.unresolved)
