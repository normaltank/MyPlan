"""One recovery policy for audit codes; never inspect teacher text or raw errors."""
from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal
from urllib.parse import quote, unquote, urlsplit

Action = Literal["access", "structure", "review", "stop"]

# These are internal machine codes, not content keywords or extraction filters.
_GROUPS = {
    "access": """profile_navigation_failed browser_navigation_failed image_bio_asset_failed
        segment_name_asset_failed pdf_roster_asset_failed table_asset_failed recon_page_unverifiable""",
    "review": """rare_surname_needs_review segment_name_needs_review distinct_profile_identity_unverified
        zero_person_delivery_requires_review image_bio_email_conflict image_bio_low_confidence
        image_bio_ocr_incomplete image_bio_owner_unresolved image_bio_unreadable_profile missing_profile_media_needs_review
        segment_image_bio_unassigned segment_name_unreadable segment_duplicate_name segment_email_ambiguous
        ambiguous_email_assignment ambiguous_shared_email ambiguous_surname_email conflicting_card_email
        appointment_evidence_unverified possible_unreviewed_appointment source_qualification_missing profile_unavailable
        profile_identity_not_found poster_full_image_review_missing embedded_pdf_roster_requires_review
        title_field_conflict excel_cell_too_long excel_invalid_xml_character mailto_address_conflict
        pdf_roster_not_pdf pdf_roster_unreadable pdf_roster_page_unreadable pdf_roster_raster_unread
        pdf_roster_records_not_found pdf_roster_heading_unrecognized pdf_roster_multiple_names
        pdf_roster_shared_email pdf_roster_table_row_unassigned pdf_roster_table_index_gap
        table_grid_ambiguous table_header_unresolved table_cell_low_confidence table_cell_unreadable
        table_text_unassigned table_name_segmentation_ambiguous table_full_image_review_missing
        table_index_gap table_row_unassigned table_name_unreadable table_duplicate_name
        table_email_ambiguous table_record_unresolved""",
    "structure": """card_selector_empty card_selector_missing roster_selector_empty candidate_outside_cards child_limit_reached
        child_link_missing_href child_outside_verified_host child_roster_not_verified child_selector_empty
        empty_roster_conflict empty_roster_not_verified initial_control_not_unique initial_control_unchanged
        next_control_not_unique no_candidates_on_roster_page page_limit_reached pagination_not_verified
        pagination_unchanged pagination_cycle plan_contract_rejected planned_branch_not_captured branch_termination_unverified
        poster_coverage_mismatch previous_branch_omitted profile_body_disagreement profile_content_browser_unverified
        profile_content_not_verified profile_content_selector_conflict profile_content_selector_invalid
        profile_content_selector_missing profile_content_selector_multiple profile_content_selector_unsafe
        profile_content_selector_hidden profile_content_selector_empty profile_content_start_selector_invalid
        profile_content_start_selector_missing profile_content_start_selector_multiple
        profile_content_start_selector_not_direct_child profile_content_start_selector_empty
        profile_content_start_selector_without_container roster_card_mismatch segment_container_not_unique
        segment_multiple_names segment_name_missing segment_start_empty segment_start_nested segments_not_verified
        selector_not_verified single_page_pagination_unverified single_page_has_next image_roster_coverage_mismatch
        name_list_item_unrecognized table_image_selector_unverified recon_gate_stalled
        roster_profile_link_outside_card source_role_evidence_unverified""",
    "stop": """lease_renewal_failed repair_skipped_task_deadline recon_budget_exceeded recon_phase_limit
        recon_failed task_timeout missing_entry_url delivery_sync_failure missing_audit_file""",
}
POLICIES: dict[str, Action] = {code: action for action, codes in _GROUPS.items() for code in codes.split()}
# A valid container can fail its live proof without its selector being wrong.
PROFILE_PROOF_POLICIES: dict[str, Action] = {"dom_unstable": "access", "network_not_idle": "access"}
NON_STRUCTURAL = frozenset(code for code, action in POLICIES.items() if action != "structure")


def recovery_action(code: str | None) -> Action:
    if isinstance(code, str) and (code == "fetch_failed" or code.startswith("fetch_failed:")):
        return "access"
    return POLICIES.get(code, "stop")


def _url_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {'http', 'https'} or not parsed.netloc:
            return None
    except ValueError:
        return None
    return quote(value, safe=":/?#[]@!$&'()*+,;=%")


def profile_access_failures(issues: list[dict]) -> set[str]:
    """URL keys with failed-fetch evidence; a bad body alone proves no access failure."""
    return {key for item in issues
            if item.get('status') == 'profile_navigation_failed'
            or (isinstance(item.get('status'), str)
                and (item['status'] == 'fetch_failed' or item['status'].startswith('fetch_failed:')))
            if (key := _url_key(item.get('url') or item.get('主页'))) is not None}


