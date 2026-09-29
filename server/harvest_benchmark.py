"""Report every frozen sample; collector readiness alone is not correctness.

This local reporting tool does not navigate sites, call models, publish records,
or derive an oracle from collector output. Independent review facts are supplied
with the retained evidence by a reviewer of the actual browser pages.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


def _independently_verified(outcome: dict, report: dict) -> bool:
    oracle = outcome.get("oracle") or {}
    expected = oracle.get("expected_observations")
    sampled = oracle.get("sampled_profile_count")
    return (outcome.get("evidence_mode") == "live_browser"
            and report.get("delivery_ready") is True
            and oracle.get("reviewed_independently") is True
            and oracle.get("complete_roster_review") is True
            and type(expected) is int and expected > 0 and expected == report.get("total")
            and oracle.get("identity_set_matches") is True
            and type(sampled) is int and sampled >= 2
            and oracle.get("sampled_fulltext_matches") is True
            and oracle.get("sampled_fields_match") is True
            and oracle.get("sampled_clean_body_match") is True
            and bool(oracle.get("evidence_paths")))


def summarize_benchmark(manifest: dict, outcomes: list[dict]) -> dict[str, Any]:
    """Preserve selected order, unknown usage and missing samples explicitly."""
    selected = manifest["cases"]
    ids = [item["id"] for item in selected]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("a nonempty, unique frozen case manifest is required")
    observed = {}
    for outcome in outcomes:
        key = outcome["id"]
        if key not in ids or key in observed:
            raise ValueError("duplicate or unselected benchmark result")
        observed[key] = outcome
    rows = []
    reported_calls = reported_tokens = unknown = 0
    for case in selected:
        outcome = observed.get(case["id"])
        if outcome is None:
            rows.append({**case, "stage": "not_tested", "collector_ready": False,
                         "independently_verified": False, "model_usage": None})
            continue
        report = outcome.get("report") or {}
        usage = outcome.get("model_usage")
        if (not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
                                               for key in ("model_calls", "tokens"))):
            usage = None
            unknown += 1
        else:
            reported_calls += usage["model_calls"]
            reported_tokens += usage["tokens"]
        rows.append({**case, "college": outcome.get("college"),
                     "entry_url": outcome.get("entry_url"), "stage": outcome.get("stage", "unknown"),
                     "evidence_mode": outcome.get("evidence_mode", "unknown"),
                     "collector_ready": report.get("delivery_ready") is True,
                     "independently_verified": _independently_verified(outcome, report),
                     "observations": report.get("total"),
                     "blockers": dict(Counter(item.get("status", "unknown")
                                              for item in report.get("unresolved", []))),
                     "failure_code": outcome.get("failure_code"),
                     "audit_path": report.get("audit_path"),
                     "model_usage": usage})
    return {"sampling": manifest.get("sampling", {}), "selected_cases": len(ids),
            "reported_cases": len(observed), "not_tested_cases": len(ids) - len(observed),
            "live_browser_cases": sum(row.get("evidence_mode") == "live_browser" for row in rows),
            "collector_ready_cases": sum(row["collector_ready"] for row in rows),
            "independently_verified_cases": sum(row["independently_verified"] for row in rows),
            "generalization_proven": False,
            "model_usage": {"reported_model_calls": reported_calls, "reported_tokens": reported_tokens,
                            "unknown_cases": unknown}, "cases": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a frozen external harvest benchmark")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--outcome", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize_benchmark(json.loads(args.manifest.read_text(encoding="utf-8")),
                                 [json.loads(path.read_text(encoding="utf-8")) for path in args.outcome])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("selected_cases", "reported_cases", "not_tested_cases",
                     "collector_ready_cases", "independently_verified_cases", "model_usage")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
