"""Tests for the site-independent harvest workflow seams."""

import pytest

from server.harvest_workflow import (
    CapturedRosterPage,
    capture_sources,
    commit_records,
    reconcile_rosters,
)


@pytest.mark.asyncio
async def test_capture_sources_keeps_navigation_and_roster_roles_separate():
    plan = {
        "branches": [
            {
                "url": "https://example.edu/departments",
                "kind": "navigation",
                "child_branch": {"single_page": True},
            }
        ]
    }

    async def discover(_context, branch):
        return "<nav>departments</nav>", ["https://example.edu/math"], [], {
            "url": branch["url"], "kind": "navigation"
        }

    async def capture(_context, branch):
        return [(branch["url"], "<main>roster</main>")], [], {
            "url": branch["url"], "visited_pages": 1
        }

    result = await capture_sources(
        plan,
        object(),
        capture_branch=capture,
        discover_children=discover,
    )

    assert result.navigation_pages == [("https://example.edu/departments", "<nav>departments</nav>")]
    assert [page.url for page in result.roster_pages] == ["https://example.edu/math"]
    assert result.roster_pages[0].branch["url"] == "https://example.edu/math"
    assert result.unresolved == []


def test_reconcile_rosters_is_independent_of_domain_eligibility_policy():
    plan = {"source_kind": "university_faculty_roster"}
    captured = [
        CapturedRosterPage(
            url="https://example.edu/faculty",
            html=(
                "<html><body><main><ul class='cards'>"
                "<li><a href='/people/1'>李一</a><span>教授</span></li>"
                "</ul></main></body></html>"
            ),
            branch={"card_selector": ".cards > li"},
        )
    ]

    result = reconcile_rosters(captured, plan)

    assert [(item.name, item.url) for item in result.candidates] == [
        ("李一", "https://example.edu/people/1")
    ]
    assert result.candidate_sources[("李一", "https://example.edu/people/1")] == {
        "university_faculty_roster"
    }
    assert result.observations[0]["visible_cards"] == 1
    assert result.unresolved == []


def test_commit_records_delegates_without_site_knowledge():
    calls = []

    def exporter(records, destination):
        calls.append((records, destination))

    records = [{"姓名": "李一"}]
    destination = commit_records(records, "out.xlsx", exporter=exporter)

    assert destination == "out.xlsx"
    assert calls == [(records, "out.xlsx")]


def test_reviewed_exclusions_are_excluded_without_unresolved():
    from server.faculty_extractor import FacultyCandidate
    from server.harvest_eligibility import classify_candidates

    plan = {
        "school": "甘肃中医药大学",
        "college": "第二临床医学院",
        "reviewed_exclusions": [
            {
                "name": "许晓燕",
                "profile_url": "http://www.lzsdyyy.cn/html/doctors-show-138.html",
                "reason": "graduated_not_faculty",
            }
        ],
    }
    candidate = FacultyCandidate(name="许晓燕", url="http://www.lzsdyyy.cn/html/doctors-show-138.html")
    sources = {(candidate.name, candidate.url): {"hospital_doctor_roster"}}
    profiles = {candidate.url: "<html><body>毕业于甘肃中医药大学</body></html>"}

    admitted, decisions, unresolved = classify_candidates([candidate], sources, profiles, plan)

    assert admitted == []
    assert unresolved == []
    assert decisions[0]["status"] == "excluded"
    assert decisions[0]["reason"] == "graduated_not_faculty"

