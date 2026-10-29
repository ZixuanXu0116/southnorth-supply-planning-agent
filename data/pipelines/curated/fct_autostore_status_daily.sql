-- One row per calendar day (Longgang local) and AutoStore subject: the whole system ('SYSTEM') and each port.
-- Stop and closure intervals are clipped to the day, so availability over any range of days is
-- SUM(available_minutes) / SUM(minutes_in_day). Days run from the first logged interval to the controller
-- data cut-off (latest bin presentation); the cut-off day is partial.
WITH cutoff AS (
  SELECT MAX(presented_at) AS cutoff_at FROM {catalog}.raw_ext.autostore_bin_presentation
),
ports AS (
  SELECT port_id, MIN(valid_from) AS first_at FROM {catalog}.raw_ext.autostore_port_status GROUP BY port_id
),
subjects AS (
  SELECT 'SYSTEM' AS subject, 'system' AS subject_type, MIN(first_at) AS first_at FROM ports
  UNION ALL
  SELECT port_id, 'port', first_at FROM ports
),
days AS (
  SELECT
    s.subject,
    s.subject_type,
    d.calendar_date,
    CAST(d.calendar_date AS TIMESTAMP_NTZ) AS day_start,
    LEAST(CAST(date_add(d.calendar_date, 1) AS TIMESTAMP_NTZ), c.cutoff_at) AS day_end
  FROM subjects s
  CROSS JOIN cutoff c
  JOIN {catalog}.curated.dim_date d
    ON d.calendar_date >= to_date(s.first_at) AND d.calendar_date <= to_date(c.cutoff_at)
),
intervals AS (
  SELECT p.port_id AS subject, p.status, p.reason, p.valid_from AS started_at,
         COALESCE(p.valid_to, c.cutoff_at) AS ended_at
  FROM {catalog}.raw_ext.autostore_port_status p
  CROSS JOIN cutoff c
  UNION ALL
  SELECT 'SYSTEM', 'STOPPED', reason, started_at, ended_at
  FROM {catalog}.raw_ext.autostore_system_status
),
overlap AS (
  SELECT
    d.subject, d.subject_type, d.calendar_date, d.day_start, d.day_end, i.status, i.reason,
    timestampdiff(SECOND, GREATEST(i.started_at, d.day_start), LEAST(i.ended_at, d.day_end)) / 60.0 AS minutes
  FROM days d
  LEFT JOIN intervals i ON i.subject = d.subject AND i.started_at < d.day_end AND i.ended_at > d.day_start
),
daily AS (
  SELECT
    subject,
    subject_type,
    calendar_date,
    timestampdiff(SECOND, day_start, day_end) / 60.0 AS minutes_in_day,
    SUM(CASE WHEN status IN ('STOPPED', 'CLOSED') THEN minutes ELSE 0 END) AS downtime_minutes,
    SUM(CASE WHEN status = 'CLOSED' AND reason = 'MAINTENANCE' THEN minutes ELSE 0 END) AS maintenance_minutes,
    SUM(CASE WHEN status = 'CLOSED' AND reason = 'FAULT' THEN minutes ELSE 0 END) AS fault_minutes,
    COUNT(CASE WHEN status IN ('STOPPED', 'CLOSED') THEN 1 END) AS downtime_events
  FROM overlap
  GROUP BY subject, subject_type, calendar_date, day_start, day_end
)
SELECT
  a.subject,
  a.subject_type,
  a.calendar_date,
  a.minutes_in_day,
  a.minutes_in_day - a.downtime_minutes AS available_minutes,
  a.downtime_minutes,
  a.maintenance_minutes,
  a.fault_minutes,
  a.downtime_events,
  d.fiscal_year,
  d.fiscal_quarter,
  d.fiscal_period,
  d.fiscal_week
FROM daily a
LEFT JOIN {catalog}.curated.dim_date d ON d.calendar_date = a.calendar_date
