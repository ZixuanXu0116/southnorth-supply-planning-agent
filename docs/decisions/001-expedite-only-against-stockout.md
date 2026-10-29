# 001. Expedite only against a projected stock-out

- **Status:** accepted
- **Date proposed:** 2026-10-13
- **Date decided:** 2026-10-14
- **Decided by:** the owner
- **Scope:** engine (lever selection, `PROPOSAL.md` §4.2)

This record was written after the decision, as the worked example of the format; later records are written before implementation.

## Context

The first hindsight oracle run (`evals/reports/engine-oracle-small.md`, 2026-10-13, engine 0.2.0) priced every option of 6,561 case instances from 12 past Mondays on the demand that actually followed. The engine's rule at the time recommended, for every case, the first lever in the fixed order (transfer → expedite → new order → expedited new order) that covered the gap by the trigger date.

- Regret per case against the oracle: 7,640 USD overall; 2,162 on shortage cases (projected below zero); 11,900 on warning cases (below safety stock, never below zero).
- Warning cases were 3,691 of the 6,561 instances. For them an expedited new order was usually the only lever that covered by the trigger date, so the engine paid a surcharge (10–25 % of the line value plus freight) to restore safety stock that, on the realized demand, was never needed: the oracle chose "do nothing" on 96 % of warnings.
- Doing nothing on every case cost 1,619 regret per case; always expediting 12,402.

## Options

1. **Change nothing.** Regret stays at 7,640 per case; the planner agent keeps flagging whole-order expedites for small gaps (prompt v3 notes).
2. **Expedite only against a projected stock-out.** A warning gets the first standard lever (transfer, else a new order at the planned delivery time) even when it arrives after the warning date; the expedite levers stay listed as alternatives with their costs. Expected regret on warnings 84 per case (the 75 USD order cost where doing nothing would have done), shortage unchanged.
3. **Do nothing on warnings.** Regret on warnings 204 per case: cheaper than today, dearer than option 2, because the few warnings that did go below zero cost more in line-down days than the order cost on the rest; also leaves the planner with nothing to prepare for a warning.
4. **Cheapest lever that covers by the trigger date.** Barely differs from option 1 (7,624), because there is rarely more than one covering lever.

## Owner questions

1. Is a surcharge ever justified to restore safety stock alone, or only to prevent a projected stock-out?
2. For a warning with no transfer available, should the engine recommend a standard order or nothing?

Answers (owner, 2026-10-14):

1. Only to prevent a projected stock-out.
2. A standard order (option 2 was approved as proposed).

## Decision

Levers with a surcharge (expedite an open order, expedited new order) are recommended only for shortage cases. A warning case gets a transfer when a network location has surplus, otherwise a new order at the planned delivery time, with `days_short` counting the days the stock stays below safety stock and no line-down cost charged. Engine version 0.3.0.

## Acceptance

- Engine tests: a warning never takes an expedite even when only an expedite is in time; a warning takes a transfer or a standard order that is in time (`data/tests/test_planning.py`).
- `snm-data oracle` rerun: regret per case below 1,100 overall, warning regret below 100, shortage unchanged at 2,162.
- `snm-data replay` rerun: unchanged (the rule does not change which cases open).
- `snm-evals planner` on the new run: no drop from 15/15 (now 13 sampled cases, one expedite case left in the run).
- Contract comments, metric view, `kb/metrics/projected_stock.md`, `PROPOSAL.md` §4.2 and §14, `DATA_MODEL.md` describe the rule.

## Consequences

- Warning levers on the `small` profile moved from expedited new orders almost throughout to 290 standard orders and 3 transfers; 11 % of warnings are covered by the warning date (median 30 days below safety stock).
- `covers_in_time` is now false for most warnings by design; `mv_replenishment_case.cases_not_in_time` says so in its comment.
- The remaining warning regret is the order cost itself: separating warnings that need an order from those that do not needs a demand-variability term in the projection (candidate record 002), not a cheaper lever.
- Shortage regret (2,162 per case, 19 % agreement with the oracle) is untouched by this decision.
- The lakehouse case store received 258 `case_updated` events for the warnings whose lever changed.

## Evidence

- `evals/reports/engine-oracle-small.md` (first run 2026-10-13, rerun 2026-10-14 on 0.3.0: regret 993 overall, 84 on warnings).
- `evals/reports/engine-replay-small.md` (0.3.0 row).
- `evals/reports/planner-prepare-v3-engine030.md`, `-rep2.md` (13/13 twice).
- Commits `9b44e4d` (engine 0.3.0), `33791cf` (reruns), `0da986a`, `7addfa2`, `e0cda62` (status and sync).
