"""Structure-only submission contracts and safe, immediate scout feedback."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, TypeAdapter, model_validator
from server.source_qualification import SourceRole

Selector = Annotated[str, Field(min_length=1, pattern=r"^[^\u4e00-\u9fff]+$")]
Url = Annotated[str, Field(min_length=1, pattern=r"^https?://")]
Count = Annotated[int, Field(ge=1)]
Source = Literal["university_faculty_roster", "hospital_doctor_roster"]


class StrictObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceFields(StrictObject):
    source_role: SourceRole
    source_container_selector: Selector
    role_evidence_selector: Selector
    source_context_selector: Selector


class RosterFields(SourceFields):
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


class DiscoveredBranch(SourceFields):
    url: Url


class UnresolvedSource(StrictObject):
    url: Url
    reason: Literal['source_unreadable', 'source_structure_unresolved']


class ReadableSourceDisposition(SourceFields):
    url: Url
    reason: Literal['source_relationship_unverified', 'not_faculty_roster']


SourceDisposition = UnresolvedSource | ReadableSourceDisposition


def validate_source_dispositions(plan: dict) -> None:
    """Shared model/batch shape boundary; an empty plan proves no absence."""
    TypeAdapter(list[SourceDisposition]).validate_python(plan.get('source_dispositions', []), strict=True)
    if not plan.get('branches') and not plan.get('source_dispositions'):
        raise ValueError('no branches or source dispositions')
    urls = [item['url'] for item in [*plan.get('branches', []), *plan.get('source_dispositions', [])]]
    if len(urls) != len(set(urls)):
        raise ValueError('source occurs in multiple dispositions')
    from server.agent_recon import _is_structure_url
    if any(not _is_structure_url(item['url']) for item in plan.get('source_dispositions', [])):
        raise ValueError('invalid source disposition URL')


class SourceCollection(StrictObject):
    model_config = ConfigDict(json_schema_extra={'anyOf': [
        {'properties': {'branches': {'minItems': 1}}},
        {'required': ['source_dispositions'], 'properties': {'source_dispositions': {'minItems': 1}}},
    ]})

    @model_validator(mode='after')
    def require_source_records(self):
        validate_source_dispositions(self.model_dump(exclude_none=True))
        return self


class Discovery(SourceCollection):
    school: Annotated[str, Field(min_length=1)]
    college: Annotated[str, Field(min_length=1)]
    source_kind: Source | None = None
    branches: list[DiscoveredBranch]
    source_dispositions: list[SourceDisposition] = Field(default_factory=list)


class Plan(SourceCollection):
    school: Annotated[str, Field(min_length=1)]
    college: Annotated[str, Field(min_length=1)]
    source_kind: Source | None = None
    branches: list[Branch]
    source_dispositions: list[SourceDisposition] = Field(default_factory=list)
    profile_concurrency: Annotated[int, Field(ge=1, le=8)] | None = None
    profile_ready_selector: Selector | None = None


MODELS = {"discovery": Discovery, "branch": Branch, "full": Plan}
SAFE_FIELDS = set(Plan.model_fields) | set(Branch.model_fields) | {'reason'}


def submission_schema(phase: str) -> dict:
    return MODELS.get(phase, Plan).model_json_schema()


def plan_digest(plan: Any) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _safe_path(loc: tuple) -> str:
    # Unknown property names may themselves contain teacher data. Never echo them.
    return "$" + "".join(f"[{part}]" if type(part) is int else f".{part}"
                          if part in SAFE_FIELDS else ".<field>" for part in loc)


def navigation_observations(events: list[dict]) -> dict[str, dict]:
    """Only actual navigation results, never links or submitted URL labels."""
    calls, found = {}, {}
    for event in events:
        data = event.get('data') or {}
        if event.get('type') == 'tool/call' and data.get('name') in (
                'mcp__playwright__browser_navigate', 'mcp__playwright__browser_survey'):
            try:
                calls[data['callId']] = (data['name'], json.loads(data.get('arguments') or '{}'))
            except (ValueError, KeyError):
                continue
        elif event.get('type') == 'tool/result':
            for block in (data.get('message') or {}).get('content', []):
                call = calls.get(block.get('toolCallId'))
                if not call:
                    continue
                for content in block.get('content', []):
                    if content.get('type') != 'text':
                        continue
                    try:
                        result = json.loads(content.get('text', ''))
                    except ValueError:
                        continue
                    if not isinstance(result, dict):
                        continue
                    name, args = call
                    if name.endswith('browser_survey'):
                        for item in (result.get('pages') or {}).get('items', []):
                            if isinstance(item, dict) and item.get('url') in args.get('urls', []):
                                found[item['url']] = item
                    elif result.get('requested_url', result.get('url')) == args.get('url'):
                        found[args['url']] = result
    return found


def disposition_event_issues(plan: dict, events: list[dict]) -> list[dict]:
    from server.source_qualification import source_event_proof
    visits = navigation_observations(events)
    issues = []
    for item in plan.get('source_dispositions', []):
        reason, url = item['reason'], item['url']
        if reason in {'not_faculty_roster', 'source_relationship_unverified'}:
            proof = source_event_proof(item, events)
            valid = proof is not None
            if reason == 'not_faculty_roster':
                valid = valid and proof.get('non_faculty_purpose') is True
            else:
                valid = valid and item['source_role'] == 'unverified_people_source'
        else:
            visit = visits.get(url, {})
            if reason == 'source_unreadable':
                valid = bool(visit.get('error') or (type(visit.get('status')) is int and visit['status'] >= 400))
            else:
                valid = bool(re.fullmatch(r'[0-9a-f]{64}', str(visit.get('source_html_sha256', '')))
                             and not visit.get('error'))
        if not valid:
            issues.append({'url': url, 'status': 'source_disposition_unverified'})
    return issues


def discovery_source_issues(plan: dict, target: dict, events: list[dict]) -> list[dict]:
    from server.agent_recon import _is_structure_url, browser_url_aliases, observed_browser_urls, resolved_browser_url
    from server.source_qualification import source_event_issues
    observed = observed_browser_urls(events)
    issues = disposition_event_issues(plan, events)
    for i, branch in enumerate(plan['branches']):
        if not _is_structure_url(branch['url']) or branch['url'] not in observed:
            issues.append({'path': f'$.branches[{i}].url', 'status': 'url_not_observed'})
        issues.extend({'path': f'$.branches[{i}]', **item} for item in source_event_issues(branch, events))
    aliases = browser_url_aliases(events)
    all_sources = [*plan['branches'], *plan.get('source_dispositions', [])]
    resolved = {resolved_browser_url(item['url'], aliases) for item in all_sources}
    for url in target.get('required_urls', []):
        if resolved_browser_url(url, aliases) not in resolved:
            issues.append({'path': '$.branches', 'url': url, 'status': 'previous_branch_omitted'})
    return issues


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
        PlanHygieneError, browser_url_aliases, check_plan_hygiene,
        resolved_browser_url, selector_evidence_issues, validate_model_plan,
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
        validate_source_dispositions(complete)
        check_plan_hygiene(complete, school=target["school"], college=target["college"])
        if phase == "discovery":
            return discovery_source_issues(plan, target, events)
        _validate_plan(complete)
        validate_model_plan(complete, school=target["school"], college=target["college"])
    except (PlanHygieneError, ValueError, TypeError, KeyError):
        return [{"path": "$", "status": "structure_contract_rejected"}]
    # Evidence issues carry only controlled codes, counts, and the URLs and
    # selectors this plan already passed hygiene with or this browser observed.
    # Without them a scout knows a check failed but not where, and resubmits blind.
    return [{"path": "$.branches" if phase == "full" else "$", **item}
            for item in [*disposition_event_issues(complete, events),
                         *selector_evidence_issues(complete, events, target.get("repair_profiles"))]]


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
