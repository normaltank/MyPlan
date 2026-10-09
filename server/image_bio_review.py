"""Local human review of uncertain OCR lines, bound to exact source evidence.

The reviewer sees local evidence directly. No teacher text is sent to the
reconnaissance model, and the original OCR JSON is never modified.
"""

from __future__ import annotations

import base64
import copy
from hashlib import sha256
from html import escape
from io import BytesIO
import json
from pathlib import Path
import time

from PIL import Image

from server import review_store

TEXT_STATUSES = frozenset({"image_bio_low_confidence", "image_bio_owner_unresolved", "segment_name_needs_review"})
EMAIL_STATUS = "image_bio_email_conflict"
TEXT_DECISIONS_FILE = "image_bio_reviews.json"
EMAIL_DECISIONS_FILE = "image_bio_email_reviews.json"


def _text_digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _item_id(media_url: str, source_sha256: str, page: int, line_index: int,
             ocr_text: str) -> str:
    source = json.dumps([media_url, source_sha256, page, line_index,
                         _text_digest(ocr_text)], ensure_ascii=False,
                        separators=(",", ":"))
    return sha256(source.encode("utf-8")).hexdigest()


def _inside(path: str | Path, root: Path) -> Path:
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("review evidence path escapes its audit directory")
    return resolved


