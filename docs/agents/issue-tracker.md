# Issue tracker: Local Markdown

Issues and specs for this repo live as Markdown files in `.scratch/`.

## Conventions

- One feature per directory: `.scratch/<feature-slug>/`
- The spec is `.scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`
- Triage state is recorded as a `Status:` line near the top of each issue file
- Comments and conversation history append under a `## Comments` heading

## Publishing and fetching

When a skill says to publish to the issue tracker, create the corresponding file under `.scratch/<feature-slug>/`. When a skill says to fetch a ticket, read the referenced Markdown file.

## Wayfinding operations

- Map: `.scratch/<effort>/map.md`
- Child ticket: `.scratch/<effort>/issues/NN-<slug>.md`
- Blocking: a `Blocked by: NN, NN` line
- Frontier: open, unblocked, unclaimed child files in numeric order
- Claim: set `Status: claimed`
- Resolve: append `## Answer`, set `Status: resolved`, and add a context pointer to the map
