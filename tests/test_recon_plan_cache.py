import json
import copy
import hashlib
import sqlite3
import pytest
from server.recon_plan_cache import ReconPlanCache, validate_cached_plan


PLAN = {"school": "样本大学", "college": "样本学院", "source_kind": "university_faculty_roster",
        "branches": [{"url": "https://example.edu/list", "card_selector": "main li", "single_page": True,
                      "profile_content_selector": "article"}]}


def stored_payload(cache):
    with sqlite3.connect(cache.db_path) as db:
        return json.loads(db.execute('SELECT payload_json FROM plan_versions ORDER BY version_id DESC LIMIT 1').fetchone()[0])


def tamper_payload(cache, value, *, match_index=False):
    with sqlite3.connect(cache.db_path) as db:
        db.execute('UPDATE plan_versions SET payload_json=?', (json.dumps(value),))
        if match_index:
            from server.recon_plan_cache import _payload_digest
            structure_digest = _payload_digest({k: v for k, v in value.items() if k not in {'saved_at', 'sha256'}})
            db.execute('UPDATE plan_versions SET sha256=?,structure_sha256=?', (value['sha256'], structure_digest))


def test_cache_fingerprint_must_be_hexadecimal(tmp_path):
    cache = ReconPlanCache(tmp_path)
    with pytest.raises(ValueError):
        cache.put(PLAN, "https://example.edu/", [{"url": "https://example.edu/",
            "links": ["https://example.edu/list"], "fingerprint": "g" * 64}])


@pytest.mark.parametrize("change", ["navigation_missing", "navigation_malformed", "plan_changed", "teacher_field"])
def test_cache_rejects_tampered_document_without_raising(tmp_path, change):
    cache = ReconPlanCache(tmp_path)
    pages = [{"url": "https://example.edu/", "links": ["https://example.edu/list"], "fingerprint": "a" * 64}]
    cache.put(PLAN, "https://example.edu/", pages)
    value = stored_payload(cache)
    if change == "navigation_missing":
        value["discovery_pages"] = []
    elif change == "navigation_malformed":
        value["discovery_pages"][0]["fingerprint"] = "g" * 64
    elif change == "plan_changed":
        value["plan"]["branches"][0]["card_selector"] = "aside li"
    else:
        value["plan"]["teacher_name"] = "forbidden faculty value"
    tamper_payload(cache, value)
    assert cache.get(PLAN["school"], PLAN["college"], "https://example.edu/") is None


@pytest.mark.parametrize("change", ["missing_navigation", "teacher_field"])
def test_cache_reuses_strict_schema_even_when_document_digest_matches(tmp_path, change):
    cache = ReconPlanCache(tmp_path)
    cache.put(PLAN, "https://example.edu/", [{"url": "https://example.edu/",
        "links": ["https://example.edu/list"], "fingerprint": "a" * 64}])
    value = stored_payload(cache)
    if change == "missing_navigation":
        value["discovery_pages"] = []
    else:
        value["plan"]["teacher_name"] = "forbidden teacher field"
    payload = {k: v for k, v in value.items() if k != "sha256"}
    value["sha256"] = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    tamper_payload(cache, value, match_index=True)
    assert cache.get(PLAN["school"], PLAN["college"], "https://example.edu/") is None


@pytest.mark.parametrize("change", ["expired", "contract"])
def test_cache_rejects_expiry_and_changed_contract(tmp_path, monkeypatch, change):
    from server import recon_plan_cache
    monkeypatch.setattr(recon_plan_cache.time, "time", lambda: 1000)
    cache = ReconPlanCache(tmp_path, max_age_seconds=60)
    cache.put(PLAN, "https://example.edu/", [{"url": "https://example.edu/",
        "links": ["https://example.edu/list"], "fingerprint": "a" * 64}])
    if change == "expired":
        monkeypatch.setattr(recon_plan_cache.time, "time", lambda: 1061)
    else:
        monkeypatch.setattr(recon_plan_cache, "_contract", lambda: "changed-contract")
    assert cache.get(PLAN["school"], PLAN["college"], "https://example.edu/") is None


