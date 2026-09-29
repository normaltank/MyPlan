# Employee Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give employees private workspaces, administrators a full view, and both roles accurate daily task counts.

**Architecture:** Reuse `tasks.db` for accounts and sessions. Persist task ownership at submission. Authorize every browser-facing read and download on the server; keep worker lease APIs local. Bind browser state to the logged-in user.

**Tech Stack:** FastAPI, SQLite, Python standard-library password hashing, existing inline HTML/JavaScript, pytest and Playwright.

---

### Task 1: Account and session storage

**Files:** Create `server/access_control.py`; test `tests/test_employee_isolation.py`.

- [x] Write and run a failing test for creating two employees and an admin, password verification, session expiry, logout, and disabled users.
- [x] Implement salted PBKDF2 password hashes and hashed random session tokens in SQLite. Require a valid CSRF token for session-backed mutations.
- [x] Run `pytest tests/test_employee_isolation.py -q` and confirm green.

### Task 2: Durable ownership and reports

**Files:** Modify `server/agent_queue.py`; test `tests/test_employee_isolation.py`.

- [x] Write and run a failing test that a batch gives every college the same owner, legacy rows stay unowned, and an employee's history excludes another owner's rows.
- [x] Add nullable `owner_user_id` migration and owner-aware single/batch insertion, history, queue counters, and per-day report queries.
- [x] Run focused tests and existing queue tests.

### Task 3: Authorize web routes and hide worker credentials

**Files:** Modify `server/app.py`; test `tests/test_employee_isolation.py`.

- [x] Write and run failing HTTP tests for anonymous access, employee A versus B detail/batch/download, admin access, and worker lease credential absence.
- [x] Add login/logout/me/account creation/report endpoints, owner checks, CSRF checks, local-only worker endpoints, and safe public task serialization.
- [x] Run focused HTTP tests. Verify worker renew/sync remains usable from loopback.

### Task 4: Employee-facing web page

**Files:** Modify `server/app.py`; test `tests/test_employee_isolation.py`.

- [x] Write and run browser/API tests for login, user-specific current request restore, logout, and daily counts.
- [x] Add login state, account label/logout, per-user current request key, daily totals, and admin-only account creation.
- [x] Run focused browser tests and `tests/test_web_current_request.py`.

### Task 5: Bootstrap, deployment and final verification

**Files:** Create `server/manage_users.py`; update `README.md` and `server/start_server.py` as needed.

- [x] Add an interactive local admin creation command and document how to create staff accounts.
- [x] Ensure remote HTTP cannot accept passwords or session cookies; document HTTPS reverse proxy requirement.
- [x] Run the full fixed-environment test suite and a two-user check against a temporary database. Report any startup or migration limitation.

Existing workspace has no Git repository, so no commit step is available.
