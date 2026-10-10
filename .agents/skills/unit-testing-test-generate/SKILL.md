---
name: unit-testing-test-generate
description: "Add meaningful tests for specified existing behavior or a reproduced defect."
---

# Add tests with independent expectations

Read the current request, affected callers and nearby tests. Use the project's test configuration and fixtures. For MyPlan, consult README.md for checks and CONTEXT.md for the business behavior; isolate databases, outputs and external effects.

Choose cases that expose the actual risk: the original failure, a relevant boundary case and an existing success that must remain valid. Derive expected values from the contract or independently verified fixture, not by copying the implementation's calculation.

Assertions must distinguish correct from incorrect behavior. A blanket `result is not None` or `raises(Exception)` is not a substitute for checking the expected records, field ownership, completeness scope, state or specific error. Do not infer that empty input must fail merely from a function's signature.

Use real collaborators where practical and focused substitutes for unavailable or expensive boundaries. Keep a separate integration check where a mock omits the behavior under investigation. Preserve existing regression cases unless their replacement demonstrably covers them.

Run the affected tests and report actual results. Coverage can identify unexecuted code, but it does not establish correctness and there is no skill-imposed percentage target. Do not generate placeholder suites, add a second test framework or change CI merely to satisfy this template.
