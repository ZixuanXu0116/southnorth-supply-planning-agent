---
id: metric.dock_to_stock_time
title: Dock-to-stock time
type: metric
aliases: [dock to stock, receipt to putaway, putaway lead time, inbound lead time, time to shelf, receiving cycle time]
domain: Inbound
grain: inbound delivery item
status: draft
last_reviewed: 2026-09-28
---

# Dock-to-stock time

**Definition.** Minutes from goods receipt to the confirmation of the putaway, per inbound delivery item.

- Start: the goods receipt. Putaway tasks are created automatically at goods receipt, so use the **earliest** `CREATED_AT` of the item's putaway tasks (a cancelled first task counts; it was created at receipt).
- End: `CONFIRMED_AT` of the item's confirmed putaway task (`TOSTAT = 'C'`).
- Putaway tasks: `SCWM_ORDIM_C` with `PROCTY = '1011'` and `RDOCCAT = 'PDI'`; `RDOCID`/`RITMID` identify the inbound delivery item.
- Period: site-local goods receipt date (`CREATED_AT_WH` of the first task).
- Report medians and 90th percentiles per warehouse (and per destination storage type when investigating); both timestamps are UTC, so no time zone conversion is needed.
- Items not yet put away at the EWM extraction have no confirmed task; report them separately as open putaways.
- Coverage: purchase-order receipts at `RR01` and `SZ01` (see `guide.inbound_flow`); Dongguan is not covered.

```sql
-- Assumes the session time zone is UTC.
WITH putaway AS (
  SELECT lgnum, rdocid, ritmid,
         MIN(created_at) AS gr_at,
         MIN(created_at_wh) AS gr_at_wh,
         MAX(CASE WHEN tostat = 'C' THEN confirmed_at END) AS stored_at,
         MAX(CASE WHEN tostat = 'C' THEN nltyp END) AS nltyp
  FROM raw_sap_ewm.scwm_ordim_c
  WHERE procty = '1011' AND rdoccat = 'PDI'
  GROUP BY lgnum, rdocid, ritmid
)
SELECT lgnum,
       percentile((unix_timestamp(to_timestamp(cast(stored_at AS STRING), 'yyyyMMddHHmmss'))
                   - unix_timestamp(to_timestamp(cast(gr_at AS STRING), 'yyyyMMddHHmmss'))) / 60.0, 0.5)
         AS median_minutes
FROM putaway
WHERE stored_at IS NOT NULL
  AND substr(cast(gr_at_wh AS STRING), 1, 8) BETWEEN :period_start AND :period_end
GROUP BY lgnum
```

Validation: SQL executed on Databricks against the `small` profile (generator 0.3.0) on 2026-09-28 (FY2026 P11: RR01 ~48 min, SZ01 ~49 min). Not yet checked against evaluation runs.
