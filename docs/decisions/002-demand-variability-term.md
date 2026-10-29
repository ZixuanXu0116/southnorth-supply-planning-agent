# 002. Make the demand forecast trustworthy by segment, then price options by stock-out risk

- **Status:** accepted; phase 4 decided by [007](007-expected-cost-ranking.md) on 2026-10-20 (rewritten 2026-10-19 after the owner's direction; the first draft of 2026-10-16, measured on the hub projection that record 003 replaced, is in the git history and in `evals/reports/engine-demand-shape.md`)
- **Date proposed:** 2026-10-16, rewritten 2026-10-19
- **Date decided:** 2026-10-19
- **Decided by:** the owner
- **Scope:** engine (demand forecast, option pricing, the mandate's arrival check; `PROPOSAL.md` §4.2), evaluation (backtest, replay)

## Context

**The owner's goal** (role play of 2026-10-19, `STATUS.md`): stay conservative today, and become able to tell, material by material, when a cheaper strategy does not put production at risk, or risks less than it saves. The trigger was Blue Fen's split offer for `C-US10-40001795-SH-20261122`: 425.64 USD cheaper than the in-mandate offer, no projected day below zero, but its second part after the trigger date and 18 days below safety stock (against 8). The mandate's arrival check (the minimum quantity by the latest arrival date) marked it outside; the owner chose the in-mandate offer because the forecast's later days are not reliable enough to bet on.

**How the forecast works today** (engine 1.0.1): firm demand from the released production orders' reservations (visible 3 to 10 days ahead, `inputs._production_demand`), the planned orders' dependent requirements for weeks 2 to 13 at the extraction (`_planned_demand`), and the trailing 28-day rate of staging to production, netted per week against the explicit requirements (`engine._forecast`). Every decision rule reads a point forecast: days below zero, the levers' coverage, the mandate's arrival check.

**Phase 1 evidence: how accurate is it?** `snm-data forecast-backtest` rebuilds the forecast on 52 past Mondays (2025-08-04 to 2026-07-27) as that day's extraction would have shown it and compares it with the staging that followed, for the 338 Round Rock materials (`evals/reports/forecast-accuracy-small.md`). Over the planned delivery time (the window an order decision protects, median 30 days):

| Segment (weeks with outflow in the last 13) | Share of windows | Bias | Actual above forecast | Demand with no forecast | 90 % band (P90 of actual / forecast) | Band holds on held-out days | Verdict |
|---|---|---|---|---|---|---|---|
| All materials | 100 % | −3 % | 45 % | 19 % | 2.88 | 76 % | not calibrated |
| Regular: 4–6 weeks | 30 % | +19 % | 33 % | 8 % | 2.72 | 87 % | calibrated, wide |
| Regular: 7–9 weeks | 4 % | +37 % | 29 % | 1 % | 2.39 | 92 % | calibrated, wide |
| Regular: 10–13 weeks | 1 % | +14 % | 43 % | 0 % | 2.27 | 99 % | calibrated, wide |
| Rare: 1–3 weeks | 62 % | −21 % | 50 % | 24 % | 2.90 | 68 % | not calibrated |
| None in 13 weeks | 3 % | −88 % | 90 % | 70 % | 6.00 | 0 % | not calibrated |

- **Within a week the forecast holds**: it is the released orders (actual above forecast in 9 % of 7-day windows). It overstates (bias +49 %) because the backlog is staged only as stock allows.
- **Beyond a week it does not**: WAPE 85 % over the lead time; the error grows with the horizon (the band is 2.15 at 14 days, 3.56 at 56 days).
- **Frequency separates the segments, criticality does not**: materials with regular demand have an error band that is honest (it holds on held-out days) but wide (2.3 to 2.7 times the forecast); materials with rare demand are under-forecast and often missed entirely, because a 28-day window frequently holds no demand. Criticality classes A, B, and C look alike.
- **Today 248 of the 338 materials are rare-demand**, including both role-play materials (the regulator had outflow in 3 of 13 weeks, in lumps of 12, 4, and 12). No segment meets the proposed bar for a probability-based decision (band at most 1.5 times the forecast), so the strict rule is the right default.
- Consistent with the replay: the engine caught 2 of 6 emergencies in time (recall 33 %, `evals/reports/engine-replay-small.md`).
- Approximations (in the report): past days have no planned orders; reservations are due two days after staging; realized outflow is censored by stock-outs; windows of consecutive weeks overlap.

## Options

1. **Change nothing.** The strict arrival rule and the trailing 28-day rate stay. No cost; savings like Blue Fen's split are never captured with confidence; rare-demand materials stay under-forecast (24 % of their lead-time windows had demand and no forecast).
2. **A robust, longer rate.** A 91-day window, or the median of weekly totals, so that a month without demand does not zero the forecast and one large order does not inflate it. Cheap (parameters and one function); expected to cut the "no forecast" share of rare materials; still a point forecast without a probability.
3. **An intermittent-demand model per material.** Order frequency and order size estimated from at least 91 days (Croston/SBA-type, or a compound Poisson that matches how the generator draws demand), giving a distribution of demand over the lead time, not only a mean. Medium cost (a new module, tests with hand-computed quantiles); addresses rare demand directly and yields stock-out probabilities.
4. **A product-level forecast through the bill of materials.** Forecast finished products from their history and explode them into component demand (what MRP's planned orders do). Pooling across products smooths components shared by several products. In this synthetic data the planned orders are drawn around each product's trailing rate, so the gain is a measurement question here; in a company with a real production plan this is usually the best source.
5. **Risk pricing on qualified segments** (the mechanism of phase 3, after 2 to 4). For segments whose forecast the backtest shows calibrated with an acceptable band, every option gets a stock-out probability and an expected line-down cost from the segment's demand distribution, and the mandate's arrival check becomes "the probability of a stock-out in the protection window is at most the target"; elsewhere the strict rule stays. Validated by replaying both rules over history.

Proposed sequence: measure options 2, 3, and 4 as alternative forecasters in the same backtest (phase 2, no engine change), choose a forecaster per segment from the numbers, build option 5 for the segments that qualify (phase 3), and replay it against the strict rule (phase 4) before any material uses it.

## Owner questions

1. **The bar for "usable"**: a segment's forecast may drive a probability-based decision when its 90 % band holds in at least 85 % of held-out windows and is at most 1.5 times the forecast (the proposal at the time of asking). Agree, or judge by cost instead (accept a wider band where a stock-out costs little, as for C-class parts at 200 USD per day)?
2. **The service target** for the probability-based arrival check: a stock-out probability in the protection window of at most 1 % for A-class, 2 % for B-class, 5 % for C-class parts, or one target for all?
3. **History**: may the engine use 91 days or more of history for its demand model? The extraction carries two years.
4. **Phase 2 scope**: measure options 2, 3, and 4 in the backtest before changing the engine; is there a forecaster you want added (for example a rule your planners use)?

Answers (owner, 2026-10-19; the owner took the assistant's recommendation on each):

1. Calibration is the gate, cost is the judge: a segment qualifies when its 90 % band holds in at least 85 % of held-out windows (with at least 30 test windows); no cap on the band's width. A wide band raises the stock-out probability, so the cost comparison turns conservative by itself.
2. Service targets per criticality: a stock-out probability in the protection window of at most 1 % for A-class, 2 % for B-class, and 5 % for C-class parts.
3. Yes: the engine may use 91 days or more of history (the extraction carries two years).
4. Phase 2 measures options 2, 3, and 4 in the backtest before any engine change; no further forecaster requested.

## Decision

Approved by the owner on 2026-10-19 (drafted from the answers above). The strict arrival rule stays the default. The forecast is improved and qualified segment by segment: phase 2 measures a robust 91-day rate, an intermittent-demand model, and a product-level forecast through the bill of materials in the backtest and picks a forecaster per segment from the numbers; a segment qualifies when its forecaster's 90 % band holds in at least 85 % of held-out windows. Phase 3 prices every option of a qualified segment by its stock-out probability and expected line-down cost and turns the mandate's arrival check into a probability check against the service target (A 1 %, B 2 %, C 5 %); phase 4 replays the risk-aware rule against the strict rule, and no material uses it before the replay shows it better.

## Amendment (2026-10-19, the owner)

After phase 3 step 2 (`evals/reports/engine-risk-small.md`), the owner changed the service targets of answer 2: a stock-out probability in the protection window of at most **1 % for A-class** (unchanged), **3 % for B-class**, and **10 % for C-class** parts, and asked that a stock-out risk be shown and cited as a percentage with two decimals (27.70%, not 0.277).

Implementation note (same day): the per-segment multiplier of step 1 holds at the targets but overshoots, because demand comes in whole lumps; applied to the simulated lumps it turned a model risk of 1-3 % into about 13 % where the backtest observed 4-7 %, so a page would have shown 1.25 % for an option that failed a 10 % target. The engine therefore maps the model's probability through a calibration curve per demand-frequency segment instead (the share of backtest windows that ran out at the same model probability, fitted on the first half of the days; on the second half every qualified segment ran out at most as often), and shows and checks that one calibrated number (`evals/reports/forecast-accuracy-small.md`, phase 3). Materials without outflow in 13 weeks stay unqualified.

## Acceptance

Draft, to be fixed with the decision:

- **Phase 2** (backtest, one row per forecaster in `evals/reports/forecast-accuracy-small.md`): for every segment, a forecaster whose 90 % band holds in at least 85 % of held-out windows, the band width reported; for rare demand, the share of windows with demand and no forecast down from 24 % to at most 10 %.
- **Phase 3**: unit tests with hand-computed probabilities on a toy history; the generator's demand parameters recovered within tolerance on the `test` profile.
- **Phase 4** (replay and oracle): on the qualified segments, the risk-aware rule's regret per case below the strict rule's, with no more A- or B-class line-down days; the mandate flags reproduce.
- Contracts, KB (`kb/metrics/projected_stock.md`), `PROPOSAL.md` §4.2, and the bank v8 planning answers updated when the engine changes; the planner's prepare evaluation unchanged.

## Consequences

Phase 1 changes nothing in the engine. Phases 3 and 4 add columns to the planning tables (probabilities, expected line-down), so the contracts, the KB, the bank v8 planning answers, the case store, and the planner's explanations change with them (the planner prompt through the failure-card process). The strict arrival rule stays the default until a segment qualifies; the case page already notes when a proposal outside on arrival keeps the projection above zero, for the person to judge.

## Evidence

- `evals/reports/forecast-accuracy-small.md` (`uv run snm-data forecast-backtest`, module `snm_data.planning.backtest`).
- `evals/reports/engine-replay-small.md` (recall of the current engine).
- `evals/reports/engine-demand-shape.md` (the first draft's evidence, on the hub projection).
- The role play of 2026-10-19 (`STATUS.md`) and `evals/failures/roleplay-20261019-C-US10-40001795-SH-20261122.md`.
