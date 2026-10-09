"""University-level identity decisions for same-name observations across colleges.

A completed college can only approve identities inside its own audit
(``identity_review``). When colleges of one delivery each hold a teacher with
the same name, the university version stays blocked until an administrator
reviews every original observation of that name here.

No model participates. A decision is bound to the exact observation set: task,
row, receipt fields and the college audit hash. Any change to those yields a
new group, so an old decision no longer applies. Decisions are appended, never
edited; the latest revision of a group is the one in force.

First version supports two verdicts only:
- ``distinct``: every observation is a different teacher; all rows are kept.
- ``insufficient``: the reason is recorded and the version stays blocked.
Treating observations from two colleges as one person is a new merge rule that
the project owner has not decided, so it is not offered.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid

from server.faculty_identity import field_value
from server.identity_review import digest, record_digest

VERDICTS = ("distinct", "insufficient")
REASON_LIMIT = 2000


class RevisionConflict(Exception):
    """Another decision was saved for this group after the page was loaded."""


def ensure_schema(con) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS university_identity_reviews (
        decision_id TEXT PRIMARY KEY, school TEXT NOT NULL, group_id TEXT NOT NULL,
        revision INTEGER NOT NULL, verdict TEXT NOT NULL CHECK(verdict IN ('distinct','insufficient')),
        reason TEXT NOT NULL, evidence_json TEXT NOT NULL,
        reviewed_by INTEGER NOT NULL, reviewed_at REAL NOT NULL,
        UNIQUE(school, group_id, revision))""")


def sources_by_profile(stats: dict) -> dict[str, list]:
    coverage = stats.get("coverage") if isinstance(stats.get("coverage"), dict) else {}
    grouped = {}
    for source in coverage.get("field_sources") or []:
        if isinstance(source, dict) and source.get("profile_url"):
            grouped.setdefault(source["profile_url"], []).append(source)
    return grouped


def observation(task, seq: int, record: dict, stats: dict, sources: dict[str, list]) -> dict:
    """Bind one saved row to its task, position, receipt fields and the college audit."""
    url = field_value(record, "主页")
    source = digest({"audit_sha256": stats.get("audit_sha256", ""),
                     "field_sources": sources.get(url, []) if url else []})
    sha = record_digest(record)
    return {"observation_id": digest({"task_id": task["task_id"], "seq": seq,
                                      "record_sha256": sha, "source_sha256": source}),
            "task_id": task["task_id"], "seq": seq, "college": task["college"],
            "record_sha256": sha, "source_sha256": source,
            "audit_sha256": stats.get("audit_sha256", "")}


def build_group(school: str, name: str, observations: list[dict], merge_keys: list) -> dict:
    """Every observation of one name in the assembled snapshot, never a subset."""
    ids = sorted(o["observation_id"] for o in observations)
    return {"group_id": digest({"school": school, "name": name, "observations": ids}),
            "name": name, "observations": observations,
            # Two rows on one profile page cannot be declared different people.
            "merge_keys_distinct": len({repr(k) for k in merge_keys}) == len(merge_keys)}


def _evidence(group: dict) -> dict:
    return {"name": group["name"], "observations": [
        {key: o[key] for key in ("observation_id", "task_id", "seq", "college",
                                 "record_sha256", "source_sha256")}
        for o in sorted(group["observations"], key=lambda o: o["observation_id"])]}


def _decode(row) -> dict:
    return {"decision_id": row["decision_id"], "school": row["school"], "group_id": row["group_id"],
            "revision": row["revision"], "verdict": row["verdict"], "reason": row["reason"],
            "evidence": json.loads(row["evidence_json"]), "reviewed_by": row["reviewed_by"],
            "reviewed_at": row["reviewed_at"]}


def latest_decisions(con, school: str) -> dict[str, dict]:
    try:
        rows = con.execute("""SELECT * FROM university_identity_reviews WHERE school=?
            ORDER BY group_id, revision""", (school,)).fetchall()
    except sqlite3.OperationalError:
        return {}
    latest = {}
    for row in rows:
        latest[row["group_id"]] = _decode(row)
    return latest


