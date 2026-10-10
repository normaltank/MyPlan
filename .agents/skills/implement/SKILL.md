---
name: implement
description: "Implement the user’s specified change using the repository’s existing checks and delivery workflow."
disable-model-invocation: true
---

# Implement a scoped change

Read the current user request and its linked issue or specification. Use AGENTS.md for project workflow and CONTEXT.md for business contracts; a separate spec or ticket is optional when the conversation already defines the work.

Inspect the current branch and other contributors' changes before editing. Reuse an appropriate isolated checkout or create one when needed; preserve work outside this task.

For behavioral fixes, choose an existing meaningful test boundary and reproduce the issue where possible. Use the tdd skill when test-first work fits the change. Run relevant checks during implementation and the repository's required checks before delivery; discover commands from actual configuration rather than assuming a typechecker exists.

Review the final diff against the request and the fixed baseline. The code-review skill supplies a two-axis review when useful. Commit only this task's changes and continue the authorized PR workflow; follow the actual branch protection and CI requirements.
