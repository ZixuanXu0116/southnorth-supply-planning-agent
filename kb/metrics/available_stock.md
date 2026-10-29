---
id: metric.available_stock
title: Available stock
type: metric
aliases: [available inventory, stock available, available to issue, on-hand available]
domain: Inventory
grain: site, material
status: draft
last_reviewed: 2026-10-17
---

# Available stock

**Definition.** Own unrestricted stock at the site's issuing storage location (what the warehouse can issue to the manufacturing department), in the base unit:

| Site | Counts | Excludes |
|---|---|---|
| Round Rock | `US10` / `1000` | overflow hubs `2001`–`2003`, production supply area `2100` (already issued to manufacturing), quality inspection |
| Longgang | `CN10` / `1000` | bonded `1900`, production supply area `2100` (already issued to manufacturing), quality inspection, vendor consignment (`MKOL`) |
| Dongguan | `CN10` / `3000` | stock in transfer (`UMLME`) until received |

- Current value: `MARD.LABST` at the listed storage location (as of the nightly ECC extraction).
- Historical value: snapshot rows with `stock_category = 'UNRESTRICTED'` at the listed storage location; missing rows mean zero.
- Report per site; never add quantities across sites without the per-site breakdown.

```sql
SELECT CASE WHEN werks = 'US10' THEN 'Round Rock' WHEN lgort = '1000' THEN 'Longgang' ELSE 'Dongguan' END AS site,
       SUM(labst) AS available_qty
FROM raw_sap_ecc.mard
WHERE (werks = 'US10' AND lgort = '1000') OR (werks = 'CN10' AND lgort IN ('1000', '3000'))
GROUP BY 1
```

Validation: SQL executed on Databricks against the `small` profile (generator 0.2.0) on 2026-09-28 (runs; results plausible). Not yet checked against evaluation cases.
