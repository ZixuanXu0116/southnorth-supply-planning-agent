# Failure card: bank-v8-g080-dev-sonnet / c176

- **Status:** fix proposed (case spec corrected in the PR that adds this card; the owner reviews the case)
- **Batch, arm, repetition:** `bank-v8-g080-dev-sonnet`, `main`, rep 1
- **Prompt version and digest:** `main/v4`, `36b457a0d591e623`
- **Model and effort:** `claude-sonnet-5-5`, effort `medium`
- **Trace:** `runs/evals/bank-v8-g080-dev-sonnet/runs/c176-main-r1/trace.jsonl`
- **Suite and category:** `bank_v8`, `unfamiliar`, traps: `stock_types`, `data_freshness`

## Expected

`answer` with the component stock in the Longgang production supply area `CN10/2100` at the end of 2026-09-20 and the number of materials. The reference read `MARD.LABST`: 17,566 units on 382 materials.

## Observed

`answer`: 17,250 units on 335 materials, from `raw_ext.inventory_snapshot_daily` for `snapshot_date = '2026-09-20'`. The scorer reported both values missing.

## Decisive part of the trace

Two `run_sql` calls on `raw_ext.inventory_snapshot_daily` (`werks = 'CN10'`, `lgort = '2100'`, `snapshot_date = '2026-09-20'`), results `r0003` and `r0004`: 335 materials, 17,250 units, all unrestricted.

## Root cause

`reference`. The agent is right. The ECC extraction runs at 00:00 America/Chicago, which is 13:00 in Longgang, so on generator 0.8 `MARD` already contains the morning of 2026-09-21: `MSEG` at `CN10/2100` with posting date 2026-09-21 has 315 `311` staging items (+7,028) and 230 `261` consumption items (−6,712); 17,250 + 7,028 − 6,712 = 17,566. The case note "MARD.LABST at the nightly extraction is the stock at the end of 2026-09-20" held on 0.7.0 but not on 0.8, whose shuttles stage to `2100` during the local morning.

## Fix hypothesis

Case spec: the reference SQL reads the daily snapshot of 2026-09-20 (`raw_ext.inventory_snapshot_daily`), and the notes explain why `MARD` is not the end-of-day stock at Longgang. Refreshed answer: 17,250 units on 335 materials. The `data_freshness` trap now lies in the other direction (an agent reading `MARD` overstates the stock).

## Affected categories

None to rerun: re-scoring the same batch passes the case.

## Owner review

- Case review (date): pending (in the PR)

## Measurement

`bank-v8-g080-dev-sonnet` re-scored after the refresh: c176 passes.

## Decision

Pending the owner's case review.
