# Engine replay on the `small` profile

## Engine 1.0 on generator 0.8 (2026-10-17): the business model of record 003 in the data

Generator 0.8 produces the flows of record 003: the deliveries are the picking lists of production orders issued from Round Rock's main warehouse `1000` to the production supply area, the overflow hub `2001` holds what the main warehouse does not need right away (30 % of supplier deliveries, the stock above the maximum), the main warehouse pulls from it below its minimum, and purchasing orders like MRP (the reservations due within the lead time count). Emergency purchase orders (`ZEX`) are now rare: 58 at Round Rock over the year (0.6 % of the plant's orders, from 9 % with a naive reorder point), placed when a reservation inside the lead time exceeds what both locations hold. Same 16 Mondays (2026-05-04 to 2026-08-17), 30-day horizon, plant `US10`.

| Engine | Realized emergencies (US10) | Shortage cases in window | Caught in window (recall) | Caught too late | Flagged as shortage or warning | Precision | Median trigger minus emergency (days) |
|---|---|---|---|---|---|---|---|
| 1.0.0 on generator 0.8 | 6 | 41 | 2 (33%) | 2 | 2 (33%) | 5% | +13 |

Reading:

- Six emergencies in sixteen weeks is what MRP-like purchasing leaves for the planner: a reservation that appears inside the supplier's lead time and exceeds the stock of both locations. The engine caught two in time, two too late (its trigger date a median of 13 days after the emergency), and missed two. The misses and the late catches are the demand the engine does not see early enough: the production order is created 3 to 10 days before its start, so its reservation enters the projection only then, and the trailing-rate forecast does not foresee a single large picking-list line.
- Precision stays low by construction (a shortage case means "action needed if nothing is done", and the simulation's purchasing acts on most of them in time); the 41 shortage cases of the windows are the cases the planner would have prepared.
- Record 002 (a demand-variability term, or a stock-out probability from the intermittent-demand model) is the next lever on recall; its evidence is to be recomputed on this projection.

## Engine 1.0 on generator 0.7 (2026-10-17, superseded by the row above)

| Engine | Realized emergencies (US10) | Shortage cases in window | Caught in window (recall) | Caught too late | Flagged as shortage or warning |
|---|---|---|---|---|---|
| 1.0.0 (record 003) on generator 0.7 | 17 | 12 | 0 (0%) | 0 | 0 (0%) |

Not comparable with the other rows: the emergency purchase orders in generator 0.7 were placed by the v1 simulation when the warehouse would have run out serving external customers, which the business model of record 003 does not have; the engine saw only manufacturing demand, so the 17 Round Rock emergencies in the replay windows were explained by flows it was told to ignore.

## Engines 0.1.0 to 0.3.0 on generator 0.7: the previous model (hub serving customers and manufacturing, both plants)


The engine was run on 16 past Mondays (2026-05-04 to 2026-08-17) with its inputs rebuilt as the nightly extraction of each day would have shown them (`snm_data.planning.history.tables_as_of`), and its shortage cases were scored against what the simulation did next: the emergency purchase orders (`ZEX`) the plants had to place within 30 days because the hub would otherwise have run out. Deterministic, no model calls; about one minute per variant.

| Engine | Realized emergencies | Shortage cases in window | Caught in window (recall) | Caught too late | Flagged as shortage or warning | Precision | Median trigger minus emergency (days) | Median lead (days) |
|---|---|---|---|---|---|---|---|---|
| 0.1.0 (customer demand only) | 94 | 323 | 23 (24%) | 25 | 33 (35%) | 7% | +21.5 | 15 |
| 0.2.0 (plus network replenishment) | 94 | 461 | 28 (30%) | 34 | 38 (40%) | 6% | +24 | 14 |
| 0.2.0 with the staging-rate forecast | 94 | 492 | 28 (30%) | 34 | 38 (40%) | 6% | +21.5 | 14 |
| 0.3.0 (expedite only against a stock-out; staging-rate forecast) | 94 | 492 | 28 (30%) | 34 | 38 (40%) | 6% | +21.5 | 14 |

Engine 0.3.0 changes the recommended lever of warning cases, not which cases open or when, so its replay row repeats 0.2.0's (rerun 2026-10-14 to confirm).

Reading:

- Engine 0.1.0 counted only customer goods issues as hub demand. The hub also replenishes its spoke and satellites (at Round Rock the satellites take more than the customers: 2,073 against 1,535 units in four weeks; at Longgang the Dongguan transfers are about 45 % of customer demand), so the projection ran too long. 0.2.0 adds the hub-to-network transfers (not the production staging, which the reservations count): recall 24.5 % → 29.8 %, flagged 35 % → 40 %.
- Most remaining misses are lumpy demand: a large customer order created after the as-of day and shipped within days, which no average-rate projection foresees (the projection of one such material: 21 on hand, 0.9 per day, emergency four days later). The engine's shortage dates are a median of about three weeks after the realized emergency. Safety stock and a demand-variability term are the remedy, not a higher average.
- Precision is low by construction: a shortage case means "action needed if nothing is done", and the simulation's own reorder policy acts on most of them in time. Precision against emergencies is therefore not a target; recall (missed shortages) and lead time are.
- The staging-rate forecast (used for past days, which have no planned orders) changes little: production staging does not trigger emergency buys in the simulation.

Approximations: reorder points are today's values (A3 changed the Dongguan ones in June); sales-order rejections are today's; planned orders exist only at the extraction. Next: score warnings separately, add a demand-variability term, and replay closed loop with the oracle (Phase B).
