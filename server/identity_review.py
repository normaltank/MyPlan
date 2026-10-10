"""Human identity decisions bound to original observations and browser body hashes.

No model participates. Original rows stay in local evidence; merging concatenates
their complete biographies and explicitly selects the primary row's other fields.
"""
from __future__ import annotations

from collections import defaultdict
from itertools import combinations
import hashlib
import json
import math
from pathlib import Path
import time

from server import review_store
from server.faculty_identity import field_value

STATUS = "distinct_profile_identity_unverified"
DECISIONS_FILE = "identity_reviews.json"
CONTENT_FIELDS = ("姓名", "主页", "职称", "邮箱", "简介", "学校", "学院")


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def record_digest(record) -> str:
    # Collection date, operator and row number are not source identity evidence.
    return digest({key: field_value(record, key) for key in CONTENT_FIELDS})


def observation_key(record):
    return field_value(record, "姓名"), field_value(record, "主页"), record_digest(record)


def item_id(item) -> str:
    return digest({"name": item["name"], "observations": sorted([
        {"url": o["url"], "record_sha256": record_digest(o["record"]),
         "source_sha256": o.get("source_sha256", "")} for o in item["observations"]], key=lambda o:o["url"])})


def pending_items(records, source_hashes=None) -> list[dict]:
    groups = defaultdict(list)
    for record in records:
        if field_value(record, "姓名") and field_value(record, "主页"):
            groups[field_value(record, "姓名")].append(record)
    items = []
    for name, group in groups.items():
        if len({field_value(r, "主页") for r in group}) < 2:
            continue
        item = {"name": name, "observations": [
            {"url": field_value(row, "主页"), "record": dict(row),
             "source_sha256": (source_hashes or {}).get(field_value(row, "主页"), "")}
            for row in group]}
        item["id"] = item_id(item)
        items.append(item)
    return items


def collect_items(audit_path) -> list[dict]:
    path = Path(audit_path).resolve()
    audit = json.loads(path.read_text(encoding="utf-8"))
    if not any(u.get("status") == STATUS for u in audit.get("unresolved", [])):
        return []
    if "identity_review_items" in audit:
        items = audit["identity_review_items"]
    else:
        # Old failed tickets already contain the original records and page captures.
        records_path = Path(audit["records_path"]).resolve()
        if not records_path.is_relative_to(path.parent):
            raise ValueError("identity records outside audit")
        records = json.loads(records_path.read_text(encoding="utf-8"))
        hashes = {}
        for source in audit.get("coverage", {}).get("field_sources", []):
            if source.get("selected_html_sha256"):
                hashes[source["profile_url"]] = source["selected_html_sha256"]
        for snapshot in audit.get("snapshots", []):
            if snapshot.get("kind") == "profile" and snapshot["url"] not in hashes:
                original = Path(snapshot["path"]).resolve()
                if not original.is_relative_to(path.parent):
                    raise ValueError("identity page outside audit")
                hashes[snapshot["url"]] = hashlib.sha256(original.read_bytes()).hexdigest()
        items = pending_items(records, hashes)
    if any(item.get("id") != item_id(item) for item in items):
        raise ValueError("identity evidence digest mismatch")
    return items


def load_decisions(path) -> dict:
    return review_store.load(path)


def _resolution(item, verdict, primary_url="", assignments=None):
    urls = {o["url"] for o in item["observations"]}
    if len(urls) != len(item["observations"]):
        raise ValueError("conflicting observations share a URL")
    if verdict == "distinct":
        return {url: url for url in urls}
    if verdict == "same" and primary_url in urls:
        return {url: primary_url for url in urls}
    if verdict == "partition" and isinstance(assignments, dict) and set(assignments) == urls:
        if all(value in urls and assignments.get(value) == value for value in assignments.values()):
            return assignments
    if verdict == "insufficient":
        return {}
    raise ValueError("identity decision must cover every observed URL and select a member as primary")


def save_decision(path, item, verdict, primary_url="", *, assignments=None, reviewed_by: int):
    if type(reviewed_by) is not int or reviewed_by <= 0 or item.get("id") != item_id(item):
        raise ValueError("verified reviewer and exact evidence required")
    resolution = _resolution(item, verdict, primary_url, assignments)
    decision = {"id": item["id"], "item": item, "verdict": verdict, "resolution": resolution,
                "reviewed_by": reviewed_by, "reviewed_at": time.time()}
    review_store.put(path, decision)
    return decision


def _valid_decision(item, decision) -> bool:
    try:
        if (decision["id"] != item["id"] or item_id(decision["item"]) != item["id"]
                or type(decision["reviewed_by"]) is not int or decision["reviewed_by"] <= 0
                or not math.isfinite(decision["reviewed_at"]) or decision["reviewed_at"] <= 0):
            return False
        resolution = _resolution(item, decision["verdict"], assignments=decision["resolution"],
                                 primary_url=next(iter(decision["resolution"].values()), ""))
        return bool(resolution) and resolution == decision["resolution"]
    except (ValueError, TypeError, KeyError, StopIteration, AttributeError):
        return False


def _resolved_records(item, resolution) -> list[dict]:
    groups = defaultdict(list)
    for observation in item["observations"]:
        groups[resolution[observation["url"]]].append(observation)
    result = []
    for primary, observations in groups.items():
        kept = dict(next(o["record"] for o in observations if o["url"] == primary))
        if len(observations) > 1:
            bios, seen = [], set()
            for o in observations:
                bio = field_value(o["record"], "简介")
                if bio and bio not in seen:
                    bios.append("来源：" + o["url"] + "\n" + bio)
                    seen.add(bio)
            kept["简介"] = "\n\n".join(bios)
        result.append(kept)
    return result


