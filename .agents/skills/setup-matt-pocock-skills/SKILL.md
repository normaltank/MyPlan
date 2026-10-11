---
name: setup-matt-pocock-skills
description: "Reconfigure engineering-skill routing when the user explicitly requests a change to this project’s setup."
disable-model-invocation: true
---

# Configure engineering skills

MyPlan already has a configured entry in AGENTS.md, a forwarding CLAUDE.md, and task/domain routing under docs/agents/. Routine maintenance does not require setup again.

When the user requests a configuration change:

1. Read the existing routing and the requested change. Inspect actual repository tools, tracker and labels; a seed template does not prove a service, label or workflow is configured.
2. Reuse the user's current choices. Ask only for a missing preference that materially affects this change. Prepare the concrete update before asking for any required approval.
3. Update the file that owns that information. Preserve AGENTS.md as the shared entry and CLAUDE.md as its link. Preserve the established goals/terms/contracts role of CONTEXT.md. Update only requested fields in docs/agents/; do not replace customized files with stock templates.
4. Verify links, task routing and any actually changed integration. Report the change and remaining unsupported capabilities.

The sibling tracker, label and domain files are optional examples for a new configuration, not current MyPlan instructions. Do not create a second local issue store alongside existing issues, PRs or the review agent's plan. Skills in .agents and .claude are project-maintained copies; keep equivalent copies in sync and review upstream changes before adopting them.
