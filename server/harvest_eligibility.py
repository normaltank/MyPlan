"""Eligibility policies applied after generic source reconciliation.

The browser workflow only knows that a candidate came from a source.  Whether
that source establishes the requested faculty relationship is a domain policy
and lives here, so adding a policy does not add a website adapter or change
browser traversal.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from bs4 import BeautifulSoup

from server.faculty_extractor import FacultyCandidate


def classify_candidates(
    candidates: list[FacultyCandidate],
    sources: dict[tuple[str, str], set[str]],
    profiles: dict[str, str | None],
    plan: dict[str, Any],
) -> tuple[list[FacultyCandidate], list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the configured source-evidence policy to reconciled candidates."""

    appointments = {
        (item["name"].strip(), item["profile_url"]): item
        for item in plan.get("verified_appointments", [])
    }
    reviewed_exclusions = {
        (item["name"].strip(), item["profile_url"]): item.get("reason", "reviewed_not_university_appointment")
        for item in plan.get("reviewed_exclusions", [])
    }
    admitted: list[FacultyCandidate] = []
    decisions: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for candidate in candidates:
        key = (candidate.name, candidate.url)
        source_kinds = sources.get(key, set())
        # The cleaned name is an identity field, not a replacement for the
        # original roster evidence (e.g. an annotation outside the name link).
        decision = {"name": candidate.name, "profile_url": candidate.url,
                    "roster_context": candidate.card_context}
        if "university_faculty_roster" in source_kinds:
            decision.update(status="eligible", evidence_kind="university_faculty_roster")
            admitted.append(candidate)
        elif "agent_verified" in source_kinds:
            decision.update(status="eligible", evidence_kind="agent_verified_source")
            admitted.append(candidate)
        elif "hospital_doctor_roster" in source_kinds:
            if key in reviewed_exclusions:
                decision.update(status="excluded", reason=reviewed_exclusions[key])
            else:
                html = profiles.get(candidate.url)
                appointment = appointments.get(key)
                if appointment:
                    page_text = BeautifulSoup(html or "", "html.parser").get_text("", strip=True)
                    compact_text = re.sub(r"\s+", "", page_text)
                    compact_quote = re.sub(r"\s+", "", appointment["quote"])
                    if compact_quote in compact_text:
                        admitted.append(
                            replace(candidate, title=appointment["academic_title"])
                        )
                        decision.update(
                            status="eligible",
                            evidence_kind="hospital_current_university_appointment",
                            quote=appointment["quote"],
                        )
                    else:
                        decision.update(status="unresolved", reason="appointment_evidence_unverified")
                        unresolved.append(
                            {
                                "url": candidate.url,
                                "name": candidate.name,
                                "status": "appointment_evidence_unverified",
                            }
                        )
                elif html is None:
                    decision.update(status="unresolved", reason="profile_unavailable")
                    unresolved.append({'url': candidate.url, 'name': candidate.name,
                                       'status': 'profile_unavailable'})
                else:
                    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
                    if plan["school"] in text or plan["college"] in text:
                        decision.update(status="unresolved", reason="possible_unreviewed_appointment")
                        unresolved.append(
                            {
                                "url": candidate.url,
                                "name": candidate.name,
                                "status": "possible_unreviewed_appointment",
                            }
                        )
                    else:
                        decision.update(status="excluded", reason="no_university_appointment_evidence")
        else:
            decision.update(status="unresolved", reason="source_qualification_missing")
            unresolved.append(
                {
                    "url": candidate.url,
                    "name": candidate.name,
                    "status": "source_qualification_missing",
                }
            )
        decisions.append(decision)
    return admitted, decisions, unresolved
