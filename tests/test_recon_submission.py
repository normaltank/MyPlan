"""Public scout submissions reject data and require the current browser proof."""
import json

import pytest
import yaml
from playwright.async_api import async_playwright

from server.agent_recon import write_runtime_patch
from server.recon_browser_mcp import BrowserScout, phase_tools
from server.recon_contract import plan_digest, submitted_plan

TARGET = {"school": "样本大学", "college": "样本学院", "url": "https://example.edu/faculty"}
BRANCH = {"url": TARGET["url"], "card_selector": "main ul > li", "single_page": True}


def body(reply):
    return json.loads(reply["content"][0]["text"])


@pytest.mark.asyncio
async def test_schema_feedback_never_echoes_forbidden_teacher_values_or_keys(tmp_path):
    scout = BrowserScout(None, tmp_path, "branch", TARGET)
    result = await scout.call("submit_plan", {"plan": {**BRANCH, "张三": "teacher@example.edu"}})
    assert result["isError"]
    assert body(result)["issues"] == [{"path": "$.<field>", "status": "schema_extra_forbidden"}]
    assert "张三" not in json.dumps(result, ensure_ascii=False)
    assert "teacher@example.edu" not in "".join(p.read_text() for p in tmp_path.glob("*.json"))
    result = await scout.call("submit_plan", {"plan": {**BRANCH, "card_selector": "a[title='张三']"}})
    assert body(result)["issues"][0]["path"] == "$.card_selector"
    assert not list(tmp_path.glob("*.accepted.json"))


def test_submission_schema_references_resolve_at_tool_root_and_target_is_explicit(tmp_path):
    schema = next(t["inputSchema"] for t in phase_tools("full") if t["name"] == "submit_plan")
    assert "Branch" in schema["$defs"]
    assert schema["properties"]["plan"]["properties"]["branches"]["items"]["$ref"] == "#/$defs/Branch"
    patch = tmp_path / "runtime.yml"
    write_runtime_patch(patch, phase="branch", target=TARGET)
    entries = yaml.safe_load(patch.read_text(encoding="utf-8"))
    mcp = entries[-1]["insert"][0]["config"]
    assert json.loads(mcp["args"][-1]) == TARGET
    assert mcp["args"][-2] == "--target"


@pytest.mark.asyncio
@pytest.mark.browser
async def test_branch_submission_requires_real_proof_and_keeps_only_accepted_structure(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            from pathlib import Path
            await context.add_init_script(Path("server/recon_dom_probe.js").read_text(encoding="utf-8"))
            await context.route("**/*", lambda route: route.fulfill(
                content_type="text/html", body="<main><ul><li>张富有</li><li>刘圣洁</li></ul></main>"))
            scout = BrowserScout(await context.new_page(), tmp_path, "branch", TARGET)
            assert body(await scout.call("submit_plan", {"plan": BRANCH}))["accepted"] is False
            await scout.call("browser_navigate", {"url": TARGET["url"]})
            await scout.call("browser_verify", {"card_selector": BRANCH["card_selector"]})
            result = body(await scout.call("submit_plan", {"plan": BRANCH}))
            assert result == {"accepted": True, "digest": plan_digest(BRANCH)}
            assert json.loads(next(tmp_path.glob("*.accepted.json")).read_text(encoding="utf-8")) == BRANCH
            changed = {**BRANCH, "card_selector": "main li"}
            assert body(await scout.call("submit_plan", {"plan": changed}))["accepted"] is False
        finally:
            await browser.close()


@pytest.mark.asyncio
@pytest.mark.browser
async def test_discovery_submission_rejects_unobserved_or_omitted_urls(tmp_path):
    discovery = {"school": TARGET["school"], "college": TARGET["college"],
                 "source_kind": "university_faculty_roster", "branches": [{"url": TARGET["url"]}]}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            # Production context supplies the same probe at every navigation.
            from pathlib import Path
            await context.add_init_script(Path("server/recon_dom_probe.js").read_text(encoding="utf-8"))
            await context.route("**/*", lambda route: route.fulfill(content_type="text/html", body="<main>Faculty</main>"))
            scout = BrowserScout(await context.new_page(), tmp_path, "discovery", TARGET)
            assert body(await scout.call("submit_plan", {"plan": discovery}))["accepted"] is False
            await scout.call("browser_navigate", {"url": TARGET["url"]})
            assert body(await scout.call("submit_plan", {"plan": discovery}))["accepted"] is True
            scout.target = {**TARGET, "required_urls": ["https://example.edu/other"]}
            result = body(await scout.call("submit_plan", {"plan": discovery}))
            assert result["issues"] == [{"path": "$.branches", "status": "previous_branch_omitted"}]
        finally:
            await browser.close()


def test_harness_uses_only_successful_digest_bound_submissions():
    def events(reply):
        return [
            {"type": "tool/call", "data": {"callId": "1", "name": "mcp__playwright__submit_plan",
                                            "arguments": json.dumps({"plan": BRANCH})}},
            {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": "1",
                "content": [{"type": "text", "text": json.dumps(reply)}]}]}}},
        ]
    assert submitted_plan(events({"accepted": True, "digest": plan_digest(BRANCH)})) == BRANCH
    assert submitted_plan(events({"accepted": False, "digest": plan_digest(BRANCH)})) is None
    assert submitted_plan(events({"accepted": True, "digest": "wrong"})) is None

# Source verification is a separately protected integration boundary.
pytestmark = pytest.mark.verified_source_boundary
