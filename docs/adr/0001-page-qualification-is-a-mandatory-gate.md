# Page qualification is a mandatory deterministic gate

> Status (2026-10-10): design intent; audit actual entry-path coverage before claiming migration is complete. Current faculty scope and delivery requirements come from [CONSTITUTION.md](../../CONSTITUTION.md). Deterministic qualification is the current implementation route, not a user-imposed ban on evidence-based architecture improvement.

Every GUI, CLI, discovery, search, Direct Lead, and imported legacy candidate path must pass the same deterministic Page Qualification before faculty extraction. Fast-track may prioritize source validation after acceptance, but does not replace the check for other faculty branches of the college; it never grants trust, old HIGH/100 values are not authority, and LLMs do not decide qualification. This deliberately trades some speed, legacy convenience, and edge recall for college-isolation safety, reproducibility, and auditability.

## Considered Options

- URL fast-track as automatic admission was rejected because URL tokens do not prove page purpose or College Attribution.
- Numeric score thresholds were rejected as the canonical decision because their additive values are not calibrated probabilities and lose hard constraints.
- LLM adjudication was rejected because qualification must remain deterministic, reproducible, and fixture-testable.
