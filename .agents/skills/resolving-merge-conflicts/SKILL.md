---
name: resolving-merge-conflicts
description: "Resolve an existing merge, rebase or stash-application conflict while preserving each contributor’s work."
---

# Resolve merge conflicts

1. Inspect status, the index and Git's operation state. Distinguish a merge, a rebase and conflicted stash application; a stash conflict does not imply a merge commit is pending. Record unrelated staged and unstaged work before editing.
2. Trace both sides to their commits, user requests and tests. Resolve each conflicting hunk to preserve both intended behaviors where compatible. If an unresolved product decision prevents that, explain the concrete conflict and continue independent work.
3. Edit only the affected files and check the resulting diff against both sides. Preserve unrelated modifications and files owned by other agents.
4. Run the relevant existing checks. Stage only the resolved paths or owned hunks after checking what each contains; do not sweep unrelated staged work into a commit.
5. Continue or finish the actual Git operation only within the user's requested scope. For a stash application, marking conflicts resolved may be all that is needed. If the user asks to abort or change direction, preserve recoverable work and follow that instruction. Report the resulting operation state and remaining changes.
