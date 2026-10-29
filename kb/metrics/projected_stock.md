---
id: metric.projected_stock
title: Projected stock and replenishment cases
type: metric
aliases: [supply projection, projected inventory, stock projection, shortage date, days short, replenishment case, planning case, will we run out, coverage, main warehouse, overflow hub, pull, move to overflow, min max, stock-out risk, stockout probability, demand model, chance of running out]
domain: Procurement
grain: material, plant, day
status: draft
last_reviewed: 2026-10-19
---

# Projected stock and replenishment cases

**Scope** (decision record 003): the planning workflow covers Round Rock's **main warehouse** `1000` (the issue point for the manufacturing department) and its **overflow hub** `2001` (cheaper storage with no demand of its own). Demand is the manufacturing department's only; there are no external customers in the business model. Customer deliveries still present in the generated data are legacy flows outside the planning scope. Longgang and Dongguan are outside the workflow for now.

**Definition.** For every externally procured material (`MARC-BESKZ` not `E`) at plant `US10`, the engine projects two lines at the end of each of the next 91 days from the as-of date (the ECC extraction date):

```
projected_main[d] = on_hand (main warehouse available stock)
                  + stock in transfer to the main warehouse (counted on day 1)
                  + Σ open purchase order quantity expected by d
                  − backlog (open component reservations due on or before the as-of date)
                  − Σ firm demand due by d (open production-order reservations RESB SB at their requirement date: the CO27 picking list)
                  − Σ forecast by d (planned-order dependent requirements RESB AR on their dates, plus the trailing 28-day rate of staging to the production supply area 2100, netted against the explicit requirements of the same week)
projected[d]      = projected_main[d] + overflow hub available stock      (the pooled line)
```

- **Expected receipt** of an open purchase order = agreed delivery date (`EKET-EINDT`) plus the vendor's median lateness over the trailing 12 months (capped at 14 days), never before tomorrow. Supplier deliveries may arrive at the main warehouse or at the overflow hub; in the generated data they arrive at the main warehouse.
- The overflow hub keeps nothing back: all of its stock (`overflow_on_hand_qty`) counts for the main warehouse.
- Governed metric views: `semantic.mv_supply_position` (the projection: `projected_qty` is the pooled line, `projected_main_qty` the main warehouse alone) and `semantic.mv_replenishment_case` (the cases). Source tables: `curated.supply_position`, `curated.replenishment_case`, `curated.supplier_profile`.

**Cases.** One case per material and plant, the first condition that applies:

| Condition | Rule | Gap |
|---|---|---|
| `shortage` | pooled stock below zero on some day; the trigger date is the first such day | the lowest pooled value, as a positive quantity |
| `warning` | pooled stock below the minimum (safety stock `MARC-EISBE`) without going below zero | minimum minus the lowest pooled value |
| `pull` | pooled stock at or above the minimum, but the main warehouse alone below it: stock to move in from the overflow hub | minimum minus the lowest main-warehouse value (never more than the overflow hub holds) |
| `excess` | main warehouse stock above the maximum (`MARC-MABST`) on every day of the next 8 weeks: stock to move out to the overflow hub | lowest main-warehouse value minus the maximum |

**Recommended lever** (`lever`, with `coverage_date`, `covers_in_time`, `days_short`):

- `shortage`: the first order lever in the order `expedite` → `new_order` → `expedited_new_order` that covers the gap by the trigger date; otherwise the earliest one, with `days_short` and the line-down cost (`stockout_cost_usd`). The overflow hub's stock is already counted and is pulled first.
- `warning`: `new_order` at the planned delivery time; no surcharge is paid to restore the minimum (decision record 001). `days_short` counts the days below the minimum until it arrives.
- `pull`: `transfer` from the overflow hub into the main warehouse (`lever_detail.from_location` = `2001`, `to_location` = `1000`, same day); decided by logistics.
- `excess`: `move_to_overflow` from the main warehouse to the overflow hub (`from_location` = `1000`, `to_location` = `2001`); the engine recommends, a person reviews, then the move is triggered.

