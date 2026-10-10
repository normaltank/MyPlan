---
name: python-pro
description: "Resolve a specific Python runtime, typing, resource-management or library-compatibility question."
---

# Python implementation reference

Start with the repository's declared runtime, locked dependencies and current callers. MyPlan's commands and dependency conventions are in README.md and pyproject.toml. Preserve those choices unless the task calls for a change.

For the specific question, check ownership and lifetime of resources, exception behavior, event-loop boundaries and compatibility with the installed library version. Use official version-appropriate documentation when the code and local package do not resolve an API question.

Prefer a small change at the existing interface and validate the affected behavior. Use async-python-patterns for a concurrency problem or python-performance-optimization for a measured bottleneck; ordinary Python work does not require loading every reference.

Do not introduce a framework, deployment stack, typechecker, model selection or coverage target from this skill. Tooling and validation requirements come from the actual project and user task.
