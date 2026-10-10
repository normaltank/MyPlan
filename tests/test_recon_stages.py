import json
import pytest
from types import SimpleNamespace

from server import agent_recon
from server.recon_browser_mcp import phase_tools


def test_stages_keep_branch_contexts_separate_and_count_every_model_request(monkeypatch):
    calls = []
    target = {"school": "样本大学", "college": "样本学院", "url": "https://example.edu/"}
    urls = ["https://example.edu/teachers", "https://example.edu/advisors"]

    def execute(prompt, sid, cfg, patches):
        calls.append((sid, cfg["phase"], prompt))
        events = [{"type": "assistant/message", "data": {"usage": {
            "inputTokens": 100, "cacheReadTokens": 200, "outputTokens": 30}}}]
        if cfg["phase"] == "discovery":
            value = {**target, "source_kind": "university_faculty_roster", "branches": [{"url": url} for url in urls]}
            value.pop("url")
            events.extend([
                {"type": "tool/call", "data": {"callId": sid, "name": "mcp__playwright__browser_navigation"}},
                {"type": "tool/result", "data": {"message": {"content": [{"type": "tool-result", "toolCallId": sid,
                    "content": [{"type": "text", "text": json.dumps({"items": [{"url": url} for url in urls]})}]}]}}}])
        else:
            url = urls[len(calls) - 2]
            value = {"url": url, "card_selector": "main ul > li", "single_page": True}
            events.extend([
                {"type": "tool/call", "data": {"callId": sid, "name": "mcp__playwright__browser_verify",
                    "arguments": json.dumps({"card_selector": value["card_selector"]})}},
                {"type": "tool/result", "data": {"message": {"content": [{"type": "tool-result", "toolCallId": sid,
                    "content": [{"type": "text", "text": json.dumps({"url": url, "cardCount": 37,
                     "pagination": {"total": 0}})}]}]}}}])
        return SimpleNamespace(final_response=json.dumps(value), finish_reason="completed", events=events)

    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    result = agent_recon._run_staged_harness("case", {"target": target, "timeout_seconds": 60}, ())
    assert [phase for _, phase, _ in calls] == ["discovery", "branch", "branch"]
    assert len({sid for sid, _, _ in calls}) == 3
    assert urls[1] not in calls[1][2] and urls[0] not in calls[2][2]
    assert len(json.loads(result.final_response)["branches"]) == 2
    assert agent_recon.run_metrics(result.events)["token_usage"] == {
        "uncached_input": 300, "cached_input": 600, "output": 90}


def test_failed_branch_retry_reuses_discovery_without_another_model_call(monkeypatch):
    target = {"school": "样本大学", "college": "样本学院", "url": "https://example.edu/"}
    url = "https://example.edu/teachers"
    calls = []

    def pair(name, args, value, call_id):
        return [{"type": "tool/call", "data": {"callId": call_id,
                 "name": "mcp__playwright__" + name, "arguments": json.dumps(args)}},
                {"type": "tool/result", "data": {"message": {"content": [
                    {"toolCallId": call_id, "content": [{"type": "text", "text": json.dumps(value)}]}]}}}]

    def execute(prompt, sid, cfg, patches):
        calls.append(cfg["phase"])
        if cfg["phase"] == "discovery":
            value = {"school": target["school"], "college": target["college"],
                     "source_kind": "university_faculty_roster", "branches": [{"url": url}]}
            events = pair("browser_navigation", {}, {"items": [{"url": url}]}, "d")
        else:
            value = {"url": url, "card_selector": "main li", "single_page": True}
            events = (pair("browser_verify", {"card_selector": "main li"},
                           {"url": url, "cardCount": 3, "pagination": {"total": 0}}, "v")
                      if calls.count("branch") == 2 else [])
        return SimpleNamespace(final_response=json.dumps(value), finish_reason="completed", events=events)

    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness("first", {"target": target, "timeout_seconds": 60}, ())
    checkpoint = caught.value.discovery_checkpoint
    result = agent_recon._run_staged_harness("second", {
        "target": target, "timeout_seconds": 60,
        "feedback": caught.value.safe_feedback, "resume_discovery": checkpoint}, ())
    assert calls == ["discovery", "branch", "branch"]
    assert result.phase_metrics[0]["reused"] is True
    assert result.discovery_session_id == "first-discover"
    assert json.loads(result.final_response)["branches"][0]["url"] == url


