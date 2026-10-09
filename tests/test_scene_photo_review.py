"""Correcting a photo's background text cannot settle biography ownership."""
import json
from hashlib import sha256
from io import BytesIO

import pytest
from PIL import Image

from server import image_bio_review, media_kind_review
from server.review_registry import review_complete
from server.image_biography import audit_profile_media


def scene_audit(tmp_path):
    raw = BytesIO()
    Image.new("RGB", (1024, 682), "white").save(raw, format="PNG")
    original = tmp_path / "scene.png"
    original.write_bytes(raw.getvalue())
    digest = sha256(raw.getvalue()).hexdigest()
    url, media = "https://faculty.example.edu/p", "https://faculty.example.edu/scene.png"
    lines = [{"text": text, "confidence": .99, "box": [[10, 10], [500, 10], [500, 60], [10, 60]]}
             for text in ("测试大学", "外国语学院", "School of Languages")]
    ocr = tmp_path / "scene_ocr.json"
    ocr.write_text(json.dumps({"kind": "image", "source_sha256": digest,
        "text": "\n".join(line["text"] for line in lines), "page_count": 1,
        "pages_ocrd": 1, "pages": [{"page": 1, "source": "local_ocr", "lines": lines,
        "text": "\n".join(line["text"] for line in lines)}]}), encoding="utf-8")
    records = tmp_path / "records.json"
    records.write_text(json.dumps([{"姓名": "张富有", "主页": url}]), encoding="utf-8")
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({"records_path": str(records), "unresolved": [{
        "status": "image_bio_owner_unresolved", "kind": "image", "profile_url": url, "media_url": media}],
        "image_biography_evidence": [{"kind": "image", "profile_url": url, "media_url": media,
        "sha256": digest, "original_path": str(original), "ocr_path": str(ocr)}]}), encoding="utf-8")
    return audit


def test_scene_photo_has_whole_image_type_review_not_only_ocr_corrections(tmp_path):
    audit = scene_audit(tmp_path)
    items = media_kind_review.collect_items(audit)
    assert len(items) == 1
    assert Image.open(BytesIO(items[0]["preview_bytes"])).size == (1024, 682)


def test_corrected_background_lines_do_not_resolve_image_owner(tmp_path):
    audit = scene_audit(tmp_path)
    items = image_bio_review.collect_review_items(audit)
    for item in items:
        image_bio_review.save_review_decision(tmp_path / image_bio_review.TEXT_DECISIONS_FILE,
                                             item, item["ocr_text"])
    assert not image_bio_review.text_review_complete(audit, items)


@pytest.mark.asyncio
async def test_photo_confirmation_settles_background_owner_and_preserves_original_ocr(tmp_path):
    audit = scene_audit(tmp_path)
    assert not review_complete(audit)
    item = media_kind_review.collect_items(audit)[0]
    media_kind_review.save_decision(tmp_path / media_kind_review.DECISIONS_FILE, item, 'photo', 1)
    assert image_bio_review.collect_review_items(audit) == []
    assert review_complete(audit)
    payload = json.loads(audit.read_text(encoding='utf-8'))
    source = payload['image_biography_evidence'][0]
    raw = (tmp_path / 'scene.png').read_bytes()
    scanned = json.loads((tmp_path / 'scene_ocr.json').read_text(encoding='utf-8'))
    def ocr(_):
        return [(line['box'], line['text'], line['confidence']) for line in scanned['pages'][0]['lines']], None
    async def fetch(_):
        return raw
    result = await audit_profile_media({source['profile_url']: '<article><img src="/scene.png"></article>'},
        {source['profile_url']: {'张富有'}}, tmp_path / 'out', fetch_asset=fetch, ocr_engine=ocr,
        media_kind_decisions_path=tmp_path / media_kind_review.DECISIONS_FILE)
    assert not result['unresolved'] and not result['biographies']
    assert result['evidence'][0]['media_kind_review'] == 'photo'
    assert json.loads((tmp_path / 'scene_ocr.json').read_text(encoding='utf-8')) == scanned
    assert (tmp_path / 'scene.png').read_bytes() == raw


def test_real_biography_owner_can_be_settled_by_correcting_ocr_without_photo_choice(tmp_path):
    audit = scene_audit(tmp_path)
    path = tmp_path / 'scene_ocr.json'
    scanned = json.loads(path.read_text(encoding='utf-8'))
    scanned['pages'][0]['lines'][0]['text'] = '张付有 教授'
    path.write_text(json.dumps(scanned), encoding='utf-8')
    items = image_bio_review.collect_review_items(audit)
    for item in items:
        image_bio_review.save_review_decision(tmp_path / image_bio_review.TEXT_DECISIONS_FILE, item,
            '张富有 教授' if item['line_index'] == 0 else item['ocr_text'])
    assert image_bio_review.text_review_complete(audit, items)
    assert review_complete(audit)


def test_photo_decision_cannot_settle_another_profile_or_changed_asset(tmp_path):
    audit = scene_audit(tmp_path)
    item = media_kind_review.collect_items(audit)[0]
    media_kind_review.save_decision(tmp_path / media_kind_review.DECISIONS_FILE, item, 'photo', 1)
    payload = json.loads(audit.read_text(encoding='utf-8'))
    payload['image_biography_evidence'][0]['profile_url'] += '/other'
    payload['unresolved'][0]['profile_url'] += '/other'
    audit.write_text(json.dumps(payload), encoding='utf-8')
    assert not review_complete(audit)
    payload['image_biography_evidence'][0]['profile_url'] = item['profile_url']
    payload['unresolved'][0]['profile_url'] = item['profile_url']
    audit.write_text(json.dumps(payload), encoding='utf-8')
    (tmp_path / 'scene.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='original changed'):
        review_complete(audit)

