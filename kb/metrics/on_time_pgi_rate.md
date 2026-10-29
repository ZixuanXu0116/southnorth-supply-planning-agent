---
id: metric.on_time_pgi_rate
title: On-time PGI rate
type: metric
aliases: [on-time goods issue, on-time shipping, ship on time, OT-PGI]
domain: Outbound
grain: delivery
status: draft
last_reviewed: 2026-09-24
---

# On-time PGI rate

**Definition.** Share of outbound deliveries due in the period whose goods issue was posted on or before the planned goods issue date.

- Due in period: planned GI date `LIKP.WADAT` (shipping-point local date) within the period **and** before the data cut-off day.
- On time: `WADAT_IST <> '00000000'` and `WADAT_IST <= WADAT`. Deliveries still open after their due date count as **late**.
- Reported per shipping point (site). Do not blend sites without a per-site breakdown.

```sql
SELECT vstel,
       COUNT(*) AS deliveries_due,
       SUM(CASE WHEN wadat_ist <> '00000000' AND wadat_ist <= wadat THEN 1 ELSE 0 END) AS on_time,
       try_divide(SUM(CASE WHEN wadat_ist <> '00000000' AND wadat_ist <= wadat THEN 1 ELSE 0 END), COUNT(*))
         AS on_time_pgi_rate
FROM raw_sap_ecc.likp
WHERE wadat BETWEEN :period_start AND :period_end   -- 'YYYYMMDD' strings compare correctly
  AND wadat < :cutoff_day
GROUP BY vstel
```

Caveats: planned dates shift for rush orders (same-day GI); fiscal periods, not calendar months, unless asked.

Validation: SQL executed on Databricks against the `small` profile on 2026-09-25 (runs; results plausible). Not yet checked against evaluation cases.
