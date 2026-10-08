import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from server import agent_recon
from server.recon_pricing import estimate
from server.recon_templates import structural_fingerprint, verify_template


def pair(name, arguments, value, cid):
    return [{"type": "tool/call", "data": {"callId": cid, "name": "mcp__playwright__" + name,
             "arguments": json.dumps(arguments)}},
            {"type": "tool/result", "data": {"message": {"content": [{"toolCallId": cid,
              "content": [{"type": "text", "text": json.dumps(value)}]}]}}}]


def result(value, events):
    usage = {"type": "assistant/message", "data": {"usage": {
        "inputTokens": 100, "cacheReadTokens": 200, "outputTokens": 30}}}
    return SimpleNamespace(final_response=json.dumps(value), finish_reason="completed", events=[usage, *events])


TARGET = {"school": "样本大学", "college": "样本学院", "url": "https://example.edu/"}
URLS = [f"https://example.edu/roster-{i}" for i in range(3)]


def discovery():
    return result({"school": TARGET["school"], "college": TARGET["college"],
        "source_kind": "university_faculty_roster", "branches": [{"url": u} for u in URLS]},
        pair("browser_navigation", {}, {"items": [{"url": u} for u in URLS]}, "d"))


def branch(url):
    value = {"url": url, "card_selector": "main li", "single_page": True}
    return value, pair("browser_verify", {"card_selector": "main li"},
                       {"url": url, "cardCount": 3, "pagination": {"total": 0}}, url)


def test_parallel_sessions_have_unique_runtime_patches_and_keep_successful_siblings(monkeypatch, tmp_path):
    barrier = threading.Barrier(3)
    patch_paths = []
    def execute(prompt, sid, cfg, patches):
        patch_paths.append(patches[0])
        if cfg["phase"] == "discovery":
            return discovery()
        barrier.wait(timeout=5)  # serial execution cannot satisfy this
        url = next(u for u in URLS if u in prompt)
        value, proof = branch(url)
        return result(value, proof if url != URLS[1] else [])
    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    patch = tmp_path / "runtime.yml"
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness("parallel", {"target": TARGET, "timeout_seconds": 10,
            "parallel_branches": True, "runtime_patch": str(patch)}, (str(patch),))
    error = caught.value
    assert set(error.verified_branches) == {URLS[0], URLS[2]}
    assert len(set(patch_paths)) == 4
    assert error.diagnostics["model_calls"] == 4
    assert [item["url"] for item in error.safe_feedback["issues"]] == [URLS[1]]


def test_template_reuse_verifies_every_target_and_falls_back_independently(monkeypatch):
    calls, checked = [], []
    def execute(prompt, sid, cfg, patches):
        if cfg["phase"] == "discovery":
            return discovery()
        url = next(u for u in URLS if u in prompt)
        calls.append(url)
        value, proof = branch(url)
        return result(value, proof)
    def verify(value, target, root, timeout, expected):
        checked.append(value["url"])
        if value["url"] == URLS[2]:
            return {"valid": False, "reason": "template_changed"}
        _, proof = branch(value["url"])
        return {"valid": True, "events": proof, "evidence_root": str(root)}
    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    monkeypatch.setattr("server.recon_templates.inspect_templates", lambda *_: dict.fromkeys(URLS, "same"))
    monkeypatch.setattr("server.recon_templates.verify_template", verify)
    value = agent_recon._run_staged_harness("templates", {"target": TARGET, "timeout_seconds": 10,
        "parallel_branches": True, "template_reuse": True}, ())
    assert calls == [URLS[0], URLS[2]]
    assert set(checked) == set(URLS[1:])
    assert [b["url"] for b in json.loads(value.final_response)["branches"]] == URLS
    assert agent_recon.run_metrics(value.events)["model_calls"] == 3
    assert any(p.get("template_reused") for p in value.phase_metrics)


def test_runtime_requires_tool_acceptance_and_does_not_accept_unsubmitted_final_json(monkeypatch):
    monkeypatch.setattr(agent_recon, '_execute_harness', lambda *_: discovery())
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness('unsubmitted', {'target': TARGET, 'timeout_seconds': 10,
            'require_submission': True}, ())
    assert caught.value.safe_feedback['issues'] == [{'status': 'plan_contract_rejected'}]
    assert caught.value.diagnostics['model_calls'] == 1


