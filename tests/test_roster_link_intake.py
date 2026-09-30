"""Batch rows with supplied roster links: one task per college, links as leads."""
import json
from types import SimpleNamespace

import pytest

from server import agent_recon
from server.agent_queue import AgentTaskQueue
from server.intake import normalize_url, parse_batch, site_domain

ROWS = """大学\t学院\t学院主页\t师资链接
湖南科技学院\t马克思主义学院\thttps://www.huse.edu.cn/mkszyxy/\thttps://www.huse.edu.cn/mkszyxy/szdw/dwgk1.htm
湖南科技学院\t马克思主义学院\thttps://www.huse.edu.cn/mkszyxy/\thttps://www.huse.edu.cn/mkszyxy/szdw/fjs.htm
湖南科技学院\t马克思主义学院\thttps://www.huse.edu.cn/mkszyxy/\thttps://www.huse.edu.cn/mkszyxy/szdw/fjs.htm
湖南科技学院\t体育学院\thttps://www.huse.edu.cn/tyxy/\thttps://www.huse.edu.cn/tyxy/xygk/jsdw/js_bs.htm
湖南科技学院\t音乐与舞蹈学院\thttps://www.huse.edu.cn/yyxy/
湖南科技学院\t传媒学院\thttps://sfll.hnist.cn/\thttps://sfll.hnist.cn/szdw.htm
"""


def test_rows_group_into_one_college_with_every_supplied_link():
    result = parse_batch(ROWS)
    assert result.errors == []
    colleges = {entry.college: entry for entry in result.colleges}
    assert list(colleges) == ["马克思主义学院", "体育学院", "音乐与舞蹈学院", "传媒学院"]
    assert colleges["马克思主义学院"].roster_urls == [
        "https://www.huse.edu.cn/mkszyxy/szdw/dwgk1.htm", "https://www.huse.edu.cn/mkszyxy/szdw/fjs.htm"]
    assert colleges["马克思主义学院"].lines == [2, 3, 4]
    assert colleges["音乐与舞蹈学院"].roster_urls == []  # three columns: the scout finds the rosters


def test_a_college_on_another_schools_domain_is_questioned_before_queueing():
    warnings = parse_batch(ROWS).warnings
    assert warnings == ["湖南科技学院 传媒学院：学院主页在 hnist.cn，同校其他学院在 huse.edu.cn，请确认是否为本校学院"]
    other_site = parse_batch("湖南科技学院 体育学院 https://www.huse.edu.cn/tyxy/ https://tyxy.other.cn/js.htm")
    assert "不在学院主页的站点上" in other_site.warnings[0]


def test_malformed_rows_are_reported_by_line():
    result = parse_batch("湖南科技学院 体育学院\n湖南科技学院 体育学院 not-a-url\n"
                         "湖南科技学院 体育学院 https://a.edu.cn/ https://a.edu.cn/1 extra\n"
                         "湖南科技学院 化学学院 https://a.edu.cn/hx/\n湖南科技学院 化学学院 https://b.edu.cn/hx/")
    assert [error.split("：")[0].split("的")[0].split("与")[0] for error in result.errors] == [
        "第 1 行应为", "第 2 行", "第 3 行应为", "第 5 行"]


def test_urls_are_normalised_and_domains_compared_by_registrable_part():
    assert normalize_url(" www.huse.edu.cn/tyxy/，") == "http://www.huse.edu.cn/tyxy/"
    assert normalize_url("https://USER:pw@a.edu.cn/") is None
    assert normalize_url("javascript:void(0)") is None
    assert site_domain("https://www.huse.edu.cn/x") == "huse.edu.cn"
    assert site_domain("https://sfll.hnist.cn/") == "hnist.cn"


@pytest.mark.parametrize("port", ["notaport", "-1", "0", "65536"])
@pytest.mark.parametrize("column", ["home", "roster"])
def test_invalid_ports_are_reported_before_any_college_is_queued(port, column):
    invalid = f"https://a.edu.cn:{port}/"
    text = f"U C {invalid}" if column == "home" else f"U C https://a.edu.cn/ {invalid}"
    parsed = parse_batch(text)
    assert parsed.colleges == []
    assert len(parsed.errors) == 1 and parsed.errors[0].startswith("第 1 行")
    assert "不是有效网址" in parsed.errors[0]
    assert normalize_url(invalid) is None


@pytest.mark.parametrize("port", [1, 443, 8000, 65535])
def test_valid_explicit_ports_are_preserved(port):
    url = f"https://a.edu.cn:{port}/"
    assert normalize_url(url) == url
    parsed = parse_batch(f"U C {url} {url}js.htm")
    assert parsed.errors == [] and parsed.warnings == []
    assert parsed.colleges[0].roster_urls == [url + "js.htm"]


