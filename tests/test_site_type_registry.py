import sqlite3

from server.site_type_registry import SiteTypeRegistry


def test_type_observation_is_idempotent_and_keeps_unaccepted_site_separate(tmp_path):
    evidence = tmp_path / "browser.html"
    evidence.write_text('<article><img src="/cv.png"><p>contact</p></article>', encoding="utf-8")
    registry = SiteTypeRegistry(tmp_path / "types.sqlite3")
    data = dict(school="样本大学", college="样本学院", entry_url="https://example.edu/",
                branch_url="https://example.edu/roster", shape_code="inline_image_bio_dom_email",
                features={"image_count": 1}, evidence_path=evidence)
    assert registry.record(**data) == registry.record(**data)
    with sqlite3.connect(registry.db_path) as con:
        assert con.execute("SELECT count(*), status FROM site_type_observations").fetchone() == (1, "observed")


def test_type_observation_changes_with_browser_evidence(tmp_path):
    evidence = tmp_path / "browser.html"
    registry = SiteTypeRegistry(tmp_path / "types.sqlite3")
    data = dict(school="样本大学", college="样本学院", entry_url="https://example.edu/",
                branch_url="https://example.edu/roster", shape_code="inline_image_bio_dom_email",
                features={}, evidence_path=evidence)
    evidence.write_text("first", encoding="utf-8")
    first = registry.record(**data)
    evidence.write_text("second", encoding="utf-8")
    assert registry.record(**data) != first