def profile_issue_is_access_only(issue: dict, failed_urls: set[str]) -> bool:
    code = issue.get('status')
    return (isinstance(code, str) and code.startswith('profile_content_')
            and POLICIES.get(code) == 'structure'
            and (key := _url_key(issue.get('url'))) is not None and key in failed_urls)


def safe_failure_code(code: object) -> str:
    """Persist only our machine vocabulary; raw or future codes still stop safely."""
    if isinstance(code, str):
        if code == 'fetch_failed' or code.startswith('fetch_failed:'):
            return 'fetch_failed'
        if code in POLICIES or code in RECON_RETRY:
            return code
    return 'unknown_failure'


def safe_failure_issues(issues: list[dict]) -> list[dict]:
    """Keep cause metadata for queue routing, never teacher fields or raw errors.

    URLs that expose credentials/email are left out; absent context cannot
    justify downgrading a body failure. The unchanged local audit keeps the
    original URL and failure evidence.
    """
    result = []
    for item in issues:
        if not isinstance(item, dict):
            result.append({'status': 'unknown_failure'})
            continue
        safe = {'status': safe_failure_code(item.get('status'))}
        for url_field in ('url', 'branch_url'):
            value = item.get(url_field) or (item.get('主页') if url_field == 'url' else None)
            key = _url_key(value)
            if key is None:
                continue
            parsed = urlsplit(key)
            if parsed.username is not None or parsed.password is not None:
                continue
            if re.search(r'[\w.+-]+@[\w.-]+\.[a-z]{2,}', unquote(key), re.I):
                continue
            safe[url_field] = key
        proof = item.get('browser_evidence')
        reason = proof.get('reason') if isinstance(proof, dict) else None
        if isinstance(reason, str) and reason in PROFILE_PROOF_POLICIES:
            safe['browser_evidence'] = {'reason': reason}
        result.append(safe)
    return result


@dataclass
class Recovery:
    access: list[dict] = field(default_factory=list)
    structure: list[dict] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    stop: list[dict] = field(default_factory=list)

    def summary(self) -> dict:
        return {name: {"count": len(items), "codes": sorted({str(i.get("status", "unknown")) for i in items})}
                for name in ("access", "structure", "review", "stop")
                if (items := getattr(self, name))}


def classify_issues(issues: list[dict]) -> Recovery:
    """Coverage errors caused by an unavailable branch follow its access retry."""
    failed_branches = {value for item in issues if item.get("status") == "browser_navigation_failed"
                       for value in (item.get("url"), item.get("branch_url")) if value}
    failed_profiles = profile_access_failures(issues)
    result = Recovery()
    for item in issues:
        code = item.get("status")
        action = recovery_action(code)
        if code == "profile_content_browser_unverified":
            proof = item.get('browser_evidence')
            reason = proof.get('reason') if isinstance(proof, dict) else None
            if isinstance(reason, str):
                action = PROFILE_PROOF_POLICIES.get(reason, action)
        if profile_issue_is_access_only(item, failed_profiles):
            action = 'access'
        if code in {"branch_termination_unverified", "planned_branch_not_captured"}:
            if (item.get("url") or item.get("branch_url")) in failed_branches:
                action = "access"
        getattr(result, action).append(item)
    return result


Disposition = Literal["review", "access", "structure", "recon", "failed"]
# A scout that ended without a plan can be scouted again; budget and cost stops cannot.
RECON_RETRY = frozenset({"recon_failed", "recon_timeout", "recon_failure", "recon_plan_invalid"})
# Every way a scout ends without a plan: it never reached a roster, so a person
# checks the official site (and may judge the college to have no faculty).
SCOUT_WITHOUT_PLAN = RECON_RETRY | {"recon_budget_exceeded", "recon_phase_limit", "recon_cost_guard_manual_stop"}


def scout_found_no_roster(codes: list[str]) -> bool:
    return bool(codes) and set(codes) <= SCOUT_WITHOUT_PLAN


def failure_disposition(codes: list[str], issues: list[dict] | None = None) -> Disposition:
    """Where a failed college goes next. The most demanding action wins: a review
    cannot settle a structure problem, and nothing settles a stop."""
    if issues is None:
        if RECON_RETRY & set(codes):
            return "recon"
        actions = {recovery_action(code) for code in codes}
    else:
        context = safe_failure_issues(issues)
        covered = {item['status'] for item in context}
        # A compact summary may include an independent error absent from the
        # context. Never lose that stop/scope error when restoring URL causes.
        context.extend({'status': safe_failure_code(code)} for code in codes
                       if safe_failure_code(code) not in covered)
        retry = any(item['status'] in RECON_RETRY for item in context)
        recovery = classify_issues([item for item in context if item['status'] not in RECON_RETRY])
        actions = {action for action in ('access', 'structure', 'review', 'stop')
                   if getattr(recovery, action)}
        if retry and 'stop' not in actions:
            return 'recon'
    for action, disposition in (("stop", "failed"), ("structure", "structure"),
                                ("access", "access"), ("review", "review")):
        if action in actions:
            return disposition
    return "failed"
