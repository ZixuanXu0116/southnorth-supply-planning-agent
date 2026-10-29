# Hindsight oracle on the `small` profile

> **Correction (2026-10-19).** Until record 002 phase 4 the oracle's realized path counted the main warehouse's stock only, although it is described (and the engine projects) as the pooled stock of the main warehouse and its overflow hub. The sections below therefore overstate the realized stock-outs, and with them the cost of doing nothing and the regret of every policy; they also use a 60-day window, shorter than most trigger dates (median 67 days). The corrected oracle (pooled stock, 91 days) is in `engine-risk-replay-small.md`.

## Engine 1.0 on generator 0.8 (2026-10-17): the business model of record 003 in the data

Rerun on generator 0.8 (deliveries from the CO27 picking list, the main warehouse and overflow hub on the min-max rule, MRP-like purchasing; see the replay report). Every case the engine opened on 12 past Mondays (2026-05-04 to 2026-07-20) is priced under every option on the demand that followed (60 days): the lever's cost under the cost model plus the line-down cost for each day the pooled stock would have been below zero. 1,308 case instances: 628 shortage, 680 warning (from 1,882 on generator 0.7, of which 1,790 were warnings that needed nothing).

| Policy | Cost per case (USD) | Regret per case (USD) | Same choice as the oracle |
|---|---|---|---|
| **Engine 1.0** | 836 | 323 | 10.7% |
| Engine 0.2.0 rule (first covering lever, warnings too) | 874 | 361 | 10.7% |
| Cheapest lever that covers by the trigger date | 844 | 331 | 10.7% |
| Do nothing | 1,742 | 1,229 | 88.8% |
| Always an expedited new order | 5,019 | 4,506 | 0.8% |
| Hindsight oracle | 513 | 0 | 100% |

| Cases | Engine cost | Engine regret | Do nothing regret | Oracle cost | Engine agrees |
|---|---|---|---|---|---|
| Shortage (628) | 788 | 595 | 1,614 | 193 | 7.5% |
| Warning (680) | 881 | 72 | 874 | 809 | 13.7% |

Reading:

- **The cases now carry real cost.** On generator 0.7 the oracle chose do-nothing 99.7 % of the time and the engine's regret was the order cost of warnings that needed nothing. On generator 0.8 doing nothing costs 1,229 USD per case more than the oracle: the manufacturing demand is sized to the main warehouse and a missed case is a line down. The engine's regret is a quarter of that.
- **Shortage cases are where the regret is** (595 per case): the engine's lever is right only 7.5 % of the time against the oracle, mostly because the oracle, knowing the realized demand, would have done nothing on the cases that resolved themselves (do-nothing agrees 92.5 %) and would have chosen a cheaper or later lever on the rest. The engine pays for not knowing which projected stock-outs are real: record 002.
- **Warning cases confirm record 001 twice**: the engine's standard order (regret 72 per case) beats the 0.2.0 rule that paid surcharges on warnings (361 per case on all cases, 144 per warning case) and the cheapest-cover rule; and doing nothing on a warning is no longer free (874 per case), because a warning on this data often precedes a stock-out within 60 days.
- Move cases (pull, excess) are not priced: they are not order decisions.

## Engine 1.0 on generator 0.7 (2026-10-17, superseded by the section above)

1,882 case instances (92 shortage, 1,790 warning): engine cost 111 and regret 97 per case, do nothing regret 9, oracle cost 14, agreement with the oracle 0.3 % (the oracle chose do-nothing 99.7 % of the time). A property of generator 0.7, whose manufacturing demand was small against the stock sized for the v1 customer flows, not of the rule.

## Engine 0.3.0 (2026-10-14): the previous model (hub serving customers and manufacturing, both plants)


Every case the engine opened on 12 past Mondays (2026-05-04 to 2026-07-20; 6,561 case instances, 2,870 shortages and 3,691 warnings, the same material often reappearing on consecutive weeks) is priced under every option (do nothing and each engine lever) on the demand that actually followed: the case's projection with the realized hub outflow over 60 days in place of the forecast, plus the option's receipt. Cost = the lever's cost under the cost model + line-down cost (A 5,000, B 1,000, C 200 USD per day) for each day the hub would have been below zero. The oracle takes the cheapest option; regret = a policy's cost minus the oracle's. Supplier behavior is the engine's assumption (lead times, surcharges); the Phase C simulator adds its variance. `snm-data oracle`, deterministic, about one minute.

