# Page Qualification Architecture

> Status (2026-10-10): interface and migration design. Whether every production entry path follows this design remains a code-audit question, not an implementation guarantee. Current user requirements are in [CONSTITUTION.md](../../CONSTITUTION.md); task-specific routing is in [AGENTS.md](../../AGENTS.md). A supplied URL prioritizes validation but never removes the duty to inspect other faculty branches.

## Decision

Use an A/B-lite hybrid: a small qualification interface with mandatory audit metadata. Discovery remains outside the module; Page Qualification owns Page Snapshot acquisition and reuse, Page Evidence, College Attribution, page-purpose classification, and the deterministic outcome.

```python
class SnapshotLoader(Protocol):
    def load(self, *, run_id: str, lead: PageLead) -> PageSnapshot: ...


@dataclass(frozen=True)
class QualificationRequest:
    target: CollectionTarget
    lead: PageLead


class PageQualification:
    def __init__(self, snapshots: SnapshotLoader): ...

    def qualify(
        self,
        *,
        run_id: str,
        requests: Iterable[QualificationRequest],
    ) -> QualificationLedger: ...


@dataclass(frozen=True)
class QualificationLedger:
    run_id: str
    policy_revision: str
    decisions: tuple[QualificationDecision, ...]

    @property
    def faculty_source_pages(self) -> tuple[FacultySourcePage, ...]: ...
```

`QualificationDecision` contains the Collection Target, Page Lead, outcome, College Attribution, page kind, stable reason codes, Page Snapshot identity and digest, and retryability. Only an accepted decision contains a Faculty Source Page, which carries the same Page Snapshot and any acquired document evidence into extraction.

## Invariants

- Every Page Lead produces exactly one decision.
- Every entry path uses the same `qualify` interface.
- A Direct Lead affects scheduling only; it never increases trust.
- College Attribution conflict always rejects the Page Lead.
- Unknown College Attribution never produces an accepted decision.
- Only a matched Faculty Directory, Faculty Profile, or Faculty Document can be accepted.
- An unavailable Page Snapshot produces an unavailable decision rather than a rejection.
- Qualification never invokes an LLM.
- A normalized URL is acquired once per qualification call and may be evaluated independently for multiple Collection Targets.
- Legacy score, recommendation level, page type, and signals are audit metadata only.
- Extraction consumes Faculty Source Pages, not bare URLs or legacy candidate rows.

## Seam placement

`SnapshotLoader` is the only caller-supplied public external seam. It has two real adapters from the start:

- Browser adapter for production acquisition, using the approved Playwright context and preserving rendered snapshots.
- Fixture adapter for deterministic offline contract tests.

Discovery and search remain above the module. Their implementations already exist and can evolve independently. A locator seam is not introduced because there is currently only one locator implementation.

## Hidden implementation

The module hides URL normalization, redirect aliasing, request deduplication, Page Snapshot parsing, Page Evidence generation, College Attribution, hard negative page intents, Faculty Source Page kind, document-text acquisition, OCR gating, reason-code selection, and the policy revision. Internal document readers may be replaced by tests, but they are not part of the public module interface.

## Rejected designs

- A full Policy Ledger exposing snapshots and ordered rule traces would freeze internal rule order into the interface.
- A one-shot module that also performs discovery would couple crawl budgets and locator failures to qualification policy.
- Numeric score and recommendation fields would preserve the current ambiguous authority model.

## Migration

Use expand-contract. Add the new decisions and compatibility columns first, route all entry paths through qualification, make extraction accept only Faculty Source Pages, then stop producing legacy score and recommendation values after every caller has migrated.
