---
id: guide.replenishment_planning
title: Replenishment planning and purchasing data
type: guide
aliases: [planning, purchase order expedite, supplier terms, surcharge, lead time, supplier class, tail supplier, purchasing history, cost model, main warehouse, overflow hub, CO27]
status: draft
last_reviewed: 2026-10-17
---

# Replenishment planning and purchasing data

How the planning tables relate to the SAP tables, and which purchasing data exists.

**Scope** (decision record 003): the planning workflow covers Round Rock's main warehouse `1000` and its overflow hub `2001`, and the demand of the manufacturing department only (the CO27 picking list: open production-order reservations `RESB`). When the main warehouse is short it pulls from the overflow hub; when the two together are not enough, purchasing expedites an open order or places a new one; when the main warehouse holds more than its maximum, the surplus is moved to the overflow hub after a person's review. Longgang, Dongguan, and the legacy customer deliveries in the data are outside the workflow.

## Tables

| Table | Grain | Use |
|---|---|---|
| `curated.supply_position` | material, plant, day | the daily projection of the pooled stock (`projected_qty`) and of the main warehouse alone (`projected_main_qty`); see `metric.projected_stock` |
| `curated.replenishment_case` | case | condition (shortage, warning, pull, excess), gap, recommended lever, alternatives, rationale, triage |
| `curated.supplier_profile` | vendor | class by spend, reliability, effort on expedite requests (median rounds and days), expedite terms, agent eligibility |
| `raw_ext.supplier_condition` | vendor | expedite terms agreed with the vendor: minimum expedite days, surcharge tiers, freight, strategic flag, portal onboarding |
| `raw_ext.purchasing_interaction` | interaction | the purchasing team's exchanges on late orders, emergency buys, and confirmation inquiries (rounds, days, days gained, surcharge paid) |
| `raw_ext.cost_parameter` | parameter | the cost model (line-down cost per criticality class, holding rate, transfer and order costs) |
| `raw_sap_ecc.marc` | material, plant | safety stock `EISBE`, maximum `MABST`, planned delivery time `PLIFZ`, criticality `MAABC` |
| `raw_sap_ecc.resb` | reservation line | component demand: `BDART = 'SB'` production-order reservations (firm), `'AR'` planned-order dependent requirements (forecast); open quantity = `BDMNG - ENMNG` where `XLOEK = ''` |
| `raw_sap_ecc.afko`, `afpo`, `plaf` | production order, planned order | the orders behind the reservations (see `concept.production_supply`) |

## Join paths

- Case to purchase orders: `replenishment_case.material` = `ltrim('0', ekpo.matnr)` and `plant` = `ekpo.werks`; open quantity = `eket.menge - eket.wemng`; vendor = `ekko.lifnr`.
- Case to vendor: `replenishment_case.vendor` = `supplier_profile.vendor` = `lfa1.lifnr` = `supplier_condition.lifnr`.
- Vendor lateness: first `mseg` receipt (`bwart = '101'`, by `ebeln`) date minus `eket.eindt`.
- Case to manufacturing demand: `resb.matnr` (zero-padded) and `resb.werks`; production backlog = open `SB` lines with `bdter` before the as-of date.
- Supplier class: cumulative share of the trailing 12-month purchase order value (`ekpo.menge * ekpo.netpr / ekpo.peinh`, CNY converted at the fixed project rate): A covers the top 70 %, B the next 20 %, C the rest.

## Policies that apply

- Prices, surcharges, freight, cost parameters, and case values are **commercial** columns: NULL unless the role has cost access (`purchasing`). Do not estimate them from other columns.
- The buyer identifier in `purchasing_interaction` is personal data (masked like operator identities).
- Recommendations are the engine's; any decision (sending a request, expediting, ordering, moving stock between the main warehouse and the overflow hub) is made by a person.

Validation: tables deployed by `snm-data plan`; metric views `mv_supply_position` and `mv_replenishment_case`.
