---
name: ask-matt
description: "Choose a relevant installed engineering skill for the current task."
disable-model-invocation: true
---

# Choose an engineering skill

Start from the user's current task and the project's AGENTS.md. A skill supplies a method for that task; it does not assign a new project, replace existing authorization or make every maintenance fix pass through an interview/spec/ticket pipeline.

| Need | Skill |
| --- | --- |
| Diagnose a concrete failure | diagnosing-bugs |
| Implement an already defined change | implement; tdd when test-first work is useful |
| Review a branch, PR or working diff | code-review |
| Resolve an existing Git conflict | resolving-merge-conflicts |
| Review architecture or compare interfaces | improve-codebase-architecture; codebase-design |
| Clarify unresolved business terms or decisions | domain-modeling; grilling when an interview is requested |
| Research an external fact or API contract | research |
| Test a design question with a small demo | prototype |
| Prepare an explicitly requested spec or task breakdown | to-spec; to-tickets |
| Triage incoming issues or PRs | triage |
| Plan a large, uncertain effort | wayfinder, when planning is the requested deliverable |
| Write agent guidance, a PR or a handoff | writing-for-agents; pr; handoff |

MyPlan's task routing and domain documentation are already configured. setup-matt-pocock-skills is for an explicitly requested configuration change, not a prerequisite to routine work.

Use only skills actually installed and supported by the current host. User-invoked workflows remain optional unless the user selected them. For session transitions, consult [PHASE-BOUNDARIES.md](PHASE-BOUNDARIES.md).
