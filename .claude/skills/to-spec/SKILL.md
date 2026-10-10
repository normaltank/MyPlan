---
name: to-spec
description: "Turn the current conversation into a spec and publish it to the project issue tracker: no interview, just synthesis of what you've already discussed."
disable-model-invocation: true
---

This skill takes the current conversation context and codebase understanding and produces a spec. Do NOT interview the user; just synthesize what you already know.

Read the current task routing in docs/agents/issue-tracker.md and use the existing work item when suitable. Discover the actual tracker and labels; setup is not a prerequisite and a separate spec file is not mandatory for routine maintenance.

## Process

1. Explore the repo to understand the current state of the codebase, if you haven't already. Use the project's domain glossary vocabulary throughout the spec, and respect any ADRs in the area you're touching.

2. Identify the contracts to verify and the existing test boundaries that reach their failures. Prefer caller paths and add focused internal or persistence checks when they cover a distinct risk.

Use existing meaningful test boundaries within the agreed task. Ask only when an unresolved requirement changes the expected behavior or test scope.

3. Write the spec using the template below, then publish it to the project issue tracker. Apply a status label only when it exists and is mapped by the actual tracker configuration; otherwise describe readiness in the work item. A vocabulary table does not authorize creating labels.

<spec-template>

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A concise set of user stories that adds information beyond the problem and solution; omit this section when redundant. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit>

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending
</user-story-example>

Cover the requested behavior and important edge cases without padding the specification.

## Implementation Decisions

A list of implementation decisions that were made. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Use behavioral contracts as the durable specification. Include precise current paths, symbols or snippets when they make the change or evidence verifiable, and record the relevant version when needed.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it within the relevant decision and note briefly that it came from a prototype. Trim to the decision-rich parts, not a working demo, just the important bits.

## Testing Decisions

A list of testing decisions that were made. Include:

- The contracts and failure cases the tests protect, including internal or persistence invariants where relevant
- Which modules will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)

## Out of Scope

A description of the things that are out of scope for this spec.

## Further Notes

Any further notes about the feature.

</spec-template>
