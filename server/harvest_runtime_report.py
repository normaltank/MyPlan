"""Compact operational evidence, with no teacher fields or raw page contents."""
from collections import Counter
from datetime import datetime, timezone
from server.recovery_policy import classify_issues


def build_runtime_report(attempts, runs, unresolved, elapsed_seconds, budget=None):
    token_usage = {key: sum((attempt.get("token_usage") or {}).get(key, 0) for attempt in attempts)
                   for key in ("uncached_input", "cached_input", "output")}
    phases = [phase for attempt in attempts for phase in attempt.get("phases", [])]
    return {"schema": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "server_browser_workflow", "elapsed_seconds": round(elapsed_seconds, 3),
            "recon": {"attempts": len(attempts),
                      "model_calls": sum(attempt.get("model_calls", 0) for attempt in attempts),
                      "token_usage": token_usage,
                      "elapsed_seconds": round(sum(attempt.get("elapsed_seconds", 0) for attempt in attempts), 3),
                      "template_reused_branches": sum(bool(p.get("template_reused")) for p in phases),
                      "verified_reused_branches": sum(bool(p.get("reused")) for p in phases if p.get("phase") == "branch"),
                      "template_fallbacks": sum(bool(p.get("template_fallback")) for p in phases)},
            "cost": (budget or {}).get("cost", {"estimated_usd": None, "reason": "usage_unavailable"}),
            "browser_runs": runs,
            "unresolved": dict(Counter(item.get("status", "unknown") for item in unresolved)),
            "review_count": len(classify_issues(unresolved).review)}