Order levers: `expedite` an open purchase order expected after the trigger date, to the vendor's minimum expedite lead time (surcharge on the whole open quantity plus freight); `new_order` at the material's planned delivery time (`MARC-PLIFZ`); `expedited_new_order` at the vendor's minimum expedite lead time (order cost plus surcharge on the gap plus freight). The other levers are listed in `alternatives` with their coverage dates; their costs in `alternative_costs_usd` (commercial).

**Triage** (`triage_class`): `agent_eligible` when the vendor is a tail supplier (class C by trailing spend, or at most 12 orders a year), not strategic, with high historical effort on expedite requests (median rounds or days to resolution) or no such history, and the case value is at most USD 25,000; otherwise `human_handled`, with the reasons in `triage_reasons`; `no_supplier` when the material has no purchase order history at the plant. Pull and excess cases are always `human_handled` (logistics).

**Stock-out risk** (engine 1.1.0, decision record 002): the projection above uses one average forecast; demand for most parts comes in irregular lumps, so the engine also fits a **demand model** per material (`demand_model`, JSON): Croston-SBA on the weekly staging to the production supply area over the 26 weeks before the as-of date, giving the probability that a week has demand and the sizes seen in the weeks with demand. `stockout_risk_no_action` is the chance (0 to 1) that the pooled stock falls below zero on or before the trigger date if nothing is done: the share of 2,000 simulated demand paths (weeks with demand drawn with that probability, sizes drawn from history, netted against the explicit requirements week by week as the forecast is) in which it does, calibrated through the segment's curve (`demand_model.calibration`: the model's probability against the share of past windows that ran out at that level). Pages and agents show it as a percentage with two decimals. It is NULL for pull and excess cases and where the model is not qualified: materials with no outflow in the last 13 weeks (`demand_model.frequency` = `none in 13 weeks`), whose forecast the backtest could not calibrate. The service targets (`demand_model.service_target`) are a stock-out probability of at most 1 % for class A, 3 % for class B, and 10 % for class C parts (record 002 as amended on 2026-10-19); they are reference values, not yet a decision rule. The point forecast that opens cases stays the trailing 28-day rate: in the replay it catches recent bursts that the model's smoother average misses.

```sql
-- Order cases of the latest run with the highest chance of running out if nothing is done
SELECT case_id, criticality, trigger_date, ROUND(stockout_risk_no_action * 100) AS risk_pct,
       get_json_object(demand_model, '$.frequency') AS demand_weeks_of_13
FROM curated.replenishment_case
WHERE as_of_date = (SELECT MAX(as_of_date) FROM curated.replenishment_case)
  AND condition IN ('shortage', 'warning') AND stockout_risk_no_action IS NOT NULL
ORDER BY stockout_risk_no_action DESC, trigger_date
LIMIT 20
```

```sql
-- Round Rock cases of the latest engine run by condition and recommended lever
SELECT condition, lever, COUNT(*) AS cases, SUM(gap_qty) AS gap_qty
FROM curated.replenishment_case
WHERE as_of_date = (SELECT MAX(as_of_date) FROM curated.replenishment_case) AND plant = 'US10'
GROUP BY condition, lever
ORDER BY condition, lever
```

Caveats: `production_demand_qty`, `planned_order_qty`, and `production_backlog_qty` show the manufacturing demand (see `concept.production_supply`); costs (`value_usd`, lever costs, surcharges) are commercial columns readable by roles with cost access only; the as-of date is the ECC extraction date, so the latest day's EWM movements are not in it. In the generated data (generator 0.8) the main warehouse's minimum and maximum (`MARC.EISBE`, `MABST`) come from the material's demand, the overflow hub `2001` receives 30 % of supplier deliveries and the stock above the maximum, and the main warehouse pulls from it when it falls below the minimum.

Validation: hand-computed references and property tests in `data/tests/test_planning.py` and, for the demand model and the stock-out risk, `data/tests/test_demand.py`; the model's calibration in `evals/reports/forecast-accuracy-small.md`; not yet checked against evaluation cases on engine 1.1.