def test_later_branch_failure_reuses_earlier_verified_branches_and_scopes_feedback(monkeypatch):
    target = {"school": "样本大学", "college": "样本学院", "url": "https://example.edu/"}
    urls = [f"https://example.edu/roster-{i}" for i in range(3)]
    calls = []

    def pair(name, args, value, call_id):
        return [{"type": "tool/call", "data": {"callId": call_id,
                 "name": "mcp__playwright__" + name, "arguments": json.dumps(args)}},
                {"type": "tool/result", "data": {"message": {"content": [
                    {"toolCallId": call_id, "content": [{"type": "text", "text": json.dumps(value)}]}]}}}]

    def execute(prompt, sid, cfg, patches):
        calls.append((cfg["phase"], sid, prompt))
        if cfg["phase"] == "discovery":
            value = {"school": target["school"], "college": target["college"],
                     "source_kind": "university_faculty_roster",
                     "branches": [{"url": url} for url in urls]}
            events = pair("browser_navigation", {}, {"items": [{"url": u} for u in urls]}, sid)
        else:
            url = next(u for u in urls if u in prompt)
            value = {"url": url, "card_selector": "main li", "single_page": True}
            events = (pair("browser_verify", {"card_selector": "main li"},
                           {"url": url, "cardCount": 3, "pagination": {"total": 0}}, sid)
                      if url != urls[2] or sid.startswith("retry-") else [])
        return SimpleNamespace(final_response=json.dumps(value), finish_reason="completed", events=events)

    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness("first", {"target": target, "timeout_seconds": 60}, ())
    assert set(caught.value.verified_branches) == set(urls[:2])
    result = agent_recon._run_staged_harness("retry", {
        "target": target, "timeout_seconds": 60,
        "feedback": caught.value.safe_feedback,
        "resume_discovery": caught.value.discovery_checkpoint,
        "verified_branches": caught.value.verified_branches}, ())
    assert [phase for phase, _, _ in calls] == ["discovery", "branch", "branch", "branch", "branch"]
    assert calls[-1][1] == "retry-branch-3"
    assert urls[0] not in calls[-1][2] and urls[1] not in calls[-1][2]
    assert len(json.loads(result.final_response)["branches"]) == 3


def test_navigation_branch_requires_child_roster_browser_proof():
    parent = "https://example.edu/advisors"
    child = "https://example.edu/advisors/list"
    branch = {"url": parent, "kind": "navigation", "child_link_selector": "main a.roster",
              "child_branch": {"card_selector": "article p > img", "single_page": True}}
    plan = {"school": "样本大学", "college": "样本学院",
            "source_kind": "university_faculty_roster", "branches": [branch]}

    def pair(args, result, call_id):
        return [{"type": "tool/call", "data": {"callId": call_id,
                 "name": "mcp__playwright__browser_verify", "arguments": json.dumps(args)}},
                {"type": "tool/result", "data": {"message": {"content": [
                    {"toolCallId": call_id, "content": [{"type": "text", "text": json.dumps(result)}]}]}}}]

    parent_proof = pair({"card_selector": "main a.roster"},
                        {"url": parent, "cardCount": 1, "profileSamples": [child],
                         "pagination": {"total": 0}}, "parent")
    [issue] = agent_recon.selector_evidence_issues(plan, parent_proof)
    assert {key: issue[key] for key in ("status", "url", "selector", "failed_condition")} == {
        "status": "selector_not_verified", "url": child, "selector": "article p > img",
        "failed_condition": "url_never_verified"}
    child_proof = pair({"card_selector": "article p > img"},
                       {"url": child, "cardCount": 5, "profileSamples": [],
                        "pagination": {"total": 0}}, "child")
    assert agent_recon.selector_evidence_issues(plan, parent_proof + child_proof) == []


def test_discovery_and_branch_tool_surfaces_enforce_stage_boundary():
    discovery = {entry["name"] for entry in phase_tools("discovery")}
    branch = {entry["name"] for entry in phase_tools("branch")}
    assert "browser_verify" not in discovery and "browser_structure" not in discovery
    assert "browser_navigation" not in branch
    assert "browser_verify" in branch and "browser_navigate" in discovery
    assert "browser_verify_segments" in branch and "browser_verify_segments" not in discovery


