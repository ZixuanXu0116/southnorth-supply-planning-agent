# Failure card: app re-test replay 2026-11-01 / C-US10-40000410-SH-20261129 (engine levers dated from the as-of date)

- **Status:** adopted 2026-11-02 (case server; no prompt change)
- **Batch, arm, repetition:** no batch: the owner's re-test in the Databricks App (2026-11-01), replayed by the assistant on a local copy of the case's events 1–12 (planner task `evaluate`, four runs on the old code)
- **Prompt version and digest:** `planner/v6`, `68437baa4e24a425` (evaluate)
- **Model and effort:** the configured main model (`SNM_MODEL_MAIN`), effort from configuration
- **Trace:** replay run `20261101T232608-fc4ef0` (the assistant's session); the App's run `20261101T231355-a4f1cf` (trace lost with the App's `/tmp`)
- **Suite and category:** none; the regression checks are `test_engine_levers_are_dated_from_today` and `test_mandate_default_judges_the_lever_on_todays_arrival` in `case_server/tests/test_case_service.py`

## Expected

The evaluate task recommends one of the supplier's three proposals (or, with a reason, an engine lever that can still happen), the same every run.

## Observed

Four replays on the old code recommended the engine's `new_order` three times and P-2 once; the App's run recommended P-3. The engine's `new_order` reference row said "covers 2026-10-12, risk 0.51 %, lowest expected total cost", but the case was evaluated on 2026-11-01: that arrival was already past.

## Decisive part of the trace

- Turn 1: `rank_options` returns the three proposals ranked and the reference rows; `new_order` covers 2026-10-12 (the snapshot's as-of date 2026-09-21 plus the 21-day lead time).
- Turn 2 (summarized thinking): "the engine option ... comes in cheapest at 140.3, covers 2026-10-12, and meets the service target"; it recommends it, with a caveat to confirm the date with the supplier, whose own standard slot is 2026-11-19.

## Root cause

`tool error`. The case server dated engine levers as if ordered on the snapshot's as-of date, while the supplier's dates and the offers' validity follow today. The demo makes the gap large (a snapshot of 2026-09-21 used on 2026-11-01), but production has it too: an unchanged case keeps its snapshot, so its as-of date ages while the case waits.

## Fix hypothesis

Tool layer (case server), deterministic:

1. Each engine lever that is still an action to take (new order, expedited new order, transfer, expedite) is dated from today: its arrival moves by the days from the snapshot's as-of date to today, and an expedite never moves past its open order's own date. The evaluation carries a note with both dates; the evaluation cache key includes the day.
2. The mandate default judges whether the engine's lever arrives in time on today's date.
3. A frozen synthetic world (an eval batch, the local flow simulation) pins the case server's today to its as-of date (`SNM_CASE_TODAY`, set by `snm-evals` and `snm-sim`, passed on by the harness), so eval results do not depend on the day they run; the deployed App uses the wall clock.

## Affected categories

The planner suites run with today pinned to the planning run's as-of date, so their levers do not move (a delay of 0 days).

## Owner review

- Hypothesis review (2026-11-01): the owner asked for the fix ("把这三个问题连同 trace 存进 volume 一起做"). The assistant changed the proposed form (a pinned day for frozen worlds) after finding that a wall-clock shift alone would move every lever in the eval worlds.
- Diff review: no prompt diff.

## Measurement

Replays of events 1–12, three runs on each side in parallel (2026-11-02): old code `new_order` 2, P-2 1 (with the first replay: `new_order` 3 in 4); new code P-2 3 in 3, the server's first-ranked proposal. The reference `new_order` now covers 2026-11-23 (risk 12.03 %). Cost 0.083–0.088 USD per run against 0.098–0.103 before, 14–16 s against 22–25 s: the rationale no longer argues about the reference row.

Open for the owner: the App's run recommended P-3, the only proposal that meets the service target, and the owner chose it. With the fix the planner follows the expected-cost ranking (record 007) and recommends P-2, which misses the target (11.45 %). Whether the service target is a hard filter before the ranking is a policy decision.

## Decision

Adopted 2026-11-02 (the owner's request). Regression tests added (see Suite).
