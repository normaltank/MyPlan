# Authenticated Image Review Implementation Plan

**Goal:** Put task-bound image review in the logged-in Web app and verify two-employee isolation through independent browser sessions.

**Architecture:** Resolve audits only from a task's output directory after owner authorization. Reuse evidence-bound OCR and email decisions. Allow a failed image-review task to retry in place after decisions are complete, carrying those decisions into the resumed attempt. Keep the old local review sample separate.

**Tech Stack:** FastAPI, SQLite, Playwright, pytest, existing deterministic OCR review module.

---

### Task 1: Authorized audit resolution

- [x] Write failing API tests for owner/admin access and cross-account denial to task review.
- [x] Add a constrained task audit resolver and guarded review page route.
- [x] Run focused tests.

### Task 2: Review decision and resume

- [x] Write failing tests for CSRF, exact evidence decision, and allowed retry.
- [x] Reuse OCR/email decision stores and add safe retry of the same failed task.
- [x] Carry decision files to the resumed attempt; rerun focused tests.

### Task 3: Main page entry and multi-user acceptance

- [x] Add an owner-scoped review entry to task progress and a return link.
- [x] Use isolated database and independent real browser contexts for employee A, employee B, and admin. Verify submit, visibility, review, download, and daily statistics without model calls.
- [x] Run focused and full regression, inspect live service, update README and handover.
