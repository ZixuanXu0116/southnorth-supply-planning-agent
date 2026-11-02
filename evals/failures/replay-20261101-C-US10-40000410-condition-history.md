# Failure card: app re-test replay 2026-11-01 / C-US10-40000410-SH-20261129 (condition history)

- **Status:** adopted 2026-11-02 (case server; no prompt change)
- **Batch, arm, repetition:** no batch: the owner's re-test in the Databricks App (2026-11-01), replayed by the assistant on a local copy of the case's events 1–4 (planner task `prepare`, four runs on the old code)
- **Prompt version and digest:** `planner/v6`, `cc950b6f6d27004f` (prepare)
- **Model and effort:** the configured main model (`SNM_MODEL_MAIN`), effort from configuration
- **Trace:** replay run `20261101T232747-0fbb7a` (the assistant's session; runs are not committed); the App's run `20261101T230716-c32834` (trace under the App's `/tmp`, lost)
- **Suite and category:** none; the regression check is `case_server/tests/test_case_service.py::test_get_case_shows_a_condition_raised_again_after_clearing`

## Expected

On a case the engine flags (state `new`, condition shortage, gap 5), `prepare` drafts the supplier request, every run.

## Observed

The App's run drafted the request; the first replay wrote a note instead ("No supplier request drafted. The case thread has an engine event condition_cleared (seq 2) ... the shortage may no longer exist"). On the old code: 2 notes in 4 replays.

## Decisive part of the trace

- Turn 1: `get_case` returns the engine block (condition shortage) and an `events` list with one entry, seq 2 `condition_cleared`; the two later `case_updated` events (seq 3, seq 4) that flagged the case again are filtered out.
- Turn 2 (summarized thinking): "If the condition really has cleared, the case may not need a drafted action." Two `kb_search` calls find nothing on the event; turn 5 calls `add_note`.

## Root cause

`tool error`. The tool output was misleading: it showed a clearing without the update that reversed it, and it had no field saying whether the case is flagged now. The model's reading of what it was shown was reasonable, so not `interpretation`.

## Fix hypothesis

Tool layer (case server), deterministic:

1. `get_case` returns `cleared` (whether the latest engine run no longer flags the case, as `list_cases` already did) and lists the engine's snapshot events in brief (as-of date, condition, trigger date, gap, lever, engine version) instead of hiding them; the tool description says what `cleared` means.
2. Engine sync: a cleared case that the engine flags again is updated even when its figures did not change. Before, it stayed marked cleared.

## Affected categories

The planner prepare suite reads `get_case`; its cases have no clearing in their history, so the change adds a field and nothing else there.

## Owner review

- Hypothesis review (2026-11-01): the owner asked for the fix in the case server ("把这三个问题连同 trace 存进 volume 一起做").
- Diff review: no prompt diff.

## Measurement

Replays of the case's events 1–4, three runs on each side in parallel (2026-11-02): old code 2 drafts, 1 note (with the first replay: 2 drafts, 2 notes in 4); new code 3 drafts in 3, 3 turns each, 0.021–0.051 USD per run.

## Decision

Adopted 2026-11-02 (the owner's request). Regression test added: `test_get_case_shows_a_condition_raised_again_after_clearing`.