The first run of this oracle (engine 0.2.0, 2026-10-13) found almost all of the engine's regret on warning cases and proposed the rule that engine 0.3.0 now implements: levers with a surcharge only against a projected stock-out; a warning gets a transfer or a standard order. The tables below are the rerun on engine 0.3.0; the rule of 0.2.0 is kept as a policy for comparison. The case set is the same (the lever rule does not change which cases open).

## All cases

| Policy | Cost per case (USD) | Regret per case (USD) | Same choice as the oracle |
|---|---|---|---|
| **Engine 0.3.0**: expedite only against a stock-out; a warning gets a transfer or a standard order | 1,273 | 993 | 10% |
| Engine 0.2.0 rule: first lever in the fixed order that covers by the trigger date, for warnings too | 7,920 | 7,640 | 9% |
| Cheapest lever that covers by the trigger date (the engine's lever when none covers) | 1,362 | 1,082 | 10% |
| Do nothing | 1,899 | 1,619 | 88% |
| Always an expedited new order | 12,682 | 12,402 | 2% |
| Hindsight oracle | 280 | 0 | 100% |

## Shortage cases (projected below zero)

| Policy | Cost per case (USD) | Regret per case (USD) | Same choice as the oracle |
|---|---|---|---|
| **Engine 0.3.0** | 2,713 | 2,162 | 19% |
| Engine 0.2.0 rule | 2,713 | 2,162 | 19% |
| Cheapest lever that covers by the trigger date | 2,704 | 2,153 | 19% |
| Do nothing | 3,989 | 3,439 | 78% |
| Always an expedited new order | 13,141 | 12,591 | 3% |
| Hindsight oracle | 551 | 0 | 100% |

## Warning cases (below safety stock, never below zero)

| Policy | Cost per case (USD) | Regret per case (USD) | Same choice as the oracle |
|---|---|---|---|
| **Engine 0.3.0** | 153 | 84 | 3% |
| Engine 0.2.0 rule | 11,970 | 11,900 | 1% |
| Cheapest lever that covers by the trigger date | 318 | 249 | 3% |
| Do nothing | 273 | 204 | 96% |
| Always an expedited new order | 12,325 | 12,255 | 1% |
| Hindsight oracle | 69 | 0 | 100% |

Oracle choices: do_nothing 5796, new_order 685, expedited_new_order 72, expedite 8.

## Reading

- **Engine 0.3.0 cuts the regret by 87 %** (7,640 → 993 USD per case) on the same cases, all of it on warnings: the 0.2.0 rule expedited to restore safety stock, usually with an expedited new order whose surcharge (a share of the line value) bought nothing when the stock never went below zero. A standard order for warnings also beats doing nothing in total (153 against 273 per case): the few warnings that did go below zero on the realized demand cost more in line-down days than the 75 USD order cost on the rest.
- **The remaining warning regret is the order cost itself** (84 USD per case): the oracle picks doing nothing on 96 % of warnings. Separating the warnings that need an order from those that do not needs a demand-variability term in the projection (next engine increment), not a cheaper lever.
- **Shortage cases are unchanged** (2,162 USD regret per case, 19 % agreement): the engine beats doing nothing, but the oracle often finds that a standard order (later, cheaper) would have been enough on the realized demand, or that the stock-out it accepts is cheaper than the surcharge for a class C part. The lumpy demand that the replay found (large orders created after the as-of day) is behind both.
- "Cheapest lever that covers" is now worse than the engine on warnings (318 against 153): when a lever covers a warning by the trigger date it is usually an expedite, which the engine no longer takes.
- The planner agent found the same pattern on single cases (expediting a whole open order where a small expedited order for the gap was enough) and flags them for purchasing (prompt v3).

Next: a demand-variability term and warning scoring in the replay; the supplier simulator (Phase C) for supplier variance; then rerun.
