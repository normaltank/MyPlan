import pytest
import json


from server import agent_recon
from pagination_contract_fixture import termination


PLAN = {"school": "样本大学", "college": "样本学院", "source_kind": "university_faculty_roster",
        "branches": [{"url": "https://example.edu/list", "card_selector": "main ul > li", "single_page": True}]}


def verification(selector="main ul > li", url="https://example.edu/list", count=37):
    return [{"type": "tool/call", "data": {"callId": "v", "name": "mcp__playwright__browser_verify",
             "arguments": json.dumps({"card_selector": selector})}},
            {"type": "tool/result", "data": {"message": {"content": [{"type": "tool-result", "toolCallId": "v",
             "isError": False, "content": [{"type": "text", "text": json.dumps({"url": url, "cardCount": count,
              "pagination": {"total": 0}, "pagination_termination": termination(url, selector)})}]}]}}}]


def test_guessed_or_empty_selector_evidence_is_rejected():
    for events, condition in (([], "url_never_verified"),
                              (verification(selector="aside li"), "selector_differs_from_verified"),
                              (verification(count=0), "card_count_zero"),
                              (verification(url="https://example.edu/other"), "url_never_verified")):
        [issue] = agent_recon.selector_evidence_issues(PLAN, events)
        assert {key: issue[key] for key in ("status", "url", "selector", "failed_condition")} == {
            "status": "selector_not_verified", "url": "https://example.edu/list", "selector": "main ul > li",
            "failed_condition": condition}
        assert issue["need"] and "page_only" not in issue
    assert agent_recon.selector_evidence_issues(PLAN, verification()) == []


def test_pagination_selector_requires_unique_observed_control():
    plan = {**PLAN, "branches": [{"url": "https://example.edu/list", "card_selector": "main ul > li",
                                  "next_selector": "a.next", "max_pages": 3}]}
    events = verification()
    assert agent_recon.selector_evidence_issues(plan, events)
    events[0]["data"]["arguments"] = json.dumps({"card_selector": "main ul > li", "next_selector": "a.next"})
    result = {"url": "https://example.edu/list", "cardCount": 37, "nextCount": 1,
              "nextUrl": "https://example.edu/list/2"}
    events[1]["data"]["message"]["content"][0]["content"][0]["text"] = json.dumps(result)
    assert agent_recon.selector_evidence_issues(plan, events)[0]["status"] == "pagination_not_verified"
    more = verification(url="https://example.edu/list/2")
    more[0]["data"]["callId"] = "v2"
    more[1]["data"]["message"]["content"][0]["toolCallId"] = "v2"
    events.extend(more)
    assert agent_recon.selector_evidence_issues(plan, events) == []


def test_single_page_claim_is_rejected_when_browser_reports_pagination_controls():
    events = verification()
    block = events[1]["data"]["message"]["content"][0]["content"][0]
    result = json.loads(block["text"])
    result["pagination"]["total"] = 1
    result["pagination_termination"]["confirmed"] = False
    block["text"] = json.dumps(result)
    assert agent_recon.selector_evidence_issues(PLAN, events)[0]["status"] == "single_page_pagination_unverified"


def test_candidate_zero_without_terminal_observation_cannot_prove_single_page():
    events = verification()
    block = events[1]['data']['message']['content'][0]['content'][0]
    result = json.loads(block['text'])
    del result['pagination_termination']
    block['text'] = json.dumps(result)
    assert agent_recon.selector_evidence_issues(PLAN, events)[0]['status'] == 'single_page_pagination_unverified'


def test_discovered_urls_require_successful_browser_observation():
    events = verification()
    events[0]["data"]["name"] = "mcp__playwright__browser_navigation"
    assert agent_recon.observed_browser_urls(events) == {"https://example.edu/list"}
    events[1]["data"]["message"]["content"][0]["isError"] = True
    assert agent_recon.observed_browser_urls(events) == set()


def test_redirect_requires_successful_real_navigation_evidence():
    original = "https://example.edu/list"
    redirected = original + "/"
    events = verification(url=redirected)
    navigation = [{"type": "tool/call", "data": {"callId": "nav", "name": "mcp__playwright__browser_navigate",
                   "arguments": json.dumps({"url": original})}},
                  {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": "nav",
                   "content": [{"type": "text", "text": json.dumps({"url": redirected})}]}]}}}]
    assert agent_recon.selector_evidence_issues(PLAN, navigation + events) == []
    navigation[1]["data"]["message"]["content"][0]["isError"] = True
    assert agent_recon.selector_evidence_issues(PLAN, navigation + events)


def test_omitted_profile_selector_is_restored_only_from_complete_browser_proof():
    branch = dict(PLAN["branches"][0])
    roster = verification()
    roster_result = roster[1]["data"]["message"]["content"][0]["content"][0]
    value = json.loads(roster_result["text"])
    samples = ["https://example.edu/p/1", "https://example.edu/p/2"]
    value["profileSamples"] = samples
    roster_result["text"] = json.dumps(value)
    events = list(roster)
    for index, url in enumerate(samples):
        call_id = f"profile-{index}"
        args = {"profile_content_selector": "article.profile"}
        result = {"url": url, **args, "profile_content_start_selector": None,
                  "confirmed": True, "container_visible": True, "container_count": 1,
                  "dom_stable": True, "network_idle": True,
                  "html_sha256": "a" * 64, "selected_html_sha256": "b" * 64}
        events.extend([
            {"type": "tool/call", "data": {"callId": call_id,
             "name": "mcp__playwright__browser_verify_profile", "arguments": json.dumps(args)}},
            {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": call_id,
             "content": [{"type": "text", "text": json.dumps(result)}]}]}}},
        ])
    restored = agent_recon.restore_verified_profile_selector(branch, PLAN, events)
    assert restored["profile_content_selector"] == "article.profile"
    assert agent_recon.selector_evidence_issues({**PLAN, "branches": [restored]}, events) == []

    # One verified page cannot justify a selector for both sampled profiles.
    incomplete = events[:-2]
    assert agent_recon.restore_verified_profile_selector(branch, PLAN, incomplete) == branch

# Source verification is a separately protected integration boundary.
pytestmark = pytest.mark.verified_source_boundary
