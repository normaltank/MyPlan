> 历史资料（2026-10-10 标记）：本文保留当时的方案、观察与验证记录，不作为当前工作指令、用户授权或能力验收。现行业务见 [CONTEXT.md](../../../CONTEXT.md)，开发任务按 [AGENTS.md](../../../AGENTS.md) 分工；使用原结论前核对代码版本、样本与原始证据。

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
