---
name: diagnosing-bugs
description: "Diagnose a concrete bug or performance regression with evidence, controlled experiments and a meaningful regression check."
---

# Diagnose a bug

Read the reported symptom, relevant code, current task and existing evidence. Use CONTEXT.md for the intended behavior and check the status of related ADRs. Preserve the original failing material and distinguish live execution, saved-page replay and static reasoning.

## Establish a useful check

Prefer a failing test through the affected call chain. A bounded CLI run, isolated HTTP request, browser fixture or saved trace may be more appropriate. Reading source and logs can help construct this check. Make it as small and quick as practical without removing the behavior that matters; browser and integration checks may legitimately take minutes.

Run the check and record its actual outcome. If the required environment or material is unavailable, state what cannot yet be verified, continue useful source inspection or isolated experiments, and ask only for the missing information that blocks the next dependent step. A static finding remains a static finding until executed evidence supports it.

## Distinguish causes

For ambiguous bugs, rank a few plausible causes and state the observation that would distinguish each. For a direct, reproducible defect, test the evidenced cause without inventing additional hypotheses to meet a quota.

Change one relevant variable per experiment. Use focused instrumentation or a debugger; redact secrets from commands, logs and artifacts. Performance work needs a measured baseline. Repetition and stress belong in an isolated test environment with bounded resources, not unbounded live website or paid-model traffic.

## Fix and verify

Where a meaningful seam exists, capture the original failure in a regression test before applying the fix. Verify the changed behavior, related historical cases and the original scenario. If no suitable automated check is available, describe the validation limit and the remaining evidence needed.

Remove this task's temporary instrumentation and disposable artifacts, retain reusable evidence in the existing work item, and report the cause, change, actual checks and remaining uncertainty. A green synthetic check alone does not establish real-site accuracy or completeness.