@pytest.mark.asyncio
async def test_branch_only_validation_checks_fresh_cards_and_actual_ajax_pagination(tmp_path):
    from playwright.async_api import async_playwright
    from server.agent_recon import selector_evidence_issues
    plan = copy.deepcopy(PLAN)
    plan["branches"][0].pop("single_page")
    plan["branches"][0].update(next_selector="#next", max_pages=3)
    body = ('<main><ul><li><a href="/p1">张富有</a>教授</li><li><a href="/p2">刘圣洁</a>教授</li>'
            '<li><a href="/p3">孙洁</a>教授</li></ul><button id="next" class="next" '
            'onclick="document.querySelector(\'a\').textContent=\'王小明\'">Next</button></main>')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        try:
            async def route(request):
                content = '<article><p>完整正文与字段。</p></article>' if '/p' in request.request.url else body
                await request.fulfill(body=content, content_type="text/html; charset=utf-8")
            await context.route("**/*", route)
            entry = {"plan": plan}
            result = await validate_cached_plan(entry, context, tmp_path / "partial", verify_discovery=False)
            assert result["valid"], {k: v for k, v in result.items() if k != "events"}
            assert result["verified_discovery"] is False
            assert selector_evidence_issues(plan, result["events"]) == []
            assert any(e.get("data", {}).get("name") == "mcp__playwright__browser_click" for e in result["events"])
            default = await validate_cached_plan(entry, context, tmp_path / "default")
            assert not default["valid"] and default["reason"] == "discovery_evidence_missing"
            body = '<main><p>No cards</p></main>'
            changed = await validate_cached_plan(entry, context, tmp_path / "selector", verify_discovery=False)
            assert not changed["valid"]
            body = '<main><ul><li>A</li><li>B</li><li>C</li></ul><button id="other" class="next">Next</button></main>'
            changed = await validate_cached_plan(entry, context, tmp_path / "pager", verify_discovery=False)
            assert not changed["valid"]
        finally:
            await browser.close()


def test_cache_requires_discovery_evidence_and_never_accepts_teacher_fields(tmp_path):
    cache = ReconPlanCache(tmp_path)
    assert cache.get("样本大学", "样本学院", "https://example.edu/") is None
    with pytest.raises(ValueError):
        cache.put(PLAN, "https://example.edu/", [])
    pages = [{"url": "https://example.edu/", "links": ["https://example.edu/list"], "fingerprint": "a" * 64}]
    cache.put(PLAN, "https://example.edu/", pages)
    assert cache.get("样本大学", "样本学院", "https://example.edu/")["plan"] == PLAN
    bad = {**PLAN, "teacher_name": "不允许的教师字段"}
    with pytest.raises(Exception):
        cache.put(bad, "https://example.edu/", pages)


@pytest.mark.asyncio
async def test_cached_plan_is_revalidated_in_real_browser_and_detects_new_links(tmp_path):
    from playwright.async_api import async_playwright
    from server.recon_plan_cache import navigation_evidence
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context()
        try:
            async def route(request):
                body = ('<article><p>完整正文与字段。</p></article>' if '/p/' in request.request.url else
                        '<nav><a href="/list">师资</a></nav><main><ul><li><a href="/p/1">甲老师</a></li><li><a href="/p/2">乙老师</a></li><li><a href="/p/3">丙老师</a></li></ul></main>')
                await request.fulfill(body=body, content_type="text/html")
            await context.route("**/*", route)
            page = await context.new_page()
            await page.goto("https://example.edu/")
            evidence = await navigation_evidence(page)
            assert set(evidence) == {"url", "links", "fingerprint"}
            assert "老师" not in json.dumps(evidence, ensure_ascii=False)
            await page.close()
            entry = {"plan": PLAN, "discovery_pages": [evidence]}
            valid = await validate_cached_plan(entry, context, tmp_path / "proof")
            assert valid["valid"] and valid["model_calls"] == 0
            assert sum(e.get('data', {}).get('name') == 'mcp__playwright__browser_verify_profile'
                       for e in valid['events']) == 2
            await context.unroute("**/*", route)
            async def changed(request):
                await request.fulfill(body='<nav><a href="/list">师资</a><a href="/advisors">新导师目录</a></nav>', content_type="text/html")
            await context.route("**/*", changed)
            invalid = await validate_cached_plan(entry, context, tmp_path / "changed")
            assert not invalid["valid"] and invalid["reason"] == "discovery_changed"
        finally:
            await browser.close()
