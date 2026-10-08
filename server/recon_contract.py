"""Structure-only submission contracts and safe, immediate scout feedback."""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

Selector = Annotated[str, Field(min_length=1, pattern=r"^[^\u4e00-\u9fff]+$")]
Url = Annotated[str, Field(min_length=1, pattern=r"^https?://")]
Count = Annotated[int, Field(ge=1)]
Source = Literal["university_faculty_roster", "hospital_doctor_roster"]


class StrictObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class RosterFields(StrictObject):
    source_kind: Source | None = None
    card_selector: Selector | None = None
    single_page: bool | None = None
    next_selector: Selector | None = None
    max_pages: Count | None = None
    initial_click_selector: Selector | None = None
    empty_state_selector: Selector | None = None
    roster_container_selector: Selector | None = None
    profile_content_selector: Selector | None = None
    profile_content_start_selector: Selector | None = None


class Branch(RosterFields):
    url: Url
    kind: Literal["roster", "navigation", "segmented", "image_table"] | None = None
    child_link_selector: Selector | None = None
    child_branch: RosterFields | None = None
    max_children: Count | None = None
    container_selector: Selector | None = None
    record_start_selector: Selector | None = None


class DiscoveredBranch(StrictObject):
    url: Url


class Discovery(StrictObject):
    school: Annotated[str, Field(min_length=1)]
    college: Annotated[str, Field(min_length=1)]
    source_kind: Literal["university_faculty_roster"]
    branches: Annotated[list[DiscoveredBranch], Field(min_length=1)]


class Plan(StrictObject):
    school: Annotated[str, Field(min_length=1)]
    college: Annotated[str, Field(min_length=1)]
    source_kind: Source | None = None
    branches: Annotated[list[Branch], Field(min_length=1)]
    profile_concurrency: Annotated[int, Field(ge=1, le=8)] | None = None
    profile_ready_selector: Selector | None = None


MODELS = {"discovery": Discovery, "branch": Branch, "full": Plan}
SAFE_FIELDS = set(Plan.model_fields) | set(Branch.model_fields)


def submission_schema(phase: str) -> dict:
    return MODELS.get(phase, Plan).model_json_schema()


def plan_digest(plan: Any) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _safe_path(loc: tuple) -> str:
    # Unknown property names may themselves contain teacher data. Never echo them.
    return "$" + "".join(f"[{part}]" if type(part) is int else f".{part}"
                          if part in SAFE_FIELDS else ".<field>" for part in loc)


def validate_submission(plan: Any, phase: str, target: dict | None,
                        events: list[dict]) -> list[dict]:
    """Schema, queued target and current-browser proof; never return input values."""
    try:
        MODELS.get(phase, Plan).model_validate(plan)
    except ValidationError as exc:
        return [{"path": _safe_path(e["loc"]), "status": "schema_" + e["type"]}
                for e in exc.errors()[:12]]
    if not target or not all(target.get(k) for k in ("school", "college", "url")):
        return [{"path": "$", "status": "queued_target_missing"}]
    from server.agent_recon import (
        PlanHygieneError, _is_structure_url, browser_url_aliases, check_plan_hygiene,
        observed_browser_urls, resolved_browser_url, selector_evidence_issues, validate_model_plan,
    )
    from server.browser_batch_tool import _validate_plan
    aliases = browser_url_aliases(events)
    if phase == "branch":
        complete = {"school": target["school"], "college": target["college"], "branches": [plan]}
        if resolved_browser_url(plan["url"], aliases) != resolved_browser_url(target["url"], aliases):
            return [{"path": "$.url", "status": "branch_target_changed"}]
    else:
        complete = plan
    if complete["school"] != target["school"] or complete["college"] != target["college"]:
        return [{"path": "$", "status": "queued_target_changed"}]
    try:
        check_plan_hygiene(complete, school=target["school"], college=target["college"])
        if phase == "discovery":
            observed = observed_browser_urls(events)
            issues = []
            for i, branch in enumerate(plan["branches"]):
                if not _is_structure_url(branch["url"]) or branch["url"] not in observed:
                    issues.append({"path": f"$.branches[{i}].url", "status": "url_not_observed"})
            resolved = {resolved_browser_url(b["url"], aliases) for b in plan["branches"]}
            if any(resolved_browser_url(url, aliases) not in resolved for url in target.get("required_urls", [])):
                issues.append({"path": "$.branches", "status": "previous_branch_omitted"})
            return issues
        _validate_plan(complete)
        validate_model_plan(complete, school=target["school"], college=target["college"])
    except (PlanHygieneError, ValueError, TypeError, KeyError):
        return [{"path": "$", "status": "structure_contract_rejected"}]
    # Evidence issues already use controlled status codes. Strip URLs/selectors as well.
    return [{"path": "$.branches" if phase == "full" else "$", "status": item["status"]}
            for item in selector_evidence_issues(complete, events)]


def submitted_plan(events: list[dict]) -> dict | None:
    """Read the last successful, digest-bound submission from Harness tool events."""
    calls, accepted = {}, None
    for event in events:
        data = event.get("data") or {}
        if event.get("type") == "tool/call" and data.get("name") == "mcp__playwright__submit_plan":
            try:
                calls[data["callId"]] = json.loads(data.get("arguments") or "{}").get("plan")
            except (ValueError, TypeError, KeyError):
                continue
        elif event.get("type") == "tool/result":
            for block in (data.get("message") or {}).get("content", []):
                candidate = calls.get(block.get("toolCallId"))
                if not isinstance(candidate, dict) or block.get("isError"):
                    continue
                for content in block.get("content", []):
                    if content.get("type") != "text":
                        continue
                    try:
                        result = json.loads(content.get("text", ""))
                    except (ValueError, TypeError):
                        continue
                    if (isinstance(result, dict) and result.get("accepted") is True
                            and result.get("digest") == plan_digest(candidate)):
                        accepted = candidate
    return accepted
