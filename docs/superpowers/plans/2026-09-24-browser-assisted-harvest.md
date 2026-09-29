# Browser-Assisted Faculty Harvest Implementation Plan

**Goal:** Let a college Subagent choose a bounded browser batch tool after inspecting the real site, while keeping full records out of the agent context and returning coverage evidence.

**Architecture:** A plan supplied by the Subagent names official roster URLs and, where needed, selectors for tabs or next-page controls. One Playwright browser context captures every listed page and personal profile. Existing structural and MECC extractors process the captured HTML; the existing Excel exporter writes the college workbook only when extraction has no unresolved items. A compact audit report routes uncertain pages back to the Subagent. The tool is callable as a Python module or with an already-owned Playwright context; the current host does not expose a transferable page handle, so the CLI starts its own real browser context. No standalone HTTP scraper is used.

**Tech stack:** Python, Playwright, `server.faculty_extractor`, pytest, openpyxl.

---

### Task 1: Bounded browser capture and extraction

**Files:** Create `server/browser_batch_tool.py`, `server/browser_poster_tool.py`, `server/browser_batch_sync.py`; test `tests/test_browser_batch_tool.py`, `tests/test_browser_poster_tool.py`, `tests/test_browser_batch_sync.py`.

- [x] Test a supplied roster with two pages and profiles: browser pages are visited, full biographies are retained, and compact audit data is returned.
- [x] Test unchanged dynamic pagination, failed profiles, missing identity, and zero candidates: each produces an unresolved audit item and blocks export.
- [x] Implement `run_plan(plan, browser_context, output_dir)` and a CLI that starts one Playwright browser context. The plan supplies `school`, `college`, and `branches`, each with `url`, optional `initial_click_selector`, optional `next_selector`, and a finite `max_pages`.
- [x] Reuse `FacultyDirectoryExtractor`, `FacultyHarvestPipeline`, and `AutonomousBrowserAgent.export_dual_sheet_excel`. Persist records, page snapshots, and audit JSON locally; print only status, counts, and paths.
- [x] Add browser screenshot OCR for image rosters, agent-verified image candidates, and file-based sync with university workbook generation.
- [x] Renew the claimed queue lease during browser capture, and block sync if renewal fails.
- [x] Trigger lazy poster image loading and require full-image review with a verified visible headcount before export.
- [x] Run focused browser batch, poster OCR, and sync tests with zero failures.

### Task 2: Progressive tool disclosure and policy alignment

**Files:** Modify `AGENTS.md`, `.agents/skills/faculty-browser-harvest/SKILL.md`, and `AGENT_HANDOVER.md`; create `.agents/skills/faculty-browser-harvest/TOOLS.md`.

- [x] Keep the main skill short: route to the browser batch tool when the Subagent has verified roster entries and page controls; load `TOOLS.md` only for a relevant branch.
- [x] Clarify that a Subagent may invoke the approved Playwright batch tool after real-browser reconnaissance. Every website page remains captured by a real browser. An HTTP-only script cannot take over harvesting.
- [x] Prevent the old queue daemon, server worker and single-task helper from claiming work through the retired script-led harvester; the daemon remains a read-only queue monitor.
- [x] Document the host limit: no transfer of a host MCP page object into local Python is currently available. Do not claim one shared host session or automatic IDE dispatch when the host lacks it.
- [x] Keep one-college-one-Subagent, no external commercial LLM calls, direct full-text extraction, university-level delivery, and dual-sheet partitioning.

### Task 3: Verification and audit

**Files:** `tests/test_browser_batch_tool.py`, `docs/superpowers/plans/2026-09-24-browser-assisted-harvest.md`.

- [x] Run focused and existing regression tests. Confirm new website capture modules have no HTTP-only website requests.
- [x] Check generated workbook's exact two sheets, ten columns, and count conservation; verify that unresolved work prevents delivery.
- [x] Report browser visits, candidate/profile counts, failures, and limitations. Do not claim site-wide coverage or Token savings without measurement.

**Verification:** A local HTTP fixture exercised a real Chromium session with AJAX pagination. `python -m pytest -q tests` passed: 99 passed, 1 skipped because the referenced `archive/legacy_v1` directory is absent. Changed Python modules compiled, and the batch CLI exposed its lease arguments. The new audit includes elapsed time, browser page counts, card-count reconciliation, and eligibility decisions; no production Token or throughput baseline was measured.

**Workspace note:** This directory is not a Git repository, so no commit step is possible here.
