# [SUPERSEDED] Two-tier hybrid harvesting pipeline with organizational reconciliation and circuit breaking

> ⚠️ **Status: SUPERSEDED / HISTORICAL REFERENCE ONLY**  
> **Status Date**: 2026-09-24  
> **Superseded by**: **College-Level Subagent and Browser Batch Tool Workflow** (documented in [`AGENTS.md`](../../AGENTS.md) and [`faculty-browser-harvest/SKILL.md`](https://github.com/normaltank/MyPlan/blob/0eaa7f5f69b9a30cfbc9975565a25ad7f3f73a6f/.agents/skills/faculty-browser-harvest/SKILL.md)).
> **Note**: This ADR records an earlier experimental architecture that attempted to use a Tier 1 static HTTP batch script (`batch_browser_extractor.py`) prior to browser interaction. **This document is preserved strictly for historical engineering reference and MUST NOT be used to guide current agent dispatching or harvesting behavior.**  
> Under the current active workflow:
> 1. Harvesting is conducted per college by dedicated browser Subagents where the host provides that capability.
> 2. Subagents first verify official roster pages with the host's browser, then may invoke an approved Playwright browser batch tool for bounded repetitive browsing. A separate HTTP-only crawler cannot take over website harvesting.
> 3. As of 2026-10-01, the legacy batch extractor, harvest template and DOM dehydrator are fail-closed import/CLI entry points. Historical implementations remain in Git; offline rendered-HTML tests use `server.faculty_extractor` instead.
> 4. Fixed throughput and token claims (e.g. "<5s", "90%", "0 token promises") from this historical exploration are not active operational guarantees or decision drivers.

---

## Context and Problem Statement
The previous faculty harvesting workflow relied heavily on multi-turn LLM agent interactions in the browser (navigating, clicking links, inspecting snapshots, and summarizing profiles). Over a 5-hour continuous run across ~10 colleges, this caused severe context snowballing, consuming 249M cumulative tokens (99.6% input token tax) and resulting in significant cost and latency.
At the same time, falling back purely to rigid static scripts risked zero-candidate drops or missing departments on non-standard, dynamic, or paginated university sites.

We need to balance the classic Trilemma:
1. **Token Cost**: Ultra-low consumption (<20M tokens per batch).
2. **Harvesting Latency**: High throughput (<5s for standard colleges).
3. **Data Accuracy & Recall**: 100% Dual-Sheet Conservation ($Sheet 1 + Sheet 2 = Total$), zero hallucination, zero information loss.

## Decision Drivers
* "Dual-Sheet Conservation Axiom" must be strictly maintained (100% recall of full-time faculty, zero omissions).
* Academic biographies must be full text verbatim copies without AI summarization.
* Third-party LLM APIs remain strictly forbidden.
* Cost must be predictable and bounded even on pathological college websites.

## Considered Options
1. **Pure Multi-turn LLM Agent Navigation**: High accuracy on edge cases, but unsustainable token burn (249M tokens) and slow latency (~3-5 mins/college).
2. **Pure Static Parser Script**: Zero token cost and sub-second execution, but high fragility; drops to 0 candidates on dynamic SPA/AJAX sites.
3. **Two-Tier Hybrid Pipeline with Organizational Reconciliation and Hard Circuit Breaking (Chosen)**:
   - **Tier 1 (Fast-Path)**: Try in-browser concurrent batch extraction script (`batch_browser_extractor.py`) first. If candidates are extracted and match organizational structure, export dual-sheet Excel immediately.
   - **Tier 2 (Deep-Path Fallback)**: If Fast-Path returns 0 candidates or reveals structural gaps (e.g. pagination not finished, unvisited departments), fallback to Playwright interactive Subagent.
   - **Circuit Breaker**: Deep-Path is hard-capped at **10 interaction steps** to strictly bound token expenditure.
   - **Ground Truth Inferred Count**: Derived by summing leaf nodes of the department/research office tree (highest authority, supersedes static CMS counters).
   - **Profile-less Faculty**: Scholars with only name/title on directory lists have their bio left blank, list URL as homepage, and are placed directly into Sheet 2 (No-Email) without costly external searches.

## Consequences
* **Positive**:
  - 90% of standard colleges complete in ~3 seconds with 0 LLM generation tokens.
  - Overall batch token consumption drops by >90% (from ~249M to <20M).
  - Non-standard colleges are automatically rescued by Tier 2 without user intervention.
  - Hard circuit breaker prevents token drain on adversarial/broken websites.
* **Negative**:
  - Tier 2 fallback logic requires maintaining the Playwright MCP interaction toolchain alongside the batch script.
