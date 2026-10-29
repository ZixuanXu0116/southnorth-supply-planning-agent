# Failure card: bank-v8-g080-dev-sonnet / c184

- **Status:** fix proposed (case spec corrected in the PR that adds this card; the owner reviews the case)
- **Batch, arm, repetition:** `bank-v8-g080-dev-sonnet`, `main`, rep 1
- **Prompt version and digest:** `main/v4`, `36b457a0d591e623`
- **Model and effort:** `claude-sonnet-5-5`, effort `medium`
- **Trace:** `runs/evals/bank-v8-g080-dev-sonnet/runs/c184-main-r1/trace.jsonl`
- **Suite and category:** `bank_v8`, `adhoc`, traps: `metric_definition`

## Expected

`answer` with the number of `agent_eligible` cases in the latest engine run (16) and the suppliers involved.

## Observed

`answer`: 16 cases, four suppliers (Corvid Gas Systems, Songshan Metal Parts, Kestrel Vacuum Components, Blue Fen Electronics). The scorer reported `Falkenrath` and `Straits Machining` missing.

## Decisive part of the trace

`run_sql` finds the latest `as_of_date` of `curated.replenishment_case` (2026-09-21); `query_metric` on `mv_replenishment_case` returns `agent_eligible_cases` (16 of 327 cases), then `cases` by `vendor`, `vendor_name`, `supplier_class`, and `condition` with `triage_class = 'agent_eligible'`; the answer lists the four vendors with their shortage and warning cases.

## Root cause

`reference`. The agent is right: an independent query of `curated.replenishment_case` joined to `lfa1` returns exactly those four vendors (6, 5, 3, 2 cases). The reference answer was refreshed on generator 0.8 (16 cases, 4 suppliers), but `must_mention` still named the five suppliers of the generator 0.7 run, because `snm-evals refresh` recomputes `reference_sql` and never the names in `must_mention`.

## Fix hypothesis

Case spec: `must_mention` names the four current suppliers, and the notes say that the names are bound to the snapshot and the engine run and must be checked after every refresh. A structural fix (deriving the names from the reference result) is a scorer change and is left as a backlog item.

## Affected categories

None to rerun: re-scoring the same batch passes the case.

## Owner review

- Case review (date): pending (in the PR)

## Measurement

`bank-v8-g080-dev-sonnet` re-scored after the refresh: c184 passes.

## Decision

Pending the owner's case review.