@pytest.mark.parametrize("feedback", [
    {"known_urls": ["https://example.edu/teachers", "https://example.edu/advisors"]},
    {"previous_plan": {"branches": [{"url": "https://example.edu/teachers"},
                                     {"url": "https://example.edu/advisors"}]}}
])
def test_repair_cannot_discard_previously_verified_branch(monkeypatch, feedback):
    target = {"school": "样本大学", "college": "样本学院", "url": "https://example.edu/"}
    url = "https://example.edu/teachers"
    value = {"school": target["school"], "college": target["college"],
             "source_kind": "university_faculty_roster", "branches": [{"url": url}]}
    events = [{"type": "tool/call", "data": {"callId": "d", "name": "mcp__playwright__browser_navigation"}},
              {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": "d",
               "content": [{"type": "text", "text": json.dumps({"url": url})}]}]}}}]
    def execute(prompt, sid, cfg, patches):
        assert cfg["phase"] == "discovery", "omission must be rejected before branch verification"
        return SimpleNamespace(final_response=json.dumps(value), finish_reason="completed", events=events)
    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness("repair", {"target": target, "feedback": feedback,
                                                  "timeout_seconds": 60}, ())
    assert "https://example.edu/advisors" in caught.value.safe_feedback["known_urls"]


def test_stage_accepts_canonical_branch_url_only_after_real_redirect(monkeypatch):
    target = {"school": "样本大学", "college": "样本学院", "url": "https://example.edu/"}
    original, canonical = "http://example.edu/list", "https://example.edu/list/"
    def event_pair(name, args, result, call_id):
        return [{"type": "tool/call", "data": {"callId": call_id, "name": "mcp__playwright__" + name,
                  "arguments": json.dumps(args)}},
                {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": call_id,
                 "content": [{"type": "text", "text": json.dumps(result)}]}]}}}]
    def execute(prompt, sid, cfg, patches):
        if cfg["phase"] == "discovery":
            value = {"school": target["school"], "college": target["college"],
                     "source_kind": "university_faculty_roster", "branches": [{"url": original}]}
            events = event_pair("browser_navigation", {}, {"items": [{"url": original}]}, "d")
        else:
            value = {"url": canonical, "card_selector": "main li", "single_page": True}
            events = event_pair("browser_navigate", {"url": original}, {"url": canonical}, "n")
            events += event_pair("browser_verify", {"card_selector": "main li"},
                                 {"url": canonical, "cardCount": 3, "pagination": {"total": 0}}, "v")
        return SimpleNamespace(final_response=json.dumps(value), finish_reason="completed", events=events)
    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    result = agent_recon._run_staged_harness("redirect", {"target": target, "timeout_seconds": 60}, ())
    assert json.loads(result.final_response)["branches"][0]["url"] == canonical


def test_invalid_discovery_retains_safe_repair_issues(monkeypatch):
    monkeypatch.setattr(agent_recon, "_execute_harness", lambda *args: SimpleNamespace(
        final_response="{}", finish_reason="completed", events=[]))
    target = {"school": "样本大学", "college": "样本学院", "url": "https://example.edu/"}
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness("invalid", {"target": target, "timeout_seconds": 60}, ())
    assert caught.value.safe_feedback["issues"] == [{'status': 'plan_contract_rejected', 'gate_issues': [
        {'path': '$.school', 'status': 'schema_missing'},
        {'path': '$.college', 'status': 'schema_missing'},
        {'path': '$.branches', 'status': 'schema_missing'},
    ]}]


@pytest.mark.parametrize("url", ["https://person@example.edu/list", "https://example.edu/list?email=person%40example.edu"])
def test_rejected_discovery_url_is_never_replayed_in_repair_feedback(monkeypatch, url):
    target = {"school": "样本大学", "college": "样本学院", "url": "https://example.edu/"}
    value = {"school": target["school"], "college": target["college"],
             "source_kind": "university_faculty_roster", "branches": [{"url": url}]}
    monkeypatch.setattr(agent_recon, "_execute_harness", lambda *args: SimpleNamespace(
        final_response=json.dumps(value), finish_reason="completed", events=[]))
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness("invalid-url", {"target": target, "timeout_seconds": 60}, ())
    assert caught.value.safe_feedback["known_urls"] == []
    assert url not in json.dumps(caught.value.safe_feedback)

# Source verification is a separately protected integration boundary.
pytestmark = pytest.mark.verified_source_boundary