def test_queue_keeps_supplied_links_through_claim_and_completion(tmp_path):
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    [task] = queue.submit_batch([("U", "C", "https://a.edu.cn/", ["https://a.edu.cn/js.htm"])], "b1")
    assert queue.get_task(task.task_id).roster_urls == ["https://a.edu.cn/js.htm"]
    claimed = queue.claim_task("w1")
    assert claimed.roster_urls == ["https://a.edu.cn/js.htm"]
    [plain] = queue.submit_batch([("U", "D", "https://a.edu.cn/d/")], "b2")
    assert queue.get_task(plain.task_id).roster_urls == []


def test_discovery_prompt_lists_supplied_links_only_when_given(monkeypatch):
    prompts = {}
    target = {"school": "U", "college": "C", "url": "https://a.edu.cn/"}

    def execute(prompt, sid, cfg, patches):
        prompts[cfg["phase"]] = prompt
        return SimpleNamespace(final_response="{}", finish_reason="completed", events=[])

    monkeypatch.setattr(agent_recon, "_execute_harness", execute)
    for provided in (["https://a.edu.cn/js.htm", "not a url"], []):
        with pytest.raises(agent_recon.ReconPlanError):
            agent_recon._run_staged_harness("s", {"target": target, "timeout_seconds": 10,
                                                  "provided_urls": provided}, ())
        if provided:
            assert "给定链接：\nhttps://a.edu.cn/js.htm" in prompts["discovery"]
            assert "not a url" not in prompts["discovery"]
        else:
            assert "给定链接" not in prompts["discovery"]


@pytest.mark.asyncio
async def test_worker_skips_the_site_cache_and_passes_links_to_the_scout(tmp_path, monkeypatch):
    from server import agent_worker
    from server.agent_recon import ReconResult
    worker = agent_worker.AgentWorker(db_path=tmp_path / "tasks.db", output_dir=tmp_path / "out")
    plan = {"school": "U", "college": "C", "branches": [{"url": "https://a.edu.cn/js.htm",
                                                           "card_selector": "li", "single_page": True}]}
    calls = []

    async def scout(*_args, **kwargs):
        calls.append(kwargs)
        return ReconResult(plan, 0, "completed", "s")

    async def cache(*_args):
        raise AssertionError("a task with supplied links must not reuse a site-wide cached plan")

    async def browser(*_args):
        return {"delivery_ready": True, "unresolved": []}

    monkeypatch.setattr(agent_worker, "reconnoiter", scout)
    monkeypatch.setattr(worker, "_cached_recon", cache)
    monkeypatch.setattr(worker, "_run_browser_plan", browser)
    task = SimpleNamespace(school="U", college="C", url="https://a.edu.cn/", task_id="t",
                           roster_urls=["https://a.edu.cn/js.htm"])
    report = await worker._harvest_with_repair(task, tmp_path / "task")
    assert report["delivery_ready"] and calls[0]["provided_urls"] == ["https://a.edu.cn/js.htm"]
    assert not (tmp_path / "out" / "plan-cache").exists() or not any((tmp_path / "out" / "plan-cache").iterdir())


def test_batch_api_asks_before_queueing_suspicious_rows(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import server.app as web
    from server.access_control import AccountStore
    queue = AgentTaskQueue(tmp_path / "tasks.db", legacy_json_path=None)
    store = AccountStore(queue.db_path)
    store.create_user("alice", "Alice", "alice password 12345", "employee")
    monkeypatch.setattr(web, "agent_queue", queue)
    monkeypatch.setattr(web, "account_store", store)
    client = TestClient(web.app)
    client.post("/api/auth/login", json={"username": "alice", "password": "alice password 12345"})
    headers = {"X-CSRF-Token": client.get("/api/auth/me").json()["csrf_token"]}
    response = client.post("/api/collect_batch", json={"items_text": ROWS}, headers=headers)
    assert response.status_code == 409 and len(response.json()["warnings"]) == 1
    assert queue.get_pending_tasks() == []
    confirmed = client.post("/api/collect_batch", json={"items_text": ROWS, "confirm_warnings": True},
                            headers=headers)
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["total_queued"] == 4 and body["with_roster_links"] == 3
    tasks = {task.college: task for task in queue.get_pending_tasks()}
    assert tasks["马克思主义学院"].roster_urls[-1] == "https://www.huse.edu.cn/mkszyxy/szdw/fjs.htm"
    assert json.dumps(queue.get_batch_status(body["batch_id"]), ensure_ascii=False).count("roster_urls")