def test_runtime_report_aggregates_failed_recon_and_browser_retries_without_teacher_data():
    from server.harvest_runtime_report import build_runtime_report
    attempts = [{'model_calls': 4, 'elapsed_seconds': 10, 'token_usage': {'cached_input': 2000},
        'phases': [{'phase': 'branch', 'template_reused': True}]},
        {'model_calls': 2, 'status': 'plan_contract_rejected', 'token_usage': {'output': 100}}]
    report = build_runtime_report(attempts, [{'retry': 0}, {'retry': 1}],
        [{'status': 'distinct_profile_identity_unverified', 'name': '保密姓名', 'url': 'https://person/'}],
        21, {'cost': {'estimated_usd': .01}})
    assert report['recon']['model_calls'] == 6
    assert report['recon']['template_reused_branches'] == 1
    assert report['recon']['token_usage']['cached_input'] == 2000
    assert report['review_count'] == 1 and len(report['browser_runs']) == 2
    assert '保密姓名' not in json.dumps(report, ensure_ascii=False)


@pytest.mark.browser
def test_template_reuse_uses_actual_browser_cards_profiles_and_detects_drift(tmp_path):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    html = '<body><main><ul><li><a href="/person/1">甲</a></li><li><a href="/person/2">乙</a></li></ul></main></body>'
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = '<body><article><p>完整学术正文。</p></article></body>' if self.path.startswith('/person') else html
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(body.encode())
        def log_message(self, *_):
            pass
    http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{http.server_port}/roster'
    value = {"url": url, "card_selector": "main li", "single_page": True,
             "profile_content_selector": "article"}
    try:
        proof = verify_template(value, TARGET, tmp_path / 'valid', 20, structural_fingerprint(html))
        assert proof['valid'] is True, proof
        assert proof['model_calls'] == 0
        assert sum(e.get('data', {}).get('name', '').endswith('browser_verify_profile')
                   for e in proof['events']) == 2
        bad = verify_template(value, TARGET, tmp_path / 'invalid', 20, 'other')
        assert bad['valid'] is False and bad['reason'] == 'template_changed'
    finally:
        http.shutdown()
        http.server_close()


def test_fee_alert_distinguishes_cache_and_peak_prices_and_is_thread_safe(monkeypatch):
    off = datetime(2026, 9, 30, 5, tzinfo=timezone.utc).timestamp()
    peak = datetime(2026, 9, 30, 6, tzinfo=timezone.utc).timestamp()
    usage = {"uncached_input": 1_000_000, "cached_input": 1_000_000, "output": 1_000_000}
    assert estimate(usage, 'deepseek-official', 'deepseek-flash', off) == pytest.approx(.753)
    assert estimate(usage, 'deepseek-official', 'deepseek-flash', peak) == pytest.approx(1.506)
    assert estimate(usage, 'other-provider', 'deepseek-flash', off) is None
    monkeypatch.setattr(time, 'time', lambda: off)
    alerts = []
    budget = agent_recon.ReconBudget(alert_cost_usd=.1, on_alert=alerts.append)
    event = {"type": "assistant/message", "data": {"usage": {
        "inputTokens": 0, "cacheReadTokens": 1_000_000, "outputTokens": 0}}}
    budget.observe(event)
    assert not alerts  # one million cached tokens cost .003, not a token alarm
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(budget.observe, [event] * 99))
    assert budget.snapshot()['calls'] == 100
    assert budget.snapshot()['cost']['estimated_usd'] == pytest.approx(.3)
    assert len(alerts) == 1
    restored = agent_recon.ReconBudget()
    restored.seed([budget.delta(agent_recon.ReconBudget().snapshot())])
    assert restored.snapshot()['cost']['estimated_usd'] == pytest.approx(.3)
    restored.seed([{"model_calls": 1}])
    assert restored.snapshot()['cost']['estimated_usd'] is None


