---
name: to-tickets
description: Break a plan, spec, or conversation into independently verifiable work items with explicit dependencies, using the project's existing tracker.
disable-model-invocation: true
---

# To Tickets

Use `docs/agents/issue-tracker.md` and the user's current task or referenced work item. Read the referenced source and relevant code before choosing scope. Existing work items remain the source of truth; setup and a separate spec file are not prerequisites.

## Draft the breakdown

- Give each ticket a concrete outcome, acceptance criteria and the dependencies that actually block it.
- Prefer small, independently verifiable slices. Include only the layers the behavior touches; a backend fix need not invent UI work.
- Keep refactoring tied to the requested change. For a wide migration, consider expand, migrate and contract phases that preserve compatibility and regressions.
- Use current paths and symbols when they clarify the scope. Size work by dependencies and verification needs, not a fixed context-window estimate.

Show the breakdown with titles, outcomes and blockers. Resolve material uncertainty with the user; carry out an already authorized breakdown or publication without an additional approval loop.

## Publish within the requested scope

Use the existing tracker and reuse matching issues. Create child work items when the requested breakdown needs them. Record dependencies with native links when supported, otherwise with links in the issue body. Apply labels only when the actual tracker configuration maps them and they exist. Without a mapping, record readiness in prose; do not create a label scheme from a skill template.

Local scratch files can hold a draft. They are not a parallel tracker or a shared deliverable. Keep the parent issue linked and update it when that is part of the user's requested workflow; do not close it merely because tickets were created.

## Ticket template

```markdown
## Parent
<existing issue or spec, when relevant>

## Outcome
<behavior this ticket makes work>

## Acceptance criteria
- <observable behavior or failure case to verify>

## Blocked by
<linked blockers, or none>

## Evidence and scope
<relevant paths, examples, constraints and existing regression cases>
```

Completion means the requested breakdown is recorded at its intended destination with accurate scope and dependencies. Implementation is a separate task unless the user also requested it.
