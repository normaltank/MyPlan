"""A field parser correction needs no fabricated counter change to preserve identity."""
import json

import pytest
from bs4 import BeautifulSoup

from server import identity_review
from server.detail_mecc import LayoutAwareTokenizer
from server.profile_content import scope_profile_html
from server.task_image_review import prepare_review_resume
from test_review_evidence_metadata import source, page, URL


@pytest.mark.parametrize('counter', [True, False])
def test_same_native_source_reparsed_title_preserves_existing_identity_decision(tmp_path, counter):
    root, attempt, audit, snapshot, _ = source(tmp_path)
    html = page(98, 450, '<table><tr><td>职称：</td><td>副教授(博导/硕导)</td></tr></table><p>完整履历。</p>')
    if not counter:
        soup = BeautifulSoup(html, 'html.parser')
        for node in soup.find_all('script'):
            node.decompose()
        for node in soup.select('span[id^="dynclicks_wbnews_"]'):
            node.decompose()
        html = str(soup)
    scoped, evidence, issue = scope_profile_html(html, 'article#profile', URL)
    assert issue is None
    bio = LayoutAwareTokenizer.get_clean_text(BeautifulSoup(scoped, 'lxml').body,
                                             preserve_content=True)
    original = {'姓名': '王芳', '主页': URL, '学校': '样本大学', '学院': '样本学院',
                '职称': '副教授', '邮箱': '', '简介': bio}
    roster = {**original, '主页': 'https://faculty.example.edu/list', '简介': ''}
    hashes = {URL: evidence['selected_html_sha256'], roster['主页']: 'a' * 64}
    previous = identity_review.pending_items([original, roster], hashes)[0]
    decision_path = attempt / 'identity_reviews.json'
    identity_review.save_decision(decision_path, previous, 'same', URL, reviewed_by=7)
    original_decision = decision_path.read_bytes()
    value = json.loads(audit.read_text(encoding='utf8'))
    value['identity_review_items'] = [previous]
    value['missing_profile_media_evidence'] = []
    value['coverage']['field_sources'][0].update(evidence)
    audit.write_text(json.dumps(value), encoding='utf8')
    snapshot.write_text(html, encoding='utf8')
    prepare_review_resume(audit, root.name)
    current = {**original, '职称': '副教授(博导/硕导)'}
    rows, pending, certificates = identity_review.apply_decisions(
        [current, roster], hashes, decision_path,
        source_html={URL: html}, source_evidence={URL: evidence})
    assert not pending and len(rows) == 1 and len(certificates) == 1
    assert certificates[0]['id'] == previous['id']
    assert certificates[0]['source_equivalence'][0]['basis'] == 'unchanged_native_selected_source'
    # All observations merged into one row, so no remaining distinct-URL pair exists.
    assert identity_review.validated_pairs(rows, json.loads(json.dumps(certificates)), decision_path) == set()
    tampered = json.loads(json.dumps(certificates))
    tampered[0]['source_equivalence'][0]['new_raw_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='source equivalence'):
        identity_review.validated_pairs(rows, tampered, decision_path)
    assert decision_path.read_bytes() == original_decision
    # Changed evidence cannot borrow the old identity assertion.
    changed = {**current, '职称': '教授'}
    _, pending, certificates = identity_review.apply_decisions(
        [changed, roster], hashes, decision_path,
        source_html={URL: html}, source_evidence={URL: evidence})
    assert pending and not certificates
