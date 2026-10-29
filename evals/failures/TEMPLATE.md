# Failure card: <batch> / <case_id>

- **Status:** open | fix proposed | measured | adopted | reverted | wontfix (reason)
- **Batch, arm, repetition:** `<batch>`, `<arm>`, rep <n>
- **Prompt version and digest:** `<profile>/v<n>`, `<digest>`
- **Model and effort:** from the run manifest
- **Trace:** `runs/evals/<batch>/<path>`
- **Suite and category:** `<suite>`, `<category>`, traps: `<trap_tags>`

## Expected

One line: the behavior and the values the case expects.

## Observed

One line: what the run did and what the scorer reported missing.

## Decisive part of the trace

The tool calls and the turn where it went wrong, as a short excerpt (tool name, arguments in brief, the result's shape, the assistant text that followed). Not the whole transcript.

## Root cause

Exactly one class: `retrieval` | `schema linking` | `interpretation` | `policy` | `tool error` | `faithfulness` | `prompt instruction` | `reference` | `infrastructure`

Why this class and not the neighbors.

## Fix hypothesis

What to change, in which layer (KB page | tool | engine | case spec | prompt), and why that layer. If the layer is the prompt: the exact lines to add, remove, or change in `v<n+1>`.

## Affected categories

Which categories of the `dev` split to rerun, paired against the previous version.

## Owner review

- Hypothesis review (date): confirmed | changed to … | sent to layer …
- Diff review (date): approved | amended | rejected, with the reason

## Measurement

Batch names, the paired numbers (passes before and after on the same cases, discordant pairs, cost per question), regressions in other categories.

## Decision

Adopted | reverted | iterate, by the owner, with the date. Regression case added: `<suite>/<case_id>`.