def _crop_original(original: bytes, kind: str, page: int, box: list) -> bytes:
    if kind == "pdf":
        import pymupdf
        document = pymupdf.open(stream=original, filetype="pdf")
        if not 1 <= page <= document.page_count:
            raise ValueError("review page outside PDF")
        pixmap = document[page - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        image = Image.open(BytesIO(pixmap.tobytes("png")))
    else:
        image = Image.open(BytesIO(original))
    image = image.convert("RGB")
    try:
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        left = max(0, int(min(xs)) - 12)
        top = max(0, int(min(ys)) - 12)
        right = min(image.width, int(max(xs)) + 12)
        bottom = min(image.height, int(max(ys)) + 12)
    except (TypeError, ValueError, IndexError) as exc:
        raise ValueError("OCR line has no usable source coordinates") from exc
    if right <= left or bottom <= top:
        raise ValueError("OCR line crop is empty")
    buffer = BytesIO()
    image.crop((left, top, right, bottom)).save(buffer, format="PNG")
    return buffer.getvalue()


def collect_review_items(audit_path: str | Path, *, confidence_floor: float = .8) -> list[dict]:
    """Return uncertain OCR or unmatched owner headings from exact local evidence."""
    audit_path = Path(audit_path).resolve()
    root = audit_path.parent
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    owner_urls = {item.get("media_url") for item in audit.get("unresolved", [])
                  if item.get("status") == "image_bio_owner_unresolved"}
    flagged_urls = owner_urls | {item.get("media_url") for item in audit.get("unresolved", [])
                                if item.get("status") == "image_bio_low_confidence"}
    result: list[dict] = []
    for evidence in audit.get("image_biography_evidence", []):
        media_url = evidence.get("media_url")
        if media_url not in flagged_urls:
            continue
        original_path = _inside(evidence["original_path"], root)
        ocr_path = _inside(evidence["ocr_path"], root)
        original = original_path.read_bytes()
        digest = sha256(original).hexdigest()
        if digest != evidence["sha256"]:
            raise ValueError("review original SHA-256 mismatch")
        scanned = json.loads(ocr_path.read_text(encoding="utf-8"))
        if scanned.get("source_sha256") not in (None, digest):
            raise ValueError("review OCR source SHA-256 mismatch")
        if any((line.get("confidence", 1) < confidence_floor or media_url in owner_urls) and not line.get("box")
               for page in scanned["pages"] for line in page["lines"]):
            from server.image_biography import scan_media
            rescan_path = root / "review-rescans" / f"{digest}_{evidence['kind']}_ocr.json"
            try:
                refreshed = json.loads(rescan_path.read_text(encoding="utf-8"))
                if refreshed.get("source_sha256") != digest:
                    raise ValueError("stale review rescan")
            except (OSError, ValueError, TypeError):
                refreshed = scan_media(original, evidence["kind"])
                refreshed["source_sha256"] = digest
                rescan_path.parent.mkdir(exist_ok=True)
                rescan_path.write_text(json.dumps(refreshed, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
            scanned = refreshed
        owner_headers = {(page['page'], index) for page, index in [
            (page, index) for page in scanned['pages'] for index, _ in enumerate(page['lines'])][:5]}
        for page in scanned["pages"]:
            page_number = page["page"]
            for line_index, line in enumerate(page["lines"]):
                owner_header = media_url in owner_urls and (page_number, line_index) in owner_headers
                if line["confidence"] >= confidence_floor and not owner_header:
                    continue
                if not line.get("box"):
                    raise ValueError("OCR line lacks coordinates; refresh this image before review")
                result.append({
                    "id": _item_id(media_url, digest, page_number, line_index, line["text"]),
                    "media_url": media_url, "source_sha256": digest,
                    "page": page_number, "line_index": line_index,
                    "ocr_text": line["text"], "ocr_text_sha256": _text_digest(line["text"]),
                    "confidence": line["confidence"],
                    **({"purpose": "image_owner"} if owner_header else {}),
                    "crop_bytes": _crop_original(original, evidence["kind"], page_number,
                                                 line["box"]),
                })
    result.extend(_segment_name_items(audit, root, {(item["media_url"], item["page"], item["line_index"])
                                                    for item in result}))
    return result


def _segment_name_items(audit: dict, root: Path, listed: set) -> list[dict]:
    """The exact OCR line a segmented-roster record name was read from.

    Such a line needs a person even when OCR was confident, because the name
    could not be cross-checked against the record's own email.
    """
    flagged = {(item.get("media_url"), item.get("page"), item.get("line_index"))
               for item in audit.get("unresolved", [])
               if item.get("status") == "segment_name_needs_review"}
    items = []
    for evidence in audit.get("segment_name_evidence", []):
        key = (evidence.get("media_url"), evidence.get("name_page"), evidence.get("name_line_index"))
        if key not in flagged or key in listed:
            continue
        original = _inside(evidence["original_path"], root).read_bytes()
        digest = sha256(original).hexdigest()
        if digest != evidence["sha256"]:
            raise ValueError("review original SHA-256 mismatch")
        scanned = json.loads(_inside(evidence["ocr_path"], root).read_text(encoding="utf-8"))
        if scanned.get("source_sha256") not in (None, digest):
            raise ValueError("review OCR source SHA-256 mismatch")
        page = next(page for page in scanned["pages"] if page["page"] == key[1])
        line = page["lines"][key[2]]
        if not line.get("box"):
            raise ValueError("OCR line lacks coordinates; refresh this image before review")
        listed.add(key)
        items.append({
            "id": _item_id(key[0], digest, key[1], key[2], line["text"]),
            "media_url": key[0], "source_sha256": digest, "page": key[1], "line_index": key[2],
            "ocr_text": line["text"], "ocr_text_sha256": _text_digest(line["text"]),
            "confidence": line["confidence"], "purpose": "segment_name",
            "crop_bytes": _crop_original(original, evidence.get("kind", "image"), key[1], line["box"]),
        })
    return items


_REVIEW_CSS = (
    "body{max-width:880px;margin:0 auto;padding:24px 16px 48px}"
    "h1{font-size:22px;margin:4px 0 6px}"
    "body>h2{font-size:15px;color:var(--muted);margin:28px 0 0}"
    ".card h2{font-size:16px;margin-bottom:8px}"
    ".intro,.back{color:var(--muted)}"
    ".card{padding:20px;margin:14px 0;display:grid;gap:10px}"
    ".card img{max-width:100%;display:block;border:1px solid var(--border);border-radius:6px}"
    ".card label{display:grid;gap:6px;font-weight:600;color:var(--text-2)}"
    "textarea,.name-card input{width:100%;padding:10px 12px;border:1px solid var(--border-strong);"
    "border-radius:var(--radius-sm);font:inherit;font-weight:400;color:var(--text);background:var(--surface)}"
    "textarea:focus,.name-card input:focus{outline:none;border-color:var(--accent);box-shadow:var(--ring)}"
    "button{justify-self:start;height:38px;padding:0 16px;border:0;border-radius:var(--radius-sm);"
    "background:var(--accent);color:#fff;font:inherit;font-weight:600;cursor:pointer}"
    "button:hover{background:var(--accent-hover)}button:disabled{opacity:.55;cursor:not-allowed}"
    "button.secondary{background:var(--surface);color:var(--text-2);border:1px solid var(--border-strong)}"
    "button.secondary:hover{background:var(--surface-2)}"
    ".actions{display:flex;flex-wrap:wrap;gap:8px}"
    "body>h2+p{color:var(--muted);margin-top:4px}"
    ".meta{color:var(--muted);font-size:13px}.status{font-size:13px;color:var(--ok)}.status:empty{display:none}"
    ".email-option{display:grid;grid-template-columns:auto 1fr;align-items:center;gap:8px 10px;padding:12px;"
    "border:1px solid var(--border);border-radius:var(--radius-sm);font-weight:400!important;cursor:pointer}"
    ".email-option img,.email-option small{grid-column:2}"
    ".email-option:has(input:checked){border-color:var(--accent);background:var(--accent-soft)}"
    "blockquote{margin:0;padding:10px 12px;background:var(--surface-2);border:1px solid var(--border);border-radius:var(--radius-sm);color:var(--text-2)}"
    "#retry-review{height:44px;margin-top:20px;background:var(--ok)}#retry-review:hover{background:#065f46}"
    "#retry-status{color:var(--text-2);margin-top:8px}"
)


def _base_css() -> str:
    return (Path(__file__).with_name("templates") / "base.css").read_text(encoding="utf-8")


def _page_link(url: str) -> str:
    if url.startswith(("http://", "https://")):
        return ('<a href="' + escape(url, quote=True) + '" target="_blank" rel="noopener noreferrer">'
                + escape(url) + '</a>')
    return escape(url)


def _name_cards(name_items: list[dict]) -> list[str]:
    cards = []
    for item in name_items:
        profile = item.get("profile_url") or ""
        cards.append(
            '<section class="card name-card" data-id="' + escape(item["id"]) + '">'
            '<h2>姓名待确认：' + escape(item["name"]) + '</h2>'
            '<p class="meta">名录页 ' + _page_link(item["url"])
            + ('' if profile == item["url"] else ' · 个人页 ' + _page_link(profile)) + '</p>'
            '<p>卡片原文：</p><blockquote>' + escape(item.get("card_text", "")[:300]) + '</blockquote>'
            '<label>教师姓名（可改正）<input type="text" maxlength="40" value="'
            + escape(item["name"], quote=True) + '"></label>'
            '<div class="actions"><button type="button" data-verdict="person">是教师，按此姓名采集</button>'
            '<button type="button" data-verdict="not_person" class="secondary">不是教师姓名</button></div>'
            '<p class="status" role="status"></p></section>')
    return cards


def _identity_cards(items: list[dict], allowed: bool) -> str:
    cards = []
    for item in items:
        observations = item['observations']
        options = ''.join('<option value="' + escape(o['url'], quote=True) + '">'
                          + escape(o['url']) + '</option>' for o in observations)
        sources = []
        for o in observations:
            row = o['record']
            from server.faculty_identity import field_value
            choices = ''.join('<option value="' + escape(p['url'], quote=True) + '"'
                              + (' selected' if p['url'] == o['url'] else '') + '>'
                              + escape(p['url']) + '</option>' for p in observations)
            sources.append('<div class="identity-source" data-url="' + escape(o['url'], quote=True) + '">'
                + '<p>来源：' + _page_link(o['url']) + '</p><p>职称：' + escape(field_value(row, '职称'))
                + ' · 邮箱：' + escape(field_value(row, '邮箱')) + '</p>'
                + '<details><summary>完整简介原文</summary><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'
                + escape(field_value(row, '简介')) + '</pre></details>'
                + '<label>这条观察属于哪个主来源代表的教师？<select>' + choices + '</select></label></div>')
        buttons = ('<div class="actions"><button data-verdict="distinct" type="button">不同教师，全部保留</button>'
                   '<button data-verdict="same" type="button">同一教师，合并来源</button>'
                   '<button data-verdict="partition" type="button">按各条选择分组</button>'
                   '<button data-verdict="insufficient" type="button" class="secondary">证据不足，保持阻断</button></div>')
        status = {'distinct': '已确认不同教师', 'same': '已确认同一教师', 'partition': '已保存来源分组',
                  'insufficient': '证据不足，仍不可交付'}.get(item.get('saved_verdict'), '')
        cards.append('<section class="card identity-card" data-id="' + escape(item['id']) + '"><h2>同名身份复核：'
            + escape(item['name']) + '</h2><p>不同网址不能证明是不同教师。同一人合并时，邮箱、职称与主页取主来源；'
            + '各来源的完整简介都保留，原始观察另存审计。</p>' + ''.join(sources)
            + '<label>全部合并时使用的主来源<select class="primary">' + options + '</select></label>'
            + (buttons if allowed else '<p>请管理员核对来源并确认身份。</p>')
            + '<p class="status" role="status">' + status + '</p></section>')
    return ''.join(cards)


_HTML_EMAIL_REASONS = {
    "shared_contact": "多位教师处同时出现，属公共或共享地址",
    "in_other_teacher_scope": "也出现在其他教师的卡片或个人页",
    "assigned_to_other_record": "已是其他教师的邮箱",
    "spells_other_teacher": "拼写对应其他教师姓名",
    "also_spells_other_teacher": "拼写也对应其他教师姓名",
}


def _html_email_cards(items: list[dict]) -> str:
    cards = []
    for item in items:
        options = []
        first = True
        for option in item["candidates"]:
            reasons = "；".join(_HTML_EMAIL_REASONS.get(r, r) for r in option["reasons"])
            contexts = "".join("<blockquote>…" + escape(text) + "…</blockquote>"
                               for text in option["card_context"] + option["page_context"])
            checked = " checked" if option["selectable"] and first else ""
            first = first and not option["selectable"]
            options.append('<label class="email-option"><input type="radio" name="html-email-'
                           + escape(item["id"]) + '" value="' + escape(option["email"], quote=True) + '"'
                           + checked + ('' if option["selectable"] else ' disabled') + '> '
                           + escape(option["email"])
                           + ('<small>不能选择：' + escape(reasons) + '</small>' if not option["selectable"]
                              else '<small>' + escape(reasons) + '</small>' if reasons else '')
                           + (contexts or '<small>原文中以变体形式出现，请打开来源页核对。</small>')
                           + '</label>')
        buttons = ('<button type="button" data-verdict="select">保存所选邮箱</button>'
                   if any(o["selectable"] for o in item["candidates"]) else '')
        if item["none_allowed"]:
            buttons += ('<button type="button" data-verdict="none" class="secondary">'
                        '确认此教师无个人邮箱</button>')
        if not buttons:
            buttons = '<p>没有可归属的候选地址，也没有证明无个人邮箱的依据；请管理员核对来源。</p>'
        status = {"select": "已保存邮箱：" + item.get("saved_email", ""), "none": "已确认无个人邮箱"}.get(
            item.get("saved_verdict", ""), "")
        cards.append('<section class="card html-email-card" data-id="' + escape(item["id"]) + '">'
                     '<h2>邮箱归属复核：' + escape(item["name"]) + '</h2>'
                     '<p class="meta">来源页 ' + _page_link(item["url"])
                     + (' · 多人共享页' if item.get("shared_page") else '') + '</p>'
                     '<p>名录卡片原文：</p><blockquote>' + escape(item.get("card_text", "")[:300]) + '</blockquote>'
                     '<p>程序无法确定哪个地址属于本人。请对照下列原文选择；只能选择页面上实际出现、'
                     '且没有指向他人证据的地址。</p>' + "".join(options)
                     + '<div class="actions">' + buttons + '</div>'
                     '<p class="status" role="status">' + escape(status) + '</p></section>')
    return "".join(cards)


def render_review_page(items: list[dict], email_items: list[dict] | None = None, *,
                       decision_url: str = '/decision',
                       email_decision_url: str = '/email-decision',
                       csrf_token: str = '', retry_url: str = '',
                       return_url: str = '/',
                       name_items: list[dict] | None = None,
                       name_decision_url: str = '', identity_items: list[dict] | None = None,
                       identity_decision_url: str = '', can_review_identity: bool = True,
                       html_email_items: list[dict] | None = None,
                       html_email_decision_url: str = '', media_kind_items: list[dict] | None = None,
                       media_kind_decision_url: str = '') -> str:
    cards = []
    for item in items:
        crop = base64.b64encode(item["crop_bytes"]).decode("ascii")
        cards.append(
            '<section class="card" data-id="' + escape(item["id"]) + '">'
            '<p class="meta">' + ('姓名行（请核对并改正为原图中的姓名） · ' if item.get("purpose") == "segment_name" else '')
            + ('图文归属（只按原图核对标题或姓名，不可推测） · ' if item.get("purpose") == "image_owner" else '')
            + '第 ' + str(item["page"]) + ' 页 · 置信度 '
            + f'{item["confidence"]:.2f}' + '</p>'
            '<img alt="待核对的原图局部" src="data:image/png;base64,' + crop + '">'
            '<label>OCR 原文<textarea rows="2">' + escape(item["ocr_text"]) + '</textarea></label>'
            '<button type="button">确认这行文字</button><p class="status" role="status"></p>'
            '</section>')
    body = "\n".join(cards)
    for item in media_kind_items or []:
        preview = base64.b64encode(item['preview_bytes']).decode('ascii')
        status = {'photo': '已确认纯人物照片', 'unreadable': '包含文字或无法确定，仍保持阻断'}.get(
            item.get('saved_verdict'), '')
        body += ('<section class="card media-kind-card" data-id="' + escape(item['id']) + '">'
            '<h2>原图类型复核</h2><p>程序无法判断这是照片还是未读出的简历。请检查整张原图：'
            '只有纯人物照片、没有履历或其他文字时才能确认照片。含文字、扫描件或无法判断时保持阻断。</p>'
            '<p>来源：' + _page_link(item['profile_url']) + '</p>'
            '<img alt="需要判断类型的完整原图" src="data:image/png;base64,' + preview + '">'
            '<button type="button" data-verdict="photo">确认纯人物照片，无履历文字</button> '
            '<button type="button" data-verdict="unreadable">含文字或无法判断，保持阻断</button>'
            '<p class="status" role="status">' + status + '</p></section>')
    email_cards = []
    for item in email_items or []:
        choices = []
        for index, (choice, crop_bytes) in enumerate(zip(item["choices"], item["crops"])):
            crop = (('<img alt="邮箱所在原图局部" src="data:image/png;base64,'
                     + base64.b64encode(crop_bytes).decode("ascii") + '">')
                    if crop_bytes else '<small>此候选来自网页正文。</small>')
            choices.append('<label class="email-option"><input type="radio" name="email-'
                           + escape(item["id"]) + '" value="' + escape(choice, quote=True)
                           + '"' + (' checked' if index == 0 else '') + '> '
                           + escape(choice) + crop + '</label>')
        email_cards.append('<section class="card email-card" data-id="' + escape(item["id"])
                           + '"><h2>邮箱归属复核：' + escape(item["owner_name"])
                           + '</h2><p>请对照来源证据选择交付表使用的邮箱；完整简介仍保留原文。</p>'
                           + "".join(choices)
                           + '<button type="button">保存邮箱选择</button><p class="status" role="status"></p>'
                           + '</section>')
    if email_cards:
        body += '<h2>邮箱冲突</h2>' + "\n".join(email_cards)
    if html_email_items:
        body += '<h2>网页邮箱归属</h2>' + _html_email_cards(html_email_items)
    name_cards = _name_cards(name_items or [])
    if name_cards:
        body += ('<h2>姓名待确认</h2><p>这些名字的姓不在常用姓氏表里，或是外籍译名等程序认不出的名字，'
                 '也可能是普通词语。请打开名录页核对。</p>' + "\n".join(name_cards))
    if not body and not identity_items:
        body = '<p>当前没有需要人工复核的图片文字、邮箱或姓名。</p>'
    if identity_items:
        body += '<h2>来源身份</h2>' + _identity_cards(identity_items, can_review_identity)
    decision_js = json.dumps(decision_url).replace('<', '\\u003c')
    email_js = json.dumps(email_decision_url).replace('<', '\\u003c')
    name_js = json.dumps(name_decision_url).replace('<', '\\u003c')
    identity_js = json.dumps(identity_decision_url).replace('<', '\\u003c')
    html_email_js = json.dumps(html_email_decision_url).replace('<', '\\u003c')
    media_kind_js = json.dumps(media_kind_decision_url).replace('<', '\\u003c')
    retry_js = json.dumps(retry_url).replace('<', '\\u003c')
    csrf_js = json.dumps(csrf_token).replace('<', '\\u003c')
    footer = ''
    if retry_url:
        footer += ('<button id="retry-review" type="button">复核完成，重新采集此工单</button>'
                   '<p id="retry-status" role="status"></p>')
    return ("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>人工复核 · 高校师资采集平台</title><style>" + _base_css() + _REVIEW_CSS + "</style>"
            '<p class="back"><a href="' + escape(return_url, quote=True) + '">← 返回采集平台</a></p>'
            "<h1>人工复核</h1><p class=\"intro\">对照原图或名录页，确认或更正。决定仅对完全相同的证据生效。</p>"
            + body + footer + "<script>const reviewCsrf=" + csrf_js + ";"
            # Show the server's own message, not its raw JSON body.
            "async function failure(response){const text=await response.text();"
            "try{const detail=JSON.parse(text).detail;if(typeof detail==='string')return new Error(detail);}catch(_){}"
            "return new Error(text||('HTTP '+response.status));}"
            "document.querySelectorAll('.name-card').forEach(card=>{"
            "card.querySelectorAll('button').forEach(button=>button.onclick=async()=>{"
            "const status=card.querySelector('.status');"
            "const verdict=button.dataset.verdict;const verified_name=card.querySelector('input').value;"
            "try{const response=await fetch(" + name_js + ",{method:'POST',"
            "headers:{'Content-Type':'application/json','X-CSRF-Token':reviewCsrf},"
            "body:JSON.stringify({id:card.dataset.id,verdict,verified_name})});"
            "if(!response.ok)throw await failure(response);"
            "status.textContent=verdict==='person'?'已确认为教师':'已标记为不是教师姓名';}"
            "catch(error){status.textContent='保存失败：'+error.message;}});});"
            "document.querySelectorAll('.identity-card').forEach(card=>{"
            "card.querySelectorAll('button').forEach(button=>button.onclick=async()=>{"
            "const status=card.querySelector('.status');const verdict=button.dataset.verdict;"
            "const primary_url=card.querySelector('.primary').value;"
            "const assignments=Object.fromEntries(Array.from(card.querySelectorAll('.identity-source'))"
            ".map(source=>[source.dataset.url,source.querySelector('select').value]));"
            "try{const response=await fetch(" + identity_js + ",{method:'POST',"
            "headers:{'Content-Type':'application/json','X-CSRF-Token':reviewCsrf},"
            "body:JSON.stringify({id:card.dataset.id,verdict,primary_url,assignments})});"
            "if(!response.ok)throw await failure(response);"
            "status.textContent=verdict==='insufficient'?'证据不足，保持阻断':'已保存身份决定；来源变化后须重新复核';}"
            "catch(error){status.textContent='保存失败：'+error.message;}});});"
            "document.querySelectorAll('.html-email-card').forEach(card=>{"
            "card.querySelectorAll('button').forEach(button=>button.onclick=async()=>{"
            "const status=card.querySelector('.status');const verdict=button.dataset.verdict;"
            "const selected_email=verdict==='select'?(card.querySelector('input:checked')?.value||''):'';"
            "try{const response=await fetch(" + html_email_js + ",{method:'POST',"
            "headers:{'Content-Type':'application/json','X-CSRF-Token':reviewCsrf},"
            "body:JSON.stringify({id:card.dataset.id,verdict,selected_email})});"
            "if(!response.ok)throw await failure(response);"
            "status.textContent=verdict==='none'?'已确认无个人邮箱':'已保存邮箱：'+selected_email;}"
            "catch(error){status.textContent='保存失败：'+error.message;}});});"
            "document.querySelectorAll('.media-kind-card').forEach(card=>{"
            "card.querySelectorAll('button').forEach(button=>button.onclick=async()=>{"
            "const status=card.querySelector('.status');const verdict=button.dataset.verdict;"
            "try{const response=await fetch(" + media_kind_js + ",{method:'POST',"
            "headers:{'Content-Type':'application/json','X-CSRF-Token':reviewCsrf},"
            "body:JSON.stringify({id:card.dataset.id,verdict})});"
            "if(!response.ok)throw await failure(response);"
            "status.textContent=verdict==='photo'?'已确认纯人物照片；重新采集时校验原件':'保持阻断，请处理原图文字';}"
            "catch(error){status.textContent='保存失败：'+error.message;}});});"
            "document.querySelectorAll('.card:not(.email-card):not(.name-card):not(.identity-card):not(.html-email-card):not(.media-kind-card)').forEach(card=>{"
            "card.querySelector('button').onclick=async()=>{const status=card.querySelector('.status');"
            "const verified_text=card.querySelector('textarea').value;"
            "try{const response=await fetch(" + decision_js + ",{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':reviewCsrf},"
            "body:JSON.stringify({id:card.dataset.id,verified_text})});"
            "if(!response.ok)throw await failure(response);status.textContent='已保存复核决定';}"
            "catch(error){status.textContent='保存失败：'+error.message;}}});"
            "document.querySelectorAll('.email-card').forEach(card=>{"
            "card.querySelector('button').onclick=async()=>{const status=card.querySelector('.status');"
            "const selected_email=card.querySelector('input:checked')?.value;"
            "try{const response=await fetch(" + email_js + ",{method:'POST',"
            "headers:{'Content-Type':'application/json','X-CSRF-Token':reviewCsrf},"
            "body:JSON.stringify({id:card.dataset.id,selected_email})});"
            "if(!response.ok)throw await failure(response);status.textContent='已保存邮箱选择';}"
            "catch(error){status.textContent='保存失败：'+error.message;}}});"
            "const retry=document.getElementById('retry-review');if(retry)retry.onclick=async()=>{"
            "const status=document.getElementById('retry-status');retry.disabled=true;"
            "try{const response=await fetch(" + retry_js + ",{method:'POST',headers:{'X-CSRF-Token':reviewCsrf}});"
            "if(!response.ok)throw await failure(response);status.textContent='已重新入队，请返回平台查看进度';}"
            "catch(error){retry.disabled=false;status.textContent='未能重试：'+error.message;}};"
            "</script></html>")


def save_review_decision(path: str | Path, item: dict, verified_text: str) -> dict:
    if (not isinstance(verified_text, str) or not verified_text.strip()
            or len(verified_text) > 3000 or "\n" in verified_text or "\r" in verified_text):
        raise ValueError("review text must be one nonempty line of at most 3000 characters")
    decision = {key: item[key] for key in ("id", "media_url", "source_sha256", "page",
                                           "line_index", "ocr_text_sha256")}
    decision.update(verified_text=verified_text, reviewed_at=time.time())
    review_store.put(path, decision)
    return decision


def apply_review_decisions(scanned: dict, media_url: str, source_sha256: str,
                           decisions_path: str | Path | None) -> tuple[dict, set[tuple[int, int]]]:
    if decisions_path is None:
        return scanned, set()
    decisions = review_store.load(decisions_path)
    if not decisions:
        return scanned, set()
    adjusted = copy.deepcopy(scanned)
    reviewed: set[tuple[int, int]] = set()
    for page in adjusted["pages"]:
        if page.get("source") != "local_ocr":
            continue
        for line_index, line in enumerate(page["lines"]):
            # Decisions are bound to one exact line; a confident line can carry
            # one too when a record name read from it was sent for review.
            item_id = _item_id(media_url, source_sha256, page["page"], line_index, line["text"])
            decision = decisions.get(item_id)
            if not isinstance(decision, dict):
                continue
            if (decision.get("media_url") != media_url
                    or decision.get("source_sha256") != source_sha256
                    or decision.get("page") != page["page"]
                    or decision.get("line_index") != line_index
                    or decision.get("ocr_text_sha256") != _text_digest(line["text"])):
                continue
            replacement = decision.get("verified_text")
            if not isinstance(replacement, str) or not replacement.strip() or len(replacement) > 3000:
                continue
            line["text"] = replacement
            reviewed.add((page["page"], line_index))
        page["text"] = "\n".join(line["text"] for line in page["lines"])
    adjusted["text"] = "\n\n".join(page["text"] for page in adjusted["pages"])
    return adjusted, reviewed


def email_conflict_id(profile_url: str, owner_name: str, image_bio: str,
                      evidence: list[dict], existing_email: str,
                      choices: list[str]) -> str:
    """Bind one email choice to the exact owner, image text and source assets."""
    source = [profile_url, _text_digest(owner_name), _text_digest(image_bio),
              [[item["media_url"], item["sha256"]] for item in evidence],
              existing_email.lower(), [choice.lower() for choice in choices]]
    return sha256(json.dumps(source, ensure_ascii=False, separators=(",", ":"))
                  .encode("utf-8")).hexdigest()


def collect_email_conflict_items(audit_path: str | Path) -> list[dict]:
    """Expose only email conflicts backed by verified original image crops."""
    from server.faculty_extractor import find_emails, is_personal_email_for_scholar

    audit_path = Path(audit_path).resolve()
    root = audit_path.parent
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    issues = [item for item in audit.get("unresolved", [])
              if item.get("status") == EMAIL_STATUS]
    if not issues:
        return []
    records_path = _inside(audit["records_path"], root)
    records = json.loads(records_path.read_text(encoding="utf-8"))
    result = []
    for issue in issues:
        profile_url, owner_name = issue["url"], issue["name"]
        row = next((row for row in records if row.get("主页") == profile_url
                    and row.get("姓名") == owner_name), None)
        if row is None:
            continue
        evidence = [item for item in audit.get("image_biography_evidence", [])
                    if item.get("profile_url") == profile_url
                    and item.get("assigned_name") == owner_name]
        if not evidence:
            continue
        image_parts = []
        crops_by_email: dict[str, bytes] = {}
        for item in evidence:
            original = _inside(item["original_path"], root).read_bytes()
            if sha256(original).hexdigest() != item["sha256"]:
                raise ValueError("email review original SHA-256 mismatch")
            scanned = json.loads(_inside(item["ocr_path"], root).read_text(encoding="utf-8"))
            if scanned.get("source_sha256") not in (None, item["sha256"]):
                raise ValueError("email review OCR source SHA-256 mismatch")
            adjusted, _ = apply_review_decisions(
                scanned, item["media_url"], item["sha256"], root / TEXT_DECISIONS_FILE)
            image_parts.append(adjusted["text"].strip())
            for page in adjusted["pages"]:
                for line in page["lines"]:
                    for email in find_emails(line["text"]):
                        if (is_personal_email_for_scholar(email, owner_name)
                                and email not in crops_by_email and line.get("box")):
                            crops_by_email[email] = _crop_original(
                                original, item["kind"], page["page"], line["box"])
        image_bio = "\n\n".join(part for part in image_parts if part)
        personal = list(dict.fromkeys(email for email in find_emails(image_bio)
                                      if is_personal_email_for_scholar(email, owner_name)))
        existing = row.get("邮箱", "") or ""
        choices = list(dict.fromkeys(personal + ([existing] if existing else [])))
        if len(choices) < 2 or any(choice not in crops_by_email for choice in personal):
            continue
        result.append({"id": email_conflict_id(profile_url, owner_name, image_bio,
                                                 evidence, existing, choices),
                       "profile_url": profile_url, "owner_name": owner_name, "choices": choices,
                       "crops": [crops_by_email.get(choice) for choice in choices]})
    return result


def save_email_decision(path: str | Path, item: dict, selected_email: str) -> dict:
    if selected_email not in item["choices"]:
        raise ValueError("selected email is not a verified choice")
    decision = {"id": item["id"], "selected_email": selected_email,
                "choices": item["choices"], "reviewed_at": time.time()}
    review_store.put(path, decision)
    return decision


def resolve_email_conflict(path: str | Path, item_id: str, choices: list[str]) -> str | None:
    decision = next((row for row in review_store.rows(path)
                     if isinstance(row, dict) and row.get("id") == item_id), None)
    if decision is None:
        return None
    selected = decision.get("selected_email")
    return selected if decision.get("choices") == choices and selected in choices else None


def text_review_complete(audit_path: str | Path, items: list[dict]) -> bool:
    """Every OCR line under review has a nonempty decision bound to the same original line."""
    if not items:
        return False
    decisions = review_store.load(Path(audit_path).parent / TEXT_DECISIONS_FILE)
    for item in items:
        decision = decisions.get(item["id"], {})
        if (any(decision.get(key) != item[key] for key in
                ("media_url", "source_sha256", "page", "line_index", "ocr_text_sha256"))
                or not isinstance(decision.get("verified_text"), str)
                or not decision["verified_text"].strip()):
            return False
    return True


def email_review_complete(audit_path: str | Path, audit: dict, items: list[dict]) -> bool:
    """Every flagged conflict is on the page and has a choice; one without verified crops stays open."""
    flagged = {(issue.get("url"), issue.get("name")) for issue in audit.get("unresolved", [])
               if issue.get("status") == EMAIL_STATUS}
    if not flagged or flagged - {(item["profile_url"], item["owner_name"]) for item in items}:
        return False
    path = Path(audit_path).parent / EMAIL_DECISIONS_FILE
    return all(resolve_email_conflict(path, item["id"], item["choices"]) is not None for item in items)


def make_review_server(audit_path: str | Path, port: int = 8765):
    """Build a loopback-only review server for one audit."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Lock

    audit_path = Path(audit_path).resolve()
    items = {item["id"]: item for item in collect_review_items(audit_path)}
    email_items = {item["id"]: item for item in collect_email_conflict_items(audit_path)}
    html = render_review_page(list(items.values()), list(email_items.values())).encode("utf-8")
    decisions_path = audit_path.parent / TEXT_DECISIONS_FILE
    email_decisions_path = audit_path.parent / EMAIL_DECISIONS_FILE
    save_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/":
                self.send_error(404)
                return
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.send_error(403, "Review page host required")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def do_POST(self):
            if self.path not in ("/decision", "/email-decision"):
                self.send_error(404)
                return
            expected_host = f"127.0.0.1:{self.server.server_port}"
            origin = self.headers.get("Origin")
            if (self.headers.get("Host") != expected_host
                    or origin not in (None, f"http://{expected_host}")
                    or self.headers.get("Content-Type", "").split(";", 1)[0].lower()
                    != "application/json"):
                self.send_error(403, "Review page origin required")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ValueError("invalid request size")
                payload = json.loads(self.rfile.read(length))
                with save_lock:
                    if self.path == "/decision":
                        item = items[payload["id"]]
                        save_review_decision(decisions_path, item, payload["verified_text"])
                    else:
                        item = email_items[payload["id"]]
                        save_email_decision(email_decisions_path, item, payload["selected_email"])
            except (ValueError, KeyError, TypeError):
                self.send_error(400, "Invalid review decision")
                return
            self.send_response(204)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_review(audit_path: str | Path, port: int = 8765) -> None:
    """Serve one local audit to a human reviewer; never bind a LAN interface."""
    with make_review_server(audit_path, port) as server:
        print(f"Review page: http://127.0.0.1:{server.server_port}/", flush=True)
        server.serve_forever()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Local human review of uncertain biography OCR")
    parser.add_argument("--audit", required=True)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    serve_review(args.audit, args.port)


if __name__ == "__main__":
    main()
