"""Offline replay of saved real-site cases (server/case_replay.py)."""
import asyncio
import json

import pytest

from server import case_replay

ROOT = 'https://fixture.edu/'
ROSTER = ('<html><body><div class="list"><ul class="t">'
          '<li><a href="/p/1.htm">王建国</a> 教授</li><li><a href="/p/2.htm">李晓明</a> 副教授</li>'
          '</ul></div></body></html>')


def profile(name):
    return (f'<html><body><header>学院导航</header><article><h1>{name}</h1><p>职称：教授</p>'
            f'<p>{name}，长期从事材料研究。</p></article><footer>版权</footer></body></html>')


def write_case(root, name, *, captured_at='2026-10-01T00:00:00Z', ready=True, college='样本学院'):
    case = root / name
    snaps = case / 'out' / f'样本大学_{college}_snapshots'
    snaps.mkdir(parents=True)
    pages = {ROOT + 'list.htm': ('directory_001.html', ROSTER),
             ROOT + 'p/1.htm': ('profile_001.html', profile('王建国')),
             ROOT + 'p/2.htm': ('profile_002.html', profile('李晓明'))}
    snapshots = []
    for url, (file, html) in pages.items():
        (snaps / file).write_text(html, encoding='utf-8')
        # Recorded where the original run wrote it, not where the case lives now.
        snapshots.append({'url': url + '#top', 'path': f'/tmp/elsewhere/{name}/out/{snaps.name}/{file}'})
    snapshots.append({'url': ROOT + 'gone.htm', 'path': '/tmp/elsewhere/out/never-saved.html'})
    (case / 'out' / f'样本大学_{college}_audit.json').write_text(
        json.dumps({'snapshots': snapshots}), encoding='utf-8')
    (case / 'plan.json').write_text(json.dumps({
        'school': '样本大学', 'college': college, 'branches': [
            {'url': ROOT + 'list.htm', 'card_selector': 'ul.t > li', 'single_page': True}]}), encoding='utf-8')
    (case / 'result.json').write_text(json.dumps({
        'status': 'completed' if ready else 'needs_agent_review', 'delivery_ready': ready,
        'total': 2, 'with_email': 0, 'unresolved_count': 0 if ready else 1}), encoding='utf-8')
    (case / 'captured_at').write_text(captured_at, encoding='utf-8')
    return case


def test_captured_files_maps_recorded_paths_into_the_case(tmp_path):
    case = case_replay.load_case(write_case(tmp_path, 'a'))
    files = case_replay.captured_files(case.directory, case.audit)
    assert files[ROOT + 'list.htm'].read_text(encoding='utf-8') == ROSTER
    assert ROOT + 'gone.htm' not in files


def test_discover_keeps_newest_capture_per_college(tmp_path):
    write_case(tmp_path, 'a_before', captured_at='2026-10-01T00:00:00Z', ready=False)
    write_case(tmp_path, 'a', captured_at='2026-10-02T00:00:00Z')
    write_case(tmp_path, 'b', college='另一学院')
    (tmp_path / 'not-a-case').mkdir()
    assert [case.name for case in case_replay.discover(tmp_path)] == ['a', 'b']
    assert [case.name for case in case_replay.discover(tmp_path, ['a_before'])] == ['a_before']


def test_render_counts_ready_and_lists_codes():
    saved = {'delivery_ready': False, 'total': 2, 'unresolved': 1, 'codes': {}}
    replay = {'delivery_ready': True, 'total': 2, 'unresolved': 0, 'codes': {}, 'missing': ['x']}
    other = {'delivery_ready': False, 'total': None, 'unresolved': None, 'codes': {}, 'missing': [],
             'error': 'TimeoutError: a|b'}
    table = case_replay.render([
        {'case': 'a', 'school': 'S', 'college': 'C', 'saved': saved, 'replay': replay},
        {'case': 'b', 'school': 'S', 'college': 'D', 'saved': saved, 'replay': other}])
    assert table.startswith('Ready: saved 0/2, replay 1/2')
    assert '| a | S C | no / 2 / 1 | yes / 2 / 0 |  | 1 |' in table
    assert 'TimeoutError: a/b' in table


def test_asset_reads_come_from_the_case_only(tmp_path):
    medium = tmp_path / 'm.png'
    medium.write_bytes(b'png')
    replay = case_replay.ReplayContext(None, {ROOT + 'm.png': medium})

    async def read(url):
        response = await replay.request.get(url, timeout=1)
        return response.ok, await response.body()

    assert asyncio.run(read(ROOT + 'm.png')) == (True, b'png')
    assert asyncio.run(read(ROOT + 'other.png')) == (False, b'')
    assert replay.missing == [ROOT + 'other.png']


@pytest.mark.browser
def test_replay_serves_saved_pages_and_reports_unsaved_ones(tmp_path):
    from playwright.async_api import async_playwright

    case = case_replay.load_case(write_case(tmp_path / 'cases', 'a'))

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                return await case_replay.replay_case(case, browser, tmp_path / 'out')
            finally:
                await browser.close()

    result = asyncio.run(run())
    assert result['status'] != 'replay_error', result
    assert result['total'] == 2
    assert result['missing'] == []
