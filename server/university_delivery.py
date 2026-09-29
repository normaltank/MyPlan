"""Scoped, immutable university deliveries and latest completed college snapshots."""
from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from server.faculty_identity import field_value


def delivery_scope(con, batch_id, school):
    rows = con.execute("""SELECT task_id,college,status,stats_json FROM tasks
        WHERE batch_id=? AND school=? AND task_type='harvest'
        ORDER BY college,created_at,task_id""", (batch_id, school)).fetchall()
    targets = sorted({r[1] for r in rows})
    completed = sorted({r[1] for r in rows if r[2] == "completed"})
    pending = [r[0] for r in rows if r[2] != "completed"]
    failed = [r[0] for r in rows if r[2] == "failed"]
    unresolved = []
    for row in rows:
        audit = json.loads(row[3])
        if (audit.get("unresolved") or audit.get("delivery_ready") is False
                or any(e.get("status") == "unresolved" for e in audit.get("eligibility", []))):
            unresolved.append(row[0])
    state = "blocked" if failed or unresolved else "collecting" if pending or not rows else "ready"
    return {"batch_id": batch_id, "school": school, "state": state,
            "coverage_scope": "requested_colleges_only", "target_colleges": targets,
            "completed_colleges": completed, "pending_task_ids": pending,
            "failed_task_ids": failed, "unresolved_task_ids": unresolved,
            "task_ids": [r[0] for r in rows],
            "eligibility_basis": "completed_college_audits"}


def _assemble(con, rows):
    records, identities, by_key, by_name, conflicts = [], [], {}, {}, []
    for task in rows:
        for seq, payload in con.execute("SELECT seq,payload_json FROM records WHERE task_id=? ORDER BY seq", (task["task_id"],)):
            record = json.loads(payload)
            name = field_value(record, "姓名")
            url = field_value(record, "主页")
            # A roster URL or missing profile is not sufficient evidence for merging.
            key = (name, url) if name and url and url != task["url"] else (task["task_id"], seq)
            source = {"task_id": task["task_id"], "seq": seq, "college": task["college"]}
            if name in by_name and key not in by_name[name]:
                conflicts.append({"kind": "homonym_or_distinct_profile", "identity_keys": [
                    hashlib.sha256(repr(k).encode()).hexdigest() for k in sorted([*by_name[name], key], key=repr)],
                    "requires_review": True})
            by_name.setdefault(name, set()).add(key)
            if key in by_key:
                identity = identities[by_key[key]]
                existing = records[by_key[key]]
                differing = [field for field in ("邮箱", "职称", "简介")
                             if field_value(existing, field) and field_value(record, field)
                             and field_value(existing, field) != field_value(record, field)]
                if differing:
                    conflicts.append({"kind": "conflicting_same_profile", "fields": differing,
                                      "sources": identity["sources"] + [source], "requires_review": True})
                    # Keep every conflicting observation; publication is blocked below.
                    key = (task["task_id"], seq)
                else:
                    identity["sources"].append(source)
                    identity["colleges"] = sorted(set(identity["colleges"] + [task["college"]]))
                    for field in ("邮箱", "职称", "简介"):
                        if not field_value(existing, field) and field_value(record, field):
                            existing[field] = field_value(record, field)
                    continue
            by_key[key] = len(records)
            records.append(record)
            identities.append({"identity_key": hashlib.sha256(repr(key).encode()).hexdigest(),
                               "colleges": [task["college"]], "sources": [source]})
    return {"records": records, "identities": identities, "identity_conflicts": conflicts,
            "task_ids": [r["task_id"] for r in rows],
            "college_snapshots": [{"college": r["college"], "task_id": r["task_id"],
                "finished_at": r["finished_at"], "audit": json.loads(r["stats_json"])} for r in rows]}


def university_snapshot(con, school, task_ids=None):
    rows = con.execute("""SELECT * FROM tasks WHERE school=? AND status='completed'
        AND task_type='harvest' ORDER BY COALESCE(finished_at,created_at),created_at,task_id""", (school,)).fetchall()
    if task_ids is not None:
        rows = [r for r in rows if r["task_id"] in task_ids]
    latest = {r["college"]: r for r in rows}
    rows = [latest[k] for k in sorted(latest)]
    return _assemble(con, rows)


