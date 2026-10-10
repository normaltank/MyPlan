---
name: wayfinder
description: Map a large, uncertain effort into decisions and dependencies, using the current work item and the project's actual tracker capabilities.
disable-model-invocation: true
---

# Wayfinder

Use this planning workflow when the route to the user's goal contains substantial unresolved decisions. Start with the current request, existing decisions, `CONTEXT.md` and `docs/agents/issue-tracker.md`. Continue prior progress; a new map is unnecessary when the next authorized step is already clear.

## Establish the destination

State the intended outcome, known constraints and unresolved decisions. Ask only for missing preferences or requirements that change the route. Planning stays within the user's task; execution proceeds when it is also authorized.

## Record decisions where the work already lives

Use the existing issue or work item as the index. Add child issues only when separate ownership or durable discussion warrants them. Link with descriptive titles and keep each decision's detail in one place.

Use actual tracker capabilities. Native dependencies are useful when available; otherwise record blocking links in the existing work item. Use only configured labels and assignment conventions. Missing `wayfinder:*` labels, a "Wayfinding operations" section or a claim mechanism do not require setup, a parallel local tracker, or guessed assignments. Report any coordination limitation that matters to the task.

An index may contain:

- Destination and constraints.
- Decisions already made, linked to their evidence.
- Specific open questions and their blockers.
- In-scope uncertainties that cannot yet be stated precisely.
- Work outside the current scope.

## Resolve the next decision

Read the relevant evidence and related work before choosing an unblocked question. Use research for missing facts, a small prototype for uncertain behavior, or questions for business choices. Consult other skills only when they fit that step. Parallel investigation is useful only for independent questions and within the host's delegation rules.

Record the answer, supporting evidence, remaining uncertainty and what it unblocks. Preserve another agent's claims and edits. Close, reassign or change external work items only within the user's requested workflow; historical rejection is context, not a prohibition against the user's new request.

Continue the authorized planning work while useful decisions remain. A fixed one-ticket limit or token count is not a completion condition. If continuation needs another session, leave a concise pointer to the existing work item and next decision.

The planning outcome is reached when the important decisions and dependencies are clear enough for the next authorized work. Explain unresolved external blockers precisely instead of treating a filled template as completion.
