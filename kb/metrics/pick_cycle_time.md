---
id: metric.pick_cycle_time
title: Pick cycle time
type: metric
aliases: [pick time, task cycle time, picking duration, time to pick]
domain: WarehouseOperations
grain: warehouse task
status: draft
last_reviewed: 2026-09-28
---

# Pick cycle time

**Definition.** Minutes from warehouse task creation to confirmation for confirmed pick tasks (`TOSTAT = 'C'`, process types `2010` and `2020`).

- Variant *execution time*: `STARTED_AT` to `CONFIRMED_AT` (excludes queue time). State which variant is used.
- Exclude cancelled tasks (`TOSTAT = 'A'`); their replacement task carries the real pick.
- `SCWM_ORDIM_C` also holds putaway tasks (`PROCTY = '1011'`); counting tasks without the process-type filter mixes picks and putaways.
- Report medians and 90th percentiles per warehouse; AutoStore picks (`VLTYP = 'AS01'`) behave very differently from manual picks.
- Site-local day: `CONFIRMED_AT_WH`.

```sql
-- Assumes the session time zone is UTC.
SELECT lgnum,
       percentile_approx((unix_timestamp(to_timestamp(cast(confirmed_at AS STRING), 'yyyyMMddHHmmss'))
                         - unix_timestamp(to_timestamp(cast(created_at AS STRING), 'yyyyMMddHHmmss'))) / 60.0, 0.5)
         AS median_minutes
FROM raw_sap_ewm.scwm_ordim_c
WHERE tostat = 'C' AND procty IN ('2010', '2020')
GROUP BY lgnum
```

Validation: SQL executed on Databricks against the `small` profile on 2026-09-25 (runs; results plausible). Not yet checked against evaluation cases.