def manifest_snapshot_is_safe(queue, manifest):
    """Audit the observations bound to this workbook, regardless of newer tasks."""
    try:
        task_ids = [item["task_id"] for item in manifest["college_snapshots"]]
        if not task_ids or len(set(task_ids)) != len(task_ids):
            return False
        with queue._connect() as con:
            rows = con.execute("SELECT * FROM tasks WHERE school=? AND task_id IN ("
                + ",".join("?" for _ in task_ids) + ")", [manifest["school"], *task_ids]).fetchall()
            if len(rows) != len(task_ids) or any(row["status"] != "completed" for row in rows):
                return False
            for row in rows:
                audit = json.loads(row["stats_json"])
                if (audit.get("unresolved") or audit.get("delivery_ready") is False
                        or any(e.get("status") == "unresolved" for e in audit.get("eligibility", []))):
                    return False
                if "coverage" in audit and (audit["coverage"].get("complete") is not True
                                            or audit["coverage"].get("issues")):
                    return False
            return not any(c["requires_review"] for c in _assemble(con, rows)["identity_conflicts"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def _publish_current(exporter, root, safe_school, school, current):
    """Current all-college state cannot enlarge or invalidate a scoped delivery."""
    state = "blocked" if any(c["requires_review"] for c in current["identity_conflicts"]) else "ready"
    fingerprint = hashlib.sha256(json.dumps(current, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    manifest_path = root / f"{safe_school}.manifest.json"
    old_fingerprint = None
    if manifest_path.is_file():
        try:
            old_fingerprint = json.loads(manifest_path.read_text(encoding="utf-8")).get("snapshot_version")
        except (ValueError, OSError):
            pass
    manifest = {"school": school, "state": state,
        "coverage_scope": "latest_completed_college_snapshots",
        "colleges": [c["college"] for c in current["college_snapshots"]],
        "college_snapshots": current["college_snapshots"], "identities": current["identities"],
        "identity_conflicts": current["identity_conflicts"], "updated_at": time.time(),
        "snapshot_version": fingerprint}
    if state == "ready" and (old_fingerprint != fingerprint or not (root / f"{safe_school}.xlsx").is_file()):
        staging = root / f".{safe_school}.{uuid.uuid4().hex}.xlsx"
        try:
            exporter.export_dual_sheet_excel(current["records"], str(staging))
            staging.replace(root / f"{safe_school}.xlsx")
        finally:
            staging.unlink(missing_ok=True)
        temp = root / f".{uuid.uuid4().hex}.json"
        temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(manifest_path)
    # Keep the previous workbook manifest paired with its actual published snapshot.
    # This separate status records an unresolved new current aggregate without overwriting it.
    status_temp = root / f".{uuid.uuid4().hex}.json"
    status_temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    status_temp.replace(root / f"{safe_school}.current_state.json")
    return {"current_snapshot_state": state, "current_identity_conflicts": current["identity_conflicts"],
            "current_snapshot_version": fingerprint}


def publish_delivery(queue, batch_id, school, output_root):
    """Serialize snapshot selection and file publication across queue processes."""
    from server.autonomous_agent import AutonomousBrowserAgent

    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    safe_school = re.sub(r'[\\/:*?"<>|]', "", school).strip() or "高校"
    with queue._connect() as con:
        con.execute("PRAGMA busy_timeout=30000")
        con.execute("BEGIN IMMEDIATE")
        scope = delivery_scope(con, batch_id, school)
        if scope["state"] != "ready":
            return {"delivery_scope": scope, "university_excel": None}
        snapshot = university_snapshot(con, school, scope["task_ids"])
        current = university_snapshot(con, school)
        exporter = AutonomousBrowserAgent()
        if any(c["requires_review"] for c in snapshot["identity_conflicts"]):
            scope["state"] = "blocked"
            scope["identity_conflicts"] = snapshot["identity_conflicts"]
            current_status = _publish_current(exporter, root, safe_school, school, current)
            return {"delivery_scope": scope, "university_excel": None, **current_status}
        encoded = json.dumps({"scope": scope, "snapshots": snapshot["college_snapshots"]}, sort_keys=True, ensure_ascii=False)
        version = hashlib.sha256(encoded.encode()).hexdigest()[:20]
        directory = root / "versions" / hashlib.sha256(school.encode()).hexdigest()[:16] / version
        directory.mkdir(parents=True, exist_ok=True)
        excel = directory / f"{safe_school}.xlsx"
        manifest_path = directory / "manifest.json"
        if not excel.exists() or not manifest_path.exists():
            staging = directory / f".{uuid.uuid4().hex}.xlsx"
            try:
                exporter.export_dual_sheet_excel(snapshot["records"], str(staging))
                staging.replace(excel)
            finally:
                staging.unlink(missing_ok=True)
            manifest = {"version": version, "school": school, "scope": scope,
                "created_at": time.time(), "identities": snapshot["identities"],
                "identity_conflicts": snapshot["identity_conflicts"],
                "college_snapshots": snapshot["college_snapshots"],
                "total": len(snapshot["records"]),
                "with_email": sum(bool(str(r.get("邮箱", r.get("email", ""))).strip()) for r in snapshot["records"])}
            manifest["no_email"] = manifest["total"] - manifest["with_email"]
            temp_manifest = directory / f".{uuid.uuid4().hex}.json"
            temp_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            temp_manifest.replace(manifest_path)
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        current_status = _publish_current(exporter, root, safe_school, school, current)
        url = "/download/" + excel.relative_to(root).as_posix()
        info = {"school": school, "version": version, "filename": excel.name,
                "download_url": url, "excel_path": str(excel), "university_excel": str(excel),
                "manifest_path": str(manifest_path), "delivery_scope": scope,
                **current_status,
                "colleges_count": len(scope["target_colleges"]),
                **{k: manifest[k] for k in ("total", "with_email", "no_email")}}
        con.execute("""INSERT OR IGNORE INTO university_deliveries
            (version,batch_id,school,manifest_json,created_at) VALUES (?,?,?,?,?)""",
            (version, batch_id, school, json.dumps(info, ensure_ascii=False), time.time()))
        # Keep college downloads distinct; batch API exposes this immutable scoped URL.
        return info
