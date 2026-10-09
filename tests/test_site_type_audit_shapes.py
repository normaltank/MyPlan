import hashlib
import json
import sqlite3

import pytest

from server.site_type_registry import SiteTypeRegistry, record_audit_shapes


def audit_file(tmp_path):
    original = tmp_path / 'source.pdf'
    original.write_bytes(b'%PDF-1.4 saved evidence')
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    audit = {'school': '测试大学', 'college': '测试学院',
             'pdf_roster_evidence': [{'media_url': 'https://example.edu/list.pdf',
                                      'original_path': str(original), 'sha256': digest}],
             'roster_observations': [{'kind': 'pdf_roster', 'layout': 'rank_grid',
                                      'url': 'https://example.edu/list',
                                      'media_url': 'https://example.edu/list.pdf', 'sha256': digest,
                                      'page_count': 1, 'records_detected': 8, 'records_admitted': 7,
                                      'sections': [{'heading': '不得进入特征的原文'}]}]}
    path = tmp_path / 'audit.json'
    path.write_text(json.dumps(audit), encoding='utf-8')
    return path, original


def test_shape_recording_is_observed_and_has_only_structure_features(tmp_path):
    path, _ = audit_file(tmp_path)
    registry = SiteTypeRegistry(tmp_path / 'types.sqlite3')
    assert len(record_audit_shapes(path, registry)) == 1
    with sqlite3.connect(registry.db_path) as con:
        [row] = con.execute('select shape_code,status,features_json from site_type_observations').fetchall()
    assert row[:2] == ('pdf_rank_grouped_name_grid', 'observed')
    assert '不得' not in row[2]
    assert json.loads(row[2])['records_admitted'] == 7
    assert len(record_audit_shapes(path, registry)) == 1


def test_modified_original_cannot_be_registered_as_that_observation(tmp_path):
    path, original = audit_file(tmp_path)
    registry = SiteTypeRegistry(tmp_path / 'types.sqlite3')
    original.write_bytes(b'different bytes')
    with pytest.raises(ValueError, match='changed'):
        record_audit_shapes(path, registry)
    with sqlite3.connect(registry.db_path) as con:
        assert con.execute('select count(*) from site_type_observations').fetchone()[0] == 0
