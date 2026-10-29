---
id: metric.days_of_supply
title: Days of supply
type: metric
aliases: [DOS, days of inventory, days of cover, stock coverage, how long will stock last]
domain: Inventory
grain: site, material
status: draft
last_reviewed: 2026-10-17
---

# Days of supply

**Definition.** Available stock (see `metric.available_stock`) divided by the average daily demand of the manufacturing department over the **trailing 28 calendar days** ending on the as-of date, per site and material.

- Demand = net issues to the production supply area `2100` from the site's issuing storage location: the `311` issuing items (`SHKZG = 'H'`, `UMLGO = '2100'`) minus the `312` reversal items back at the warehouse (`SHKZG = 'S'`, `UMLGO = '2100'`), by site-local posting date. Transfers between storage locations (`311` elsewhere, `313`/`315`), satellite consumption (`201`), consumption at `2100` (`261`), and receipts do not count.
- Materials with zero demand in the window have undefined days of supply; report them separately (count), do not treat them as infinite or zero.
- For an as-of date in the past, use the snapshot of that date for stock and the 28 days ending on that date for demand.

```sql
-- Longgang, as of the last complete day 2026-09-20
WITH demand AS (
  SELECT matnr,
         SUM(CASE WHEN bwart = '311' THEN menge ELSE -menge END) / 28.0 AS avg_daily_demand
  FROM raw_sap_ecc.mseg
  WHERE werks = 'CN10' AND lgort = '1000' AND umlgo = '2100'
    AND ((bwart = '311' AND shkzg = 'H') OR (bwart = '312' AND shkzg = 'S'))
    AND budat_mkpf BETWEEN '20260824' AND '20260920'
  GROUP BY matnr
),
stock AS (
  SELECT matnr, quantity
  FROM raw_ext.inventory_snapshot_daily
  WHERE snapshot_date = DATE'2026-09-20' AND werks = 'CN10' AND lgort = '1000' AND stock_category = 'UNRESTRICTED'
)
SELECT d.matnr, COALESCE(s.quantity, 0) / d.avg_daily_demand AS days_of_supply
FROM demand d LEFT JOIN stock s ON s.matnr = d.matnr
WHERE d.avg_daily_demand > 0
```

Validation: definition changed with generator 0.8 (deliveries to manufacturing); to be executed on Databricks after the reload. Not yet checked against evaluation cases.
