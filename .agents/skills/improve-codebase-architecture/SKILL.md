---
name: improve-codebase-architecture
description: "Inspect a requested code area for architectural problems and compare evidence-backed improvements."
disable-model-invocation: true
---

# Review architecture

Use the user's named area or problem as the scope. When none is given, inspect recent changes and explain the selected area. Read CONTEXT.md for business contracts, relevant current ADRs for rationale, and actual callers/tests for behavior. A superseded ADR is historical evidence, not a prohibition on a fix.

Look for duplicated rules, scattered knowledge, unclear ownership, leaky interfaces and bugs that existing tests cannot exercise. The codebase-design vocabulary can help explain these issues; retain actual module names and ordinary technical terms where clearer.

For each candidate, provide the affected paths, a concrete symptom, the proposed boundary or interface change, tradeoffs and the checks needed to preserve existing behavior. A small module can be justified by ownership or lifecycle; size alone is not evidence that it should be merged.

Present a short explanation first. Use a diagram or an optional [HTML report](HTML-REPORT.md) when it materially helps compare alternatives or the user requests an artifact. Do not require a CDN-based report or a new file for every review.

For a review-only request, deliver findings. For an already authorized implementation, continue the scoped repair. Ask about unresolved product or architectural tradeoffs, not routine choices already covered by the task. Record a lasting decision in the existing ADR/work item only when its rationale is worth retaining.

Before replacing tests during restructuring, map each existing regression to a preserved or stronger check. Validate the claimed improvement and state where real-site evidence remains missing.
