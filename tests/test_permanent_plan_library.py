import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from server import recon_plan_cache as module
from server.recon_plan_cache import ReconPlanCache

PLAN = {'school': '样本大学', 'college': '样本学院', 'source_kind': 'university_faculty_roster',
        'branches': [{'url': 'https://example.edu/list', 'card_selector': 'main li', 'single_page': True}]}
URL = 'https://example.edu/'
PAGES = [{'url': URL, 'links': ['https://example.edu/list'], 'fingerprint': 'a' * 64}]


def legacy_file(directory, *, saved_at=1000, invalid=False, contract=None):
    value = {'contract': contract or module._contract(), 'saved_at': saved_at, 'entry_url': URL,
             'plan': copy.deepcopy(PLAN), 'discovery_pages': PAGES}
    if invalid:
        value['plan']['teacher_name'] = 'forbidden faculty field'
    value['sha256'] = module._payload_digest(value)
    directory.mkdir(parents=True, exist_ok=True)
    path = ReconPlanCache(directory)._path(PLAN['school'], PLAN['college'], URL)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    return path


def test_default_plan_survives_a_year_and_new_instances(tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    cache = ReconPlanCache(tmp_path)
    cache.put(PLAN, URL, PAGES)
    monkeypatch.setattr(module.time, 'time', lambda: 1000 + 365 * 86400)
    entry = ReconPlanCache(tmp_path).get(PLAN['school'], PLAN['college'], URL)
    assert entry is not None and entry['plan'] == PLAN and entry['saved_at'] == 1000
    assert (tmp_path / 'plans.sqlite3').is_file()


def test_keeps_all_versions_and_returns_the_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    cache = ReconPlanCache(tmp_path)
    cache.put(PLAN, URL, PAGES)
    newer = copy.deepcopy(PLAN)
    newer['branches'][0]['card_selector'] = 'article li'
    monkeypatch.setattr(module.time, 'time', lambda: 2000)
    cache.put(newer, URL, PAGES)
    assert ReconPlanCache(tmp_path).get(PLAN['school'], PLAN['college'], URL)['plan'] == newer
    with sqlite3.connect(tmp_path / 'plans.sqlite3') as db:
        versions = db.execute('SELECT payload_json FROM plan_versions ORDER BY version_id').fetchall()
    assert [json.loads(row[0])['plan'] for row in versions] == [PLAN, newer]


def test_repeated_identical_plan_keeps_one_version_and_original_time(tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, 'time', lambda: 1000)
    cache = ReconPlanCache(tmp_path)
    cache.put(PLAN, URL, PAGES)
    monkeypatch.setattr(module.time, 'time', lambda: 2000)
    cache.put(PLAN, URL, PAGES)
    assert cache.get(PLAN['school'], PLAN['college'], URL)['saved_at'] == 1000
    with sqlite3.connect(tmp_path / 'plans.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM plan_versions').fetchone()[0] == 1


def test_reactivated_historical_structure_becomes_the_latest_candidate(tmp_path, monkeypatch):
    cache = ReconPlanCache(tmp_path)
    alternative = copy.deepcopy(PLAN)
    alternative['branches'][0]['card_selector'] = 'article li'
    for instant, plan in [(1000, PLAN), (2000, alternative), (3000, PLAN)]:
        monkeypatch.setattr(module.time, 'time', lambda instant=instant: instant)
        cache.put(plan, URL, PAGES)
    assert cache.get(PLAN['school'], PLAN['college'], URL)['plan'] == PLAN
    with sqlite3.connect(tmp_path / 'plans.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM plan_versions').fetchone()[0] == 2


def test_imports_old_json_once_preserving_time_and_original_file(tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, 'time', lambda: 1000 + 365 * 86400)
    path = legacy_file(tmp_path)
    original = path.read_bytes()
    for _ in range(3):
        entry = ReconPlanCache(tmp_path).get(PLAN['school'], PLAN['college'], URL)
        assert entry is not None and entry['saved_at'] == 1000
    assert path.read_bytes() == original
    with sqlite3.connect(tmp_path / 'plans.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM plan_versions').fetchone()[0] == 1


@pytest.mark.parametrize('defect', ['teacher_field', 'bad_digest', 'bad_json', 'bad_contract', 'huge_timestamp'])
def test_does_not_import_invalid_legacy_files(tmp_path, defect):
    path = legacy_file(tmp_path, invalid=defect == 'teacher_field')
    if defect == 'bad_digest':
        value = json.loads(path.read_text(encoding='utf-8'))
        value['sha256'] = '0' * 64
        path.write_text(json.dumps(value), encoding='utf-8')
    elif defect == 'bad_json':
        path.write_text('{', encoding='utf-8')
    elif defect == 'bad_contract':
        value = json.loads(path.read_text(encoding='utf-8'))
        value['contract'] = 'untrusted page text'
        value['sha256'] = module._payload_digest({k: v for k, v in value.items() if k != 'sha256'})
        path.write_text(json.dumps(value), encoding='utf-8')
    elif defect == 'huge_timestamp':
        value = json.loads(path.read_text(encoding='utf-8'))
        value['saved_at'] = 10 ** 400
        value['sha256'] = module._payload_digest({k: v for k, v in value.items() if k != 'sha256'})
        path.write_text(json.dumps(value), encoding='utf-8')
    cache = ReconPlanCache(tmp_path)
    assert cache.get(PLAN['school'], PLAN['college'], URL) is None
    assert path.exists()
    with sqlite3.connect(tmp_path / 'plans.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM plan_versions').fetchone()[0] == 0


def test_archives_old_contract_but_never_reuses_it(tmp_path):
    path = legacy_file(tmp_path, contract='0' * 64)
    cache = ReconPlanCache(tmp_path)
    assert cache.get(PLAN['school'], PLAN['college'], URL) is None
    with sqlite3.connect(tmp_path / 'plans.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM plan_versions').fetchone()[0] == 1
    assert path.exists()


def test_concurrent_college_plans_are_not_lost(tmp_path):
    def save(index):
        plan = {**PLAN, 'college': f'学院 {index}'}
        ReconPlanCache(tmp_path).put(plan, URL, PAGES)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save, range(16)))
    cache = ReconPlanCache(tmp_path)
    assert all(cache.get(PLAN['school'], f'学院 {index}', URL) for index in range(16))
    with sqlite3.connect(tmp_path / 'plans.sqlite3') as db:
        assert db.execute('SELECT count(*) FROM plan_versions').fetchone()[0] == 16


def test_saves_only_compact_validation_metadata(tmp_path):
    cache = ReconPlanCache(tmp_path)
    cache.put(PLAN, URL, PAGES)
    entry = cache.get(PLAN['school'], PLAN['college'], URL)
    cache.record_validation(entry, {'valid': True, 'events': [{'teacher_name': 'not for storage'}]})
    cache.record_validation(entry, {'valid': False, 'reason': 'discovery_changed', 'issues': ['raw page data']})
    with sqlite3.connect(tmp_path / 'plans.sqlite3') as db:
        rows = db.execute('SELECT valid,reason FROM validation_events ORDER BY validation_id').fetchall()
    assert rows == [(1, 'verified'), (0, 'discovery_changed')]
    assert cache.get(PLAN['school'], PLAN['college'], URL) is not None