def decision_applies(decision: dict | None, group: dict) -> bool:
    """Only an intact, current ``distinct`` decision releases a group."""
    try:
        return (decision is not None and decision["verdict"] == "distinct"
                and decision["group_id"] == group["group_id"]
                and type(decision["reviewed_by"]) is int and decision["reviewed_by"] > 0
                and group["merge_keys_distinct"]
                and decision["evidence"] == _evidence(group))
    except (KeyError, TypeError):
        return False


def certificate(decision: dict) -> dict:
    return {"decision_id": decision["decision_id"], "group_id": decision["group_id"],
            "revision": decision["revision"], "verdict": decision["verdict"],
            "reason": decision["reason"], "reviewed_by": decision["reviewed_by"],
            "reviewed_at": decision["reviewed_at"],
            "observation_ids": [o["observation_id"] for o in decision["evidence"]["observations"]]}


def save_decision(con, school: str, group: dict, verdict: str, reason: str, *,
                  reviewed_by: int, expected_revision: int) -> tuple[dict, bool]:
    """Append a decision for the server-computed group; returns (decision, created)."""
    if verdict not in VERDICTS:
        raise ValueError("verdict must be distinct or insufficient")
    if type(reviewed_by) is not int or reviewed_by <= 0:
        raise ValueError("verified reviewer required")
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason or len(reason) > REASON_LIMIT:
        raise ValueError("reason is required")
    if verdict == "distinct" and not group["merge_keys_distinct"]:
        raise ValueError("observations sharing one profile page cannot be declared distinct")
    con.execute("BEGIN IMMEDIATE")
    row = con.execute("""SELECT * FROM university_identity_reviews WHERE school=? AND group_id=?
        ORDER BY revision DESC LIMIT 1""", (school, group["group_id"])).fetchone()
    latest = _decode(row) if row else None
    if latest and latest["verdict"] == verdict and latest["reason"] == reason:
        return latest, False
    if (latest["revision"] if latest else 0) != expected_revision:
        raise RevisionConflict(group["group_id"])
    decision = {"decision_id": uuid.uuid4().hex, "school": school, "group_id": group["group_id"],
                "revision": expected_revision + 1, "verdict": verdict, "reason": reason,
                "evidence": _evidence(group), "reviewed_by": reviewed_by, "reviewed_at": time.time()}
    con.execute("""INSERT INTO university_identity_reviews (decision_id,school,group_id,revision,
        verdict,reason,evidence_json,reviewed_by,reviewed_at) VALUES (?,?,?,?,?,?,?,?,?)""",
        (decision["decision_id"], school, decision["group_id"], decision["revision"], verdict, reason,
         json.dumps(decision["evidence"], ensure_ascii=False, sort_keys=True), reviewed_by,
         decision["reviewed_at"]))
    return decision, True


_FIELDS = ("学院", "姓名", "主页", "职称", "邮箱")