def _certificate(decision, results, *, current_item=None, equivalence=None):
    certificate = {"id": decision["id"], "verdict": decision["verdict"], "resolution": decision["resolution"],
                "reviewed_by": decision["reviewed_by"], "reviewed_at": decision["reviewed_at"],
                "result_sha256s": sorted(record_digest(row) for row in results)}
    if equivalence:
        certificate.update(current_item=current_item, source_equivalence=equivalence)
    return certificate


def _reviewed_item(item, decision_path, *, source_html=None, source_evidence=None):
    if item.get('id') != item_id(item):
        return None, None
    decisions = load_decisions(decision_path)
    direct = decisions.get(item['id'],{})
    if _valid_decision(item,direct):
        return direct, None
    if item['id'] in decisions:
        return None,None
    if source_html is None or source_evidence is None:
        return None, None
    from server.review_evidence import old_review_items, identity_equivalence
    for bundle, previous in old_review_items(Path(decision_path).parent.parent,'identity_review_items'):
        try:
            decision = bundle.decisions(DECISIONS_FILE).get(previous['id'],{})
            if (previous.get('id') != item_id(previous) or decisions.get(previous['id']) != decision
                    or not _valid_decision(previous,decision)):
                continue
            proof = identity_equivalence(bundle,previous,item,source_html,source_evidence)
            if proof:
                return decision, proof
        except (OSError,ValueError,KeyError,TypeError):
            continue
    return None, None


def apply_decisions(records, source_hashes, decision_path, *, source_html=None, source_evidence=None):
    """Resolve exact current observations; stale/unresolved decisions stay pending."""
    items = pending_items(records, source_hashes)
    pending, certificates, replacements = [], [], {}
    for item in items:
        decision, proof = _reviewed_item(item,decision_path,source_html=source_html,source_evidence=source_evidence)
        if decision is None:
            pending.append(item)
            continue
        results = _resolved_records(item, decision["resolution"])
        replacements[item["name"]] = results
        certificates.append(_certificate(decision, results,current_item=item,equivalence=proof))
    result, emitted = [], set()
    for row in records:
        name = field_value(row, "姓名")
        if name not in replacements:
            result.append(row)
        elif name not in emitted:
            result.extend(replacements[name])
            emitted.add(name)
    if certificates:
        result = [{**row, "序号": index} for index, row in enumerate(result, 1)]
    return result, pending, certificates


def validated_pairs(records, certificates, decision_path):
    """Revalidate output rows against the actual human decision, not a claimed flag."""
    decisions = load_decisions(decision_path)
    keys = {observation_key(row) for row in records}
    pairs = set()
    for certificate in certificates:
        if not isinstance(certificate, dict):
            raise ValueError("identity review certificate must be an object")
        decision = decisions.get(certificate.get("id"), {})
        item = decision.get("item", {})
        if not _valid_decision(item, decision):
            raise ValueError("identity review evidence is missing or invalid")
        proof = None
        if certificate.get('source_equivalence'):
            from server.review_evidence import _read
            current = certificate.get('current_item',{})
            root = Path(decision_path).parent.parent
            html = {p['source_url']:_read(root,p['new_original_sha256'],'.html').decode('utf8')
                    for p in certificate['source_equivalence']}
            evidence = {p['source_url']:{'profile_content_selector':p['bounds'][0],
                         'profile_content_start_selector':p['bounds'][1]} for p in certificate['source_equivalence']}
            reviewed, proof = _reviewed_item(current,decision_path,source_html=html,source_evidence=evidence)
            if reviewed != decision or proof != certificate['source_equivalence']:
                raise ValueError('identity source equivalence is missing or invalid')
            item = current
        results = _resolved_records(item, decision["resolution"])
        if certificate != _certificate(decision, results,current_item=item,equivalence=proof) or any(observation_key(r) not in keys for r in results):
            raise ValueError("identity review does not match output records")
        pairs.update(frozenset((observation_key(a), observation_key(b))) for a,b in combinations(results, 2))
    return pairs


def audit_pairs(records, stats, task_id):
    """Allow only decisions located within this task's local evidence directory."""
    certificates = stats.get("identity_reviews") or []
    if not certificates:
        return set()
    audit = Path(stats.get("audit_path", "")).resolve()
    root = audit.parent.parent
    decision_path = Path(stats.get("identity_review_path", "")).resolve()
    if (root.name != task_id or root.parent.name != "tasks" or decision_path.name != DECISIONS_FILE
            or not decision_path.is_relative_to(root) or not audit.is_file()):
        raise ValueError("identity review is not bound to this task")
    return validated_pairs(records, certificates, decision_path)


def review_complete(audit_path, items):
    from server.review_evidence import current_source
    path = Path(audit_path)
    html, evidence = {}, {}
    try:
        for item in items:
            for observation in item['observations']:
                url = observation['url']
                html[url], evidence[url] = current_source(path,url)
        return all(_reviewed_item(item,path.parent/DECISIONS_FILE,source_html=html,
                                 source_evidence=evidence)[0] is not None for item in items)
    except (OSError,ValueError,KeyError,TypeError):
        # Exact decisions do not require a counter equivalence source archive.
        decisions = load_decisions(path.parent/DECISIONS_FILE)
        return all(_valid_decision(item,decisions.get(item['id'],{})) for item in items)