@pytest.mark.browser
@pytest.mark.asyncio
@pytest.mark.parametrize('endless', [False, True])
async def test_profile_waits_for_finite_animation_but_blocks_continuing_animation(tmp_path, endless):
    from playwright.async_api import async_playwright
    from server.browser_context import new_browser_context
    from server.browser_readiness import install_request_tracking
    from server.profile_structure import verify_profile_container
    repeat = 'infinite' if endless else '1'
    html = ('<body><style>@keyframes enter {from{transform:translateX(-5px)}to{transform:translateX(0)}}'
            'article {animation: enter 1.2s ' + repeat + ';}</style>'
            '<article><p>张富有完整履历。</p></article></body>')
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await new_browser_context(browser)
        page = await context.new_page()
        install_request_tracking(page)
        await context.raw_context.route('**/*', lambda request: request.fulfill(body=html, content_type='text/html'))
        try:
            await page.goto('https://fixture.edu/profile')
            proof = await verify_profile_container(page, 'article')
            assert proof['confirmed'] is (not endless), proof
            if endless:
                assert proof['reason'] == 'dom_unstable'
            else:
                assert proof['settle_samples'] > 1
        finally:
            await browser.close()


def roster_page(cards, highlighted, pager=False):
    menu = ''.join(f'<li><div class="nav-link{" hover" if i == highlighted else ""}"><a href="/m{i}">栏目</a></div></li>'
                   for i in range(4))
    items = ''.join(f'<li><a href="/p{i}">某</a><span>2020</span></li><span></span>' for i in range(cards))
    nav = '<div class="pb_sys_common"><a href="/2.htm">下页</a></div>' if pager else ''
    return f'<body><ul class="menu">{menu}</ul><div class="list"><ul>{items}</ul>{nav}</div></body>'


def test_sibling_roster_pages_share_one_template_fingerprint():
    # 教授／副教授／讲师 pages: different card counts and menu highlight, one template.
    pages = [roster_page(7, 0), roster_page(12, 1), roster_page(3, 2)]
    assert len({structural_fingerprint(html) for html in pages}) == 1
    # A paged roster is a different shape and keeps its own scout.
    assert structural_fingerprint(roster_page(7, 0, pager=True)) != structural_fingerprint(pages[0])


def test_siblings_wait_for_their_template_scout_instead_of_repeating_a_failure(monkeypatch):
    calls, checked = [], []
    def execute(prompt, sid, cfg, patches):
        if cfg["phase"] == "discovery":
            return discovery()
        url = next(u for u in URLS if u in prompt)
        calls.append(url)
        value, proof = branch(url)
        return result(value, [] if "本阶段结构反馈" not in prompt else proof)  # first scout fails its gate
    def verify(value, target, root, timeout, expected):
        checked.append(value["url"])
        _, proof = branch(value["url"])
        return {"valid": True, "events": proof, "evidence_root": str(root)}
    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    monkeypatch.setattr("server.recon_templates.inspect_templates", lambda *_: dict.fromkeys(URLS, "same"))
    monkeypatch.setattr("server.recon_templates.verify_template", verify)
    cfg = {"target": TARGET, "timeout_seconds": 10, "parallel_branches": True, "template_reuse": True}
    with pytest.raises(agent_recon.ReconPlanError) as caught:
        agent_recon._run_staged_harness("one-scout", cfg, ())
    error = caught.value
    assert calls == [URLS[0]] and checked == []
    assert error.diagnostics["model_calls"] == 2  # discovery and the one template scout
    assert error.safe_feedback["known_urls"] == URLS
    assert [item["url"] for item in error.safe_feedback["issues"]] == [URLS[0]]

    # The repair round re-scouts the failed page; its siblings still follow it.
    calls.clear()
    value = agent_recon._run_staged_harness("one-scout-repair", {**cfg,
        "feedback": error.safe_feedback, "resume_discovery": error.discovery_checkpoint,
        "verified_branches": error.verified_branches}, ())
    assert calls == [URLS[0]]
    assert sorted(checked) == URLS[1:]
    assert [b["url"] for b in json.loads(value.final_response)["branches"]] == URLS
