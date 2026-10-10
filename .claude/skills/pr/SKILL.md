---
name: pr
description: "Write a pull request description explaining the concrete change and its verification."
---

# Write a PR description

Lead with the problem and resulting behavior. Use a concrete before/after example when it helps. Describe the final diff for a reviewer who has not read the conversation.

Include the checks actually run, their results and any material validation limits. Cite the relevant CI run or preserved evidence instead of copying execution logs. Explain compatibility, migration or operational risks only where the change introduces them.

Scale the format to the change: a small fix may need two paragraphs; a broad change may benefit from a short list or diagram. Follow an existing repository template when one applies. A diagram, screenshot, coverage target or risk section is not a universal requirement.

Use the user's language and the project's domain terms. Keep evolving work status in the PR and CI rather than appending a second progress log to project entry documents.
