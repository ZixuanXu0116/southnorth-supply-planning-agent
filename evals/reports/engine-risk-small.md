# Engine 1.1.0: stock-out risk per option (record 002, phase 3 step 2)

Profile `small` (generator 0.8), plant `US10`, deterministic, no model calls. Engine 1.1.0 adds a demand model per material (Croston-SBA on the 26 weeks of staging before the as-of date, `snm_data.planning.demand`) and prices stock-out risk with it: on the case (`stockout_risk_no_action`) and, in the case server, for every option on the same 2,000 simulated demand paths (stock-out risk up to the later of the trigger date and the option's last arrival; expected line-down over the horizon, if nothing else is ordered). Every risk is the model's share of paths mapped through its segment's calibration curve (`forecast-accuracy-small.md`, phase 3) and shown with two decimals; the service targets are A 1 %, B 3 %, C 10 % (record 002 as amended on 2026-10-19). Figures below are after the amendment.

## Which forecast opens cases: replay

The same 16 Mondays as `engine-replay-small.md` (2026-05-04 to 2026-08-17), 30-day horizon, scored against the realized emergency purchase orders (`snm-data replay`, `--params` with `forecast_model: sba` for the second row).

| Point forecast | Realized emergencies | Shortage cases in window | Caught in window (recall) | Caught too late | Flagged as shortage or warning | Median trigger minus emergency (days) |
|---|---|---|---|---|---|---|
| Trailing 28-day rate (engines 1.0 and 1.1) | 6 | 41 | 2 (33 %) | 2 | 2 (33 %) | +13 |
| SBA average (26 weeks) | 6 | 15 | 0 (0 %) | 3 | 1 (17 %) | +31 |

The model's average is the better forecast of demand over a lead time (phase 2: WAPE 68 % against 85 %, no window without a forecast), but it smooths away the recent bursts that precede emergencies, so the cases open later and the replay catches none. Engine 1.1.0 keeps the trailing rate for the projection that opens cases (`forecast_model: trailing`, the default; the case set on 2026-09-21 is identical to engine 1.0.1's, 327 cases) and uses the model only to price risk.

## The chance of running out if nothing is done (2026-09-21)

All 199 order cases have demand in at least one of the last 13 weeks, so every one is priced (the unqualified segment, no outflow in 13 weeks, has no case on this day).

| Condition | Cases | 10th percentile | Median | 90th percentile |
|---|---|---|---|---|
| shortage (pooled stock below zero by the trigger date on the average forecast) | 116 | 9.56% | 52.06% | 100.00% |
| warning (below the minimum only) | 83 | 14.88% | 38.51% | 86.96% |

For a shortage case the average line reaches zero on the trigger date, so about half of the simulated paths run out by then: **the trigger date is roughly the median run-out date, not a safe deadline**. The spread (10 % to 100 %) is the information the average line lacks: firm reservations that already exceed stock give 100 %, a distant trigger with rare demand gives a few percent.

## Example: the role-play material with three scripted proposals

`C-US10-40001795-SH-20261122` (REGULATOR, GAS, 2SLM, class C, trigger 2026-11-22, gap 25; demand in 3 of the last 13 weeks in lumps of 4 to 12), with the `cheaper_late_split` answers of the planner-evaluate suite under the default mandate (arrive by the trigger date, no surcharge, no freight). Cost in USD; line-down 200 USD per day below zero.

| Option | Arrives | Mandate | Stock-out risk | Option cost | Expected line-down | Expected total |
|---|---|---|---|---|---|---|
| P-3, new order (rank 1, recommended) | 2026-11-17 | inside | 51.36% | 220.85 | 1,786.60 | 2,007.45 |
| P-2, split (rank 2) | 12 on 11-17, 13 on 11-29 | misses arrival | 52.56% | −597.67 | 2,247.40 | 1,649.73 |
| P-1, new order (rank 3) | 2026-11-07 | misses surcharge, freight | 20.93% | 1,471.91 | 1,290.70 | 2,762.61 |
| Engine: new order | 2026-11-05 | | 20.78% | 130.98 | 1,227.20 | 1,358.18 |
| Engine: expedited new order | 2026-09-27 | | 9.23% (within the 10% target) | 4,613.92 | 963.50 | 5,577.42 |
| Do nothing | | | 59.01% | 4.58 | 5,879.50 | 5,884.08 |

Reading:

- Arriving five days before the trigger date still leaves about even odds of running out first (51.36%): most of the risk comes before any of these proposals arrives, from one or two early lumps. Arriving three weeks earlier (P-1, the engine's new order) cuts it to about 21 %; only the expedited new order, two months earlier, meets the C-class target of 10 % (9.23%).
- The late split adds 1.20 points of risk and 461 USD of expected line-down for 818 USD saved: on expected cost it is the cheapest proposal, which is the question the owner asked after the role play. The purchasing rule still ranks it second (outside the mandate on arrival); record 002 phase 4 replays a risk-aware rule before any rule uses these figures.
- Expected line-down assumes nothing else is ordered over the 91 days, so its level overstates what the daily re-plan would leave (it reacts to a lump that shows up early); the differences between options, priced on the same paths, are the usable part.

## Planner regression

The planner's case tools (`evaluate_option`, `rank_options`) now return the risk fields; the prompt (`planner/v4`) is unchanged. The planner-evaluate suite on engine 1.1.0 (batch `planner-evaluate-v4-risk`, 12 cases, one repetition, USD 0.72): the proposals in purchasing's order in **12/12** runs (v4's measurement: 36/36), the rule's first recommended in 10/12 (34/36). The two other runs recommended the engine's new order with stated reasons that are true of the figures (it arrives weeks earlier and costs less; in one case it is the only option that meets the service target). The rationales cited the risk as a raw share ("a stockout risk of 0.277"); since the owner's amendment the tools return it as a percentage with two decimals ("27.70%"), which is what the planner then quotes.

## Approximations

- The calibration curve was fitted on lead-time window totals (does demand over the window exceed the model's percentile); the engine applies it to the chance of a day below zero within a window, which with one receipt is close to the same event. Phase 4's replay checks the rule end to end.
- The model draws weeks independently, with sizes resampled from the 26 weeks; a trend or a new product's ramp-up is not modelled.
- The risk is measured against zero on the pooled line (main warehouse plus overflow hub), like the projection.

## Reproduce

```
uv run snm-data replay                                  # trailing rate (default)
uv run snm-data replay --params sba.yaml                # sba.yaml: "forecast_model: sba"
uv run snm-data plan --out out --from-profile small --as-of 2026-09-21
```

The example table comes from the case server's `rank_options` on a scratch store prepared with `snm_evals.planner_evaluate.prepare_store` (mix `cheaper_late_split`); the case page shows the same figures.
