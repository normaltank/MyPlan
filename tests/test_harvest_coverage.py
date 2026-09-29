from types import SimpleNamespace
from server.harvest_coverage import build_coverage_ledger


def test_missing_whole_planned_branch_blocks_coverage_even_with_equal_sheet_counts():
    plan = {"school": "样本大学", "college": "样本学院", "branches": [
        {"url": "https://example.edu/a"}, {"url": "https://example.edu/b"}]}
    capture = SimpleNamespace(branches=[{"url": "https://example.edu/a", "visited_pages": 1,
                                        "end": "single_page_no_pagination_control_seen"}], roster_pages=[], navigation_pages=[])
    ledger = build_coverage_ledger(plan, capture, [], [], {}, [], [])
    assert ledger["scope"] == "verified_roster_branches"
    assert not ledger["complete"]
    assert ledger["issues"] == [{"url": "https://example.edu/b", "status": "planned_branch_not_captured"}]


def test_same_url_ajax_pages_get_distinct_content_identities():
    plan = {"school": "样本大学", "college": "样本学院", "branches": [{"url": "https://example.edu/a"}]}
    pages = [SimpleNamespace(url="https://example.edu/a", html=html, branch=plan["branches"][0])
             for html in ("<li>第一页</li>", "<li>第二页</li>")]
    capture = SimpleNamespace(branches=[{"url": "https://example.edu/a", "visited_pages": 2,
                                        "end": "next_control_absent"}], roster_pages=pages, navigation_pages=[])
    ledger = build_coverage_ledger(plan, capture, [], [], {}, [], [])
    assert ledger["complete"]
    assert len({page["state_id"] for page in ledger["pages"]}) == 2
