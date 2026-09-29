# 学院采集可靠性加固 Implementation Plan

> For agentic workers: implement task by task in this session. This workspace has no Git metadata, so commit steps do not apply.

**Goal:** Prevent silent roster omissions and false delivery success while preserving the college harvest and university workbook contract.

**Architecture:** Keep the existing recon, browser and deterministic extraction modules. Add narrow validation at the model-plan seam, fail-closed browser coverage checks, group-evidence name recall, and an explicit sync success result.

**Tech Stack:** Python 3.12, pytest, FastAPI, Playwright, BeautifulSoup, SQLite, openpyxl.

---

### Task 1: Model plan contract

**Files:** `server/agent_recon.py`, `tests/test_agent_recon_hygiene.py`

- [x] Add failing tests for extra model fields, mismatched target identity, and embedded address data.
- [x] Run `python -m pytest tests/test_agent_recon_hygiene.py -q` and confirm the intended failures.
- [x] Implement one strict validator at the `reconnoiter` return seam, preserving permitted URL and selector fields.
- [x] Run the target tests and confirm they pass.

### Task 2: Roster coverage

**Files:** `server/browser_batch_tool.py`, `server/harvest_workflow.py`, `tests/test_browser_batch_tool.py`

- [x] Add a failing browser fixture where a declared single page visibly contains a second page.
- [x] Add a failing test for a roster without a verifiable `card_selector`.
- [x] Run the target tests and confirm the intended failures.
- [x] Add a bounded pagination probe and fail closed on unverified coverage; require a card selector for roster reconciliation.
- [x] Run the target tests and confirm they pass.

### Task 3: Structural name recall

**Files:** `server/faculty_extractor.py`, `Opus/iso_mining.py` only if necessary, `tests/test_surname_prior_faculty_recall.py`

- [x] Add a failing test with an unlisted genuine surname in a repeated teacher-card group and a competing ordinary-text group.
- [x] Run the target test and confirm the expected missing candidate.
- [x] Admit low-prior names only when the record group supplies independent structural evidence.
- [x] Run recall and precision tests, then the full extractor tests.

### Task 4: Delivery status

**Files:** `server/agent_worker.py`, new `tests/test_agent_worker_delivery.py`

- [x] Add a failing test showing a sync failure produces a success log.
- [x] Run the target test and confirm the intended failure.
- [x] Make `_sync` return an explicit success result or raise, and log success only after completion.
- [x] Run the target test and confirm it passes.

### Task 5: Reproducible operations and documentation

**Files:** `requirements.txt`, dependency lock or constraints file, `server/agent_recon.py`, `server/browser_batch_tool.py`, `README.md`, `AGENT_HANDOVER.md`

- [x] Record compatible resolved dependency versions and make installation reproducible.
- [x] Make runtime paths configurable without depending on scratch or a Windows Chrome install.
- [x] Update user-facing state and test counts with the fresh verification result.
- [x] Run the full suite with `requirements.lock` in an isolated Python 3.12 environment and inspect every failure and warning.
