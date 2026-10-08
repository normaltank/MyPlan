"""Address reading has one implementation; every caller of faculty_extractor gets it."""
import server.detail_mecc as detail_mecc
import server.faculty_extractor as faculty_extractor


def test_faculty_extractor_reexports_the_detail_mecc_email_tools():
    assert faculty_extractor.find_emails is detail_mecc.find_emails
    assert faculty_extractor.is_personal_email_for_scholar is detail_mecc.is_personal_email_for_scholar
    assert faculty_extractor.is_institutional_email is detail_mecc.is_institutional_email


def test_unused_free_mail_lists_are_gone():
    assert not hasattr(faculty_extractor, "FREE_MAIL_DOMAINS")
    assert not hasattr(detail_mecc, "FREE_MAIL_DOMAINS")
