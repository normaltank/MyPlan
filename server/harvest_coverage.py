"""A local coverage ledger; counts do not imply undiscovered sources are covered."""
import hashlib
from collections import Counter

TERMINAL = {"single_page_no_pagination_control_seen", "next_control_absent", "next_control_disabled"}


def _termination_verified(branch, evidence, capture, observations):
    if branch.get('kind') == 'navigation':
        children = evidence.get('child_visits', [])
        if not children:
            return False
        if 'child_branches' in branch:
            if evidence.get('navigation_termination_verified') is not True:
                return False
            expected = {child['url']: child for child in branch['child_branches']}
            urls = [child.get('url') for child in children]
            if len(urls) != len(set(urls)) or set(urls) != set(expected):
                return False
            return all(_termination_verified(expected[child['url']], child, capture, observations)
                       for child in children)
        child_branch = branch.get('child_branch', {})
        return all(_termination_verified({**child_branch, 'url': child.get('url')}, child,
                                         capture, observations) for child in children)
    if 'empty_state_selector' in branch or 'roster_container_selector' in branch:
        if evidence.get('end') != 'confirmed_empty_roster':
            return False
        from server.empty_roster import empty_roster_proof_valid
        from bs4 import BeautifulSoup
        pages = [page for page in capture.roster_pages if page.branch.get('url') == branch['url']]
        if len(pages) != 1 or any(row.get('url') == pages[0].url and
                                 row.get('extracted_candidates', 0) != 0 for row in observations):
            return False
        page = pages[0]
        return (empty_roster_proof_valid(evidence.get('empty_roster'), branch, page.html, url=page.url)
                and empty_roster_proof_valid(page.empty_roster_evidence, branch, page.html, url=page.url)
                and not BeautifulSoup(page.html, 'html.parser').select(branch['card_selector']))
    return evidence.get('end') in TERMINAL


def build_coverage_ledger(plan, capture, observations, candidates, profiles, eligibility, snapshots):
    issues = []
    completed = {branch["url"]: branch for branch in capture.branches}
    branches = []
    for branch in plan["branches"]:
        evidence = completed.get(branch["url"])
        if evidence is None:
            issues.append({"url": branch["url"], "status": "planned_branch_not_captured"})
            continue
        end_proven = _termination_verified(branch, evidence, capture, observations)
        if not end_proven:
            issues.append({"url": branch["url"], "status": "branch_termination_unverified"})
        branches.append({**evidence, "termination_verified": end_proven})
    pages = [{"url": page.url, "branch_url": page.branch["url"], "sequence": index,
              "state_id": hashlib.sha256((page.url + "\n" + page.html).encode()).hexdigest()}
             for index, page in enumerate(capture.roster_pages, 1)]
    return {"scope": "verified_roster_branches", "complete": not issues, "issues": issues,
            "planned_branches": len(plan["branches"]), "captured_branches": len(branches),
            "branches": branches, "pages": pages, "roster_observations": observations,
            "candidate_count": len(candidates), "eligibility_counts": dict(Counter(row["status"] for row in eligibility)),
            "profiles": [{"url": url, "status": "captured" if html else "unresolved"} for url, html in profiles.items()],
            "snapshots": snapshots, "discovery_completeness": "requires_independent_navigation_evidence"}
