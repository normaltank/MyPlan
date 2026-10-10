"""A reviewed identity survives only a source-proved title truncation repair."""
import json

import pytest
from bs4 import BeautifulSoup

from server import identity_review
from server.detail_mecc import LayoutAwareTokenizer
from server.profile_content import scope_profile_html
from server.task_image_review import prepare_review_resume
from test_review_evidence_metadata import URL, page, source


@pytest.mark.parametrize("change", [None, "new_title", "old_title", "school", "college", "name",
                                    "url", "body", "conflict", "revoked"])
def test_identity_title_reparse_requires_same_declared_field_and_current_human_decision(tmp_path, change):
    root, attempt, audit, snapshot, _ = source(tmp_path)
    body = '<table><tr><td>职称：</td><td>副教授(博导/硕导)</td></tr></table><p>完整履历。</p>'
    if change == "conflict":
        body += '<p>职称：教授</p>'
    old_html, new_html = page(98, 450, body), page(13, 451, body)
    if change == "body":
        new_html = new_html.replace("完整履历。", "履历已改变。")
    old_scope, old_e, _ = scope_profile_html(old_html, "article#profile", URL)
    new_scope, new_e, _ = scope_profile_html(new_html, "article#profile", URL)
    bio = lambda html: LayoutAwareTokenizer.get_clean_text(BeautifulSoup(html, "lxml").body,
                                                          preserve_content=True)
    first = {"姓名": "王芳", "主页": URL, "学校": "样本大学", "学院": "样本学院",
             "职称": "教授" if change == "old_title" else "副教授", "邮箱": "", "简介": bio(old_scope)}
    second = {**first, "主页": "https://faculty.example.edu/list", "简介": ""}
    hashes = {URL: old_e["selected_html_sha256"], second["主页"]: "a" * 64}
    old_item = identity_review.pending_items([first, second], hashes)[0]
    decision_path = attempt / "identity_reviews.json"
    identity_review.save_decision(decision_path, old_item, "same", URL, reviewed_by=7)
    saved = decision_path.read_bytes()
    value = json.loads(audit.read_text(encoding="utf8"))
    value["identity_review_items"] = [old_item]
    value["missing_profile_media_evidence"] = []
    value["coverage"]["field_sources"][0].update(old_e)
    audit.write_text(json.dumps(value), encoding="utf8")
    snapshot.write_text(old_html, encoding="utf8")
    prepare_review_resume(audit, root.name)
    current = {**first, "职称": "副教授(博导/硕导)", "简介": bio(new_scope)}
    changed = {"new_title": ("职称", "教授"), "school": ("学校", "别的大学"),
               "college": ("学院", "别的学院"), "name": ("姓名", "李星"),
               "url": ("主页", URL + "?different=1")}.get(change)
    if changed:
        current[changed[0]] = changed[1]
    if change == "revoked":
        identity_review.save_decision(decision_path, old_item, "insufficient", reviewed_by=7)
        saved = decision_path.read_bytes()
    rows, pending, certificates = identity_review.apply_decisions(
        [current, second], {**hashes, URL: new_e["selected_html_sha256"]}, decision_path,
        source_html={URL: new_html}, source_evidence={URL: new_e})
    assert decision_path.read_bytes() == saved
    if change is not None:
        assert not certificates and len(rows) == 2
        if change != "name":
            assert pending
        return
    assert not pending and len(rows) == 1 and rows[0]["职称"] == current["职称"]
    assert certificates[0]["id"] == old_item["id"]
    proof = certificates[0]["source_equivalence"][0]
    assert proof["field_reparses"][0]["field"] == "职称"
    identity_review.validated_pairs(rows, json.loads(json.dumps(certificates)), decision_path)
    forged = json.loads(json.dumps(certificates))
    forged[0]["source_equivalence"][0]["field_reparses"][0]["new_value"] = "教授"
    with pytest.raises(ValueError, match="equivalence"):
        identity_review.validated_pairs(rows, forged, decision_path)
