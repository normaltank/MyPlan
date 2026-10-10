# Refactoring reference

Use this reference for an authorized refactor. The actual task, project contracts, existing tests and CI determine the scope and completion criteria.

## Find a concrete reason to change the structure

Start with an observed failure, repeated maintenance cost or a responsibility that is split across incompatible implementations. Follow callers and stored data before choosing a design. Length, complexity, nesting and dependency counts can identify places to inspect; no universal numeric threshold requires splitting a function, adding an abstraction or raising coverage.

For a proposed change, explain:

- The behavior or invariant that must remain stable.
- The duplication, coupling or lifecycle problem it resolves.
- Which callers, data formats and historical regressions it affects.
- Why the change is worth its migration and verification cost.

## Choose a focused transformation

| Observed problem | Possible change | Check before applying |
| --- | --- | --- |
| Same rule implemented differently in several callers | Consolidate the rule behind a shared function | The callers actually have the same contract |
| One function combines parsing, policy and persistence | Extract the responsibility that changes independently | Ordering, transaction and failure behavior stay explicit |
| External I/O prevents meaningful isolated tests | Inject the dependency or isolate the effect | Integration tests still reach the real failure path |
| Public schema must change across many callers | Expand, migrate, then remove the old representation | Compatibility and stored data are covered at each step |
| Slow path has measured repeated work | Reduce queries, reuse results or change the algorithm | Measurements include invalidation, memory and correctness |

These are options, not commands to apply every pattern. A small direct implementation may be easier to maintain than an extra abstraction.

## Preserve evidence while changing code

Record relevant test results before the change where practical. Keep existing failure cases and successful examples; if a test is replaced, show where its distinct failure is still detected. Prefer assertions on meaningful outcomes, with focused internal or persistence checks when those are the contract at risk.

Make reviewable changes, inspect the diff, and run the checks required by the repository plus targeted verification of affected behavior. Use isolated databases and output directories. Report failed or unavailable checks accurately.

## Tooling and completion

Use the project's installed tools and configuration. A refactor does not authorize replacing Ruff settings, adding strict typing, installing review services or imposing a coverage quota. Toolchain changes need their own task justification and compatibility checks.

The work is complete when the requested structural problem is addressed, relevant behavior and historical regressions remain protected, required checks have final results, and remaining risks are recorded in the existing work item or PR. File length or a generic checklist cannot establish that outcome.
