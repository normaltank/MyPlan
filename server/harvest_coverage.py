"""A local coverage ledger; counts do not imply undiscovered sources are covered."""
import hashlib
from collections import Counter

TERMINAL = {"single_page_no_pagination_control_seen", "next_control_absent", "next_control_disabled"}


def build_coverage_ledger(plan, capture, observations, candidates, profiles, eligibility, snapshots):
    issues = []
    completed = {branch["url"]: branch for branch in capture.branches}
    branches = []
    reviewed = {item["source_url"] for item in plan.get("poster_reviews", [])
                if item.get("reviewed_full_image") is True}
    for branch in plan["branches"]:
        evidence = completed.get(branch["url"])
        if evidence is None:
            issues.append({"url": branch["url"], "status": "planned_branch_not_captured"})
            continue
        children = evidence.get("child_visits", [])
        end_proven = (all(child.get("end") in TERMINAL for child in children) and bool(children)
                      if branch.get("kind") == "navigation" else evidence.get("end") in TERMINAL)
        empty_declared = "empty_state_selector" in branch or "roster_container_selector" in branch
        if empty_declared:
            # An ordinary pagination terminal cannot stand in for empty proof.
            end_proven = False
        if evidence.get("end") == "confirmed_empty_roster":
            from server.empty_roster import empty_roster_proof_valid
            pages_for_branch = [page for page in capture.roster_pages if page.branch.get("url") == branch["url"]]
            from bs4 import BeautifulSoup
            end_proven = (len(pages_for_branch) == 1 and not any(
                row.get("url") == pages_for_branch[0].url and row.get("extracted_candidates", 0) != 0
                for row in observations))
            if end_proven:
                page = pages_for_branch[0]
                end_proven = (empty_roster_proof_valid(evidence.get("empty_roster"), branch, page.html, url=page.url)
                              and empty_roster_proof_valid(page.empty_roster_evidence, branch, page.html, url=page.url)
                              and not BeautifulSoup(page.html, "html.parser").select(branch["card_selector"]))
        poster_covered = branch["url"] in reviewed and not empty_declared
        if not end_proven and not poster_covered:
            issues.append({"url": branch["url"], "status": "branch_termination_unverified"})
        branches.append({**evidence, "termination_verified": end_proven or poster_covered})
    pages = [{"url": page.url, "branch_url": page.branch["url"], "sequence": index,
              "state_id": hashlib.sha256((page.url + "\n" + page.html).encode()).hexdigest()}
             for index, page in enumerate(capture.roster_pages, 1)]
    return {"scope": "verified_roster_branches", "complete": not issues, "issues": issues,
            "planned_branches": len(plan["branches"]), "captured_branches": len(branches),
            "branches": branches, "pages": pages, "roster_observations": observations,
            "candidate_count": len(candidates), "eligibility_counts": dict(Counter(row["status"] for row in eligibility)),
            "profiles": [{"url": url, "status": "captured" if html else "unresolved"} for url, html in profiles.items()],
            "snapshots": snapshots, "discovery_completeness": "requires_independent_navigation_evidence"}
