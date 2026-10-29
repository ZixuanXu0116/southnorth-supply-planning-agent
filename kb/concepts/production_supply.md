---
id: concept.production_supply
title: Production supply and manufacturing demand
type: concept
aliases: [production order, AFKO, AFPO, RESB, reservation, dependent requirement, planned order, PLAF, staging, 261, 2100, PSA, production supply area, manufacturing demand, manufacturing backlog, bill of materials]
status: draft
last_reviewed: 2026-10-12
---

# Production supply and manufacturing demand

Each plant has a manufacturing building on the same campus as the warehouse that builds finished products (material type `FERT`) from spare-part components. In SAP it is the **production supply storage location `2100`** of the plant (`US10/2100` Round Rock assembly, `CN10/2100` Longgang assembly), inventory-managed only: no EWM warehouse, no bins, no warehouse tasks.

| Object | Table | Key fields |
|---|---|---|
| Production order header | `raw_sap_ecc.afko` | `AUFNR`, `PLNBEZ` (product), `GAMNG` (quantity), `FTRMI` (release date), `GSTRP`/`GLTRP` (scheduled start and finish), `GSTRI`/`GETRI` (actual start and finish; `00000000` while open), `RSNUM` |
| Production order item | `raw_sap_ecc.afpo` | `AUFNR`, `POSNR`, `MATNR`, `PSMNG`, `WEMNG` (delivered), `PWERK` (plant) |
| Component reservation | `raw_sap_ecc.resb` with `BDART = 'SB'` | `RSNUM`/`RSPOS`, `MATNR`, `WERKS`, `LGORT = '2100'`, `BDTER` (requirement date = order start), `BDMNG` (required), `ENMNG` (withdrawn), `XWAOK` (consumed), `XLOEK` (deleted), `AUFNR` |
| Planned order | `raw_sap_ecc.plaf` | `PLNUM`, `MATNR` (product), `PLWRK`, `GSMNG`, `PSTTR`/`PEDTR` (start and finish), `RSNUM` |
| Dependent requirement | `raw_sap_ecc.resb` with `BDART = 'AR'` | as above, `PLNUM` instead of `AUFNR`; one week of the MRP plan, weeks 2–13 after the extraction |

## Flow and movements

1. A production order is created 3–10 days before its scheduled start and released the next day (`FTRMI`). Its components are reserved at `2100` for the start date.
2. Two working days before the start the main warehouse stages the components: the picking list (CO27) is an outbound delivery (`LIKP` type `ZPSA`, `LIPS.VGBEL` = `AUFNR`, `VGPOS` = `RSPOS`) to the work center, picked in EWM and posted as `311` from `1000` (at Longgang also from the Dongguan overflow hub `3000`) to `2100`, with `MSEG.AUFNR` and the delivery on the items (see `guide.outbound_flow`).
3. On the first working day on which every component is staged, the components are consumed: `261` at `2100` against the order (`MSEG.AUFNR` set, posted by `WF-BATCH`), `RESB.ENMNG` is filled and `XWAOK = 'X'`; the order's actual start (`GSTRI`) is that day and its finish follows after the scheduled duration.
4. **Manufacturing backlog** = reservations whose requirement date has passed and whose withdrawn quantity is below the required quantity (`BDART = 'SB'`, `XLOEK = ''`, `BDTER` before the as-of date, `ENMNG < BDMNG`). These orders have not started.

## Demand for planning

- **Firm demand** of a component = open quantity (`BDMNG - ENMNG`) of its `SB` reservations at their requirement date.
- **Forecast** = dependent requirements (`AR`) of the planned orders, by requirement date; they are the MRP output and already net the firm orders, so do not add planned independent requirements on top.
- The replenishment engine counts both (`metric.projected_stock`: `production_demand_qty`, `planned_order_qty`, `production_backlog_qty`).

## Rules for analysis

- Staging (`311` to `2100`) is the warehouse's outbound flow and the demand of replenishment planning; consumption (`261`) is the manufacturing department's own movement at `2100`: exclude it from warehouse throughput and from demand (it would double count the staging).
- Stock at `2100` belongs to the plant but is already issued to manufacturing: it is not available stock of the warehouse (see `metric.available_stock`), and it is neither main-warehouse nor overflow-hub stock in the projection.
- Count reservations by `RSNUM`/`RSPOS` and filter `XLOEK = ''`; a deleted line keeps its row.
- Planned orders change with every MRP run; the extract shows the plan at the extraction only, so do not compare planned-order quantities across extraction dates as if they were history.

```sql
-- Open production reservations due in the next 14 days at Round Rock, by component
SELECT r.matnr, SUM(r.bdmng - r.enmng) AS open_qty, COUNT(DISTINCT r.aufnr) AS orders
FROM raw_sap_ecc.resb r
WHERE r.werks = 'US10' AND r.bdart = 'SB' AND r.xloek = ''
  AND r.bdter BETWEEN '20260921' AND '20261005'
GROUP BY r.matnr
ORDER BY open_qty DESC
```