def render_page(school: str, scope: dict, groups: list[dict], *, csrf_token: str,
                decision_url: str, publish_url: str, return_url: str = "/") -> str:
    """Show every original observation of each same-name group side by side."""
    from html import escape
    from server.image_bio_review import _REVIEW_CSS, _base_css, _page_link
    verdict_text = {"distinct": "不同教师", "insufficient": "证据不足，保持阻断"}
    cards = []
    for group in groups:
        rows = []
        for o in group["observations"]:
            record = o["record"]
            values = {"学院": o["college"], **{field: field_value(record, field) for field in _FIELDS[1:]}}
            fields = "".join("<dt>" + field + "</dt><dd>" + (
                _page_link(values[field]) if field == "主页" and values[field] else escape(values[field] or "（空）"))
                + "</dd>" for field in _FIELDS)
            bio = field_value(record, "简介")
            rows.append('<div class="observation"><dl>' + fields + "</dl>"
                        + "<details><summary>完整简介（" + str(len(bio)) + " 字）</summary><blockquote>"
                        + (escape(bio).replace("\n", "<br>") or "（空）") + "</blockquote></details>"
                        + '<p class="meta">工单 ' + escape(o["task_id"]) + " · 第 " + str(o["seq"]) + " 行 · 学院审计 "
                        + escape((o["audit_sha256"] or "无")[:16]) + "</p></div>")
        decision = group.get("decision")
        state = ("当前决定：" + verdict_text[decision["verdict"]] + "（第 " + str(decision["revision"]) + " 版）理由："
                 + escape(decision["reason"])) if decision else "尚未复核"
        if group.get("applied"):
            state += "；已在大学汇总中生效"
        shared = "" if group["merge_keys_distinct"] else (
            '<p class="meta">有观察指向同一个人主页，不能判为不同教师。</p>')
        form = (shared + '<label>判定理由（必填，写明对照了哪些来源）<textarea rows="2"></textarea></label>'
                '<div class="actions">'
                + ('<button type="button" data-verdict="distinct">确认是不同教师</button>'
                   if group["merge_keys_distinct"] else "")
                + '<button type="button" class="secondary" data-verdict="insufficient">'
                  '证据不足或疑似同一人，保持阻断</button></div>')
        cards.append('<section class="card university-group" data-group="' + escape(group["group_id"], quote=True)
                     + '" data-revision="' + str(decision["revision"] if decision else 0) + '">'
                     + "<h2>" + escape(group["name"] or "（无姓名）") + " · " + str(len(group["observations"]))
                     + " 条原始观察</h2>" + "".join(rows) + '<p class="meta">' + state + "</p>" + form
                     + '<p class="status" role="status"></p></section>')
    body = "".join(cards) or "<p>本次大学范围没有待复核的跨学院同名。</p>"
    state_text = {"ready": "可交付", "blocked": "待复核", "collecting": "采集中"}.get(scope["state"], scope["state"])
    data = json.dumps({"csrf": csrf_token, "decision": decision_url, "publish": publish_url,
                       "school": school}, ensure_ascii=False).replace("<", "\\u003c")
    css = (".observation{border:1px solid var(--border);border-radius:var(--radius-sm);padding:12px}"
           ".observation dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:0 0 8px}"
           ".observation dt{color:var(--muted)}.observation dd{margin:0;word-break:break-all}")
    return ("<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>大学身份复核 · 高校师资采集平台</title><style>" + _base_css() + _REVIEW_CSS + css + "</style>"
            '<p class="back"><a href="' + escape(return_url, quote=True) + '">← 返回采集平台</a></p>'
            "<h1>" + escape(school) + " · 跨学院同名复核</h1>"
            '<p class="intro">本次范围：' + escape("、".join(scope.get("target_colleges", []))) + "；大学汇总"
            + state_text + "。各学院仍为采集完成。决定只对页面上这组完全相同的观察生效，来源变化后须重新复核。"
            "首版只能确认“不同教师”或记录“证据不足”；跨学院合并为同一人尚未开放。</p>"
            + body + '<button id="publish" type="button">重新审计并发布大学版本</button>'
            '<p id="publish-status" role="status"></p>'
            "<script>const review=" + data + ";"
            "async function failure(response){const text=await response.text();"
            "try{const detail=JSON.parse(text).detail;if(typeof detail==='string')return new Error(detail);}catch(_){}"
            "return new Error(text||('HTTP '+response.status));}"
            "document.querySelectorAll('.university-group').forEach(card=>{"
            "card.querySelectorAll('button[data-verdict]').forEach(button=>button.onclick=async()=>{"
            "const status=card.querySelector('.status');"
            "try{const response=await fetch(review.decision,{method:'POST',"
            "headers:{'Content-Type':'application/json','X-CSRF-Token':review.csrf},"
            "body:JSON.stringify({school:review.school,group_id:card.dataset.group,verdict:button.dataset.verdict,"
            "reason:card.querySelector('textarea').value,expected_revision:Number(card.dataset.revision)})});"
            "if(!response.ok)throw await failure(response);const saved=await response.json();"
            "card.dataset.revision=saved.revision;"
            "status.textContent='已保存：'+(saved.verdict==='distinct'?'不同教师':'证据不足，保持阻断');}"
            "catch(error){status.textContent='保存失败：'+error.message;}});});"
            "document.getElementById('publish').onclick=async()=>{const status=document.getElementById('publish-status');"
            "try{const response=await fetch(review.publish,{method:'POST',"
            "headers:{'Content-Type':'application/json','X-CSRF-Token':review.csrf},"
            "body:JSON.stringify({school:review.school})});if(!response.ok)throw await failure(response);"
            "const result=await response.json();status.textContent=result.download_url?"
            "'已发布大学版本 '+result.version+'，请返回平台下载':'大学汇总仍待复核';}"
            "catch(error){status.textContent='发布失败：'+error.message;}};"
            "</script></html>")
