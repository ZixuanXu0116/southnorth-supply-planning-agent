---
id: metric.autostore_availability
title: AutoStore availability (system and port)
type: metric
aliases: [AutoStore uptime, AutoStore downtime, system availability, port availability, AutoStore stops, port closed]
domain: Automation
grain: site-local period; port
status: draft
last_reviewed: 2026-09-29
---

# AutoStore availability

**System availability.** Share of calendar time in the period during which the AutoStore system was not stopped:
`1 - (minutes inside STOPPED intervals of raw_ext.autostore_system_status) / (minutes in the period)`.

**Port availability.** Per port, share of calendar time in the period with status `OPEN` in `raw_ext.autostore_port_status`. Report planned maintenance (`reason = 'MAINTENANCE'`) and faults (`reason = 'FAULT'`) separately when explaining low values.

Rules:
- Periods are Longgang local (the controller logs local time); fiscal periods from `concept.fiscal_calendar`.
- Clip intervals to the period; an open interval (`valid_to IS NULL`) ends at the data cut-off.
- Longgang works 24 hours, 7 days: the period's calendar minutes are the denominator.

```sql
-- System availability in FY2026 P11 (2026-08-24 00:00 to 2026-09-21 00:00 local)
SELECT 1 - SUM((unix_timestamp(LEAST(ended_at, TIMESTAMP_NTZ'2026-09-21 00:00:00'))
                - unix_timestamp(GREATEST(started_at, TIMESTAMP_NTZ'2026-08-24 00:00:00'))) / 60.0)
           / (28 * 24 * 60) AS system_availability
FROM raw_ext.autostore_system_status
WHERE started_at < TIMESTAMP_NTZ'2026-09-21 00:00:00' AND ended_at > TIMESTAMP_NTZ'2026-08-24 00:00:00'
```

Validation: SQL executed on Databricks against the `small` profile (generator 0.4.0) on 2026-09-29 (FY2026 P11: 99.75 %). Not yet checked against evaluation runs.
