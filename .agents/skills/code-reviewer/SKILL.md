---
name: code-reviewer
description: "Supplement a requested review with a focused security, configuration or reliability checklist."
---

# Focused review checklist

Use code-review for the project's normal diff review. Consult this checklist only for a concrete risk in the requested scope:

- Inputs crossing a trust boundary: validation, authorization and output handling.
- Files, database transactions and tasks: ownership, atomicity, cancellation and cleanup.
- Configuration or dependency changes: actual locked version, compatibility and failure behavior.
- Collection and delivery: trace the relevant CONTEXT.md contract through callers and evidence.

For each finding, identify the affected path, trigger, consequence and supporting evidence. Distinguish confirmed defects from hypotheses. Run relevant available checks where useful; a list of commercial tools is not evidence and does not justify installing services or expanding the task.

Report actionable issues with precise locations and validation limits. This reference does not select a model, assign an unsolicited audit or establish new project standards.
