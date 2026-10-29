-- One row per calendar day with its fiscal year, quarter, period, and week (fiscal year variant Z5 in T009B:
-- 52/53-week years ending on the last Sunday of October, 4-4-5 periods, weeks starting on Monday).
-- Starts the day after the earliest period-12 end in T009B, so every day has a complete period definition.
WITH period_ends AS (
  SELECT
    make_date(CAST(bdatj AS INT), CAST(bumon AS INT), CAST(butag AS INT)) AS row_end,
    CAST(bdatj AS INT) + CAST(trim(reljr) AS INT) AS fiscal_year,
    CAST(poper AS INT) AS fiscal_period
  FROM {catalog}.raw_sap_ecc.t009b
  WHERE periv = 'Z5'
),
days AS (
  SELECT explode(sequence(
    date_add((SELECT MIN(row_end) FROM period_ends WHERE fiscal_period = 12), 1),
    (SELECT MAX(row_end) FROM period_ends),
    INTERVAL 1 DAY
  )) AS calendar_date
),
assigned AS (
  -- T009B lookup rule: the first row of the date's calendar year that ends on or after the date.
  SELECT d.calendar_date, e.fiscal_year, e.fiscal_period
  FROM days d
  JOIN period_ends e ON year(e.row_end) = year(d.calendar_date) AND e.row_end >= d.calendar_date
  QUALIFY row_number() OVER (PARTITION BY d.calendar_date ORDER BY e.row_end) = 1
),
bounded AS (
  SELECT
    *,
    MIN(calendar_date) OVER (PARTITION BY fiscal_year) AS fiscal_year_start,
    MIN(calendar_date) OVER (PARTITION BY fiscal_year, fiscal_period) AS fiscal_period_start,
    MAX(calendar_date) OVER (PARTITION BY fiscal_year, fiscal_period) AS fiscal_period_end
  FROM assigned
)
SELECT
  calendar_date,
  fiscal_year,
  CAST(ceil(fiscal_period / 3) AS INT) AS fiscal_quarter,
  fiscal_period,
  CAST(floor(datediff(calendar_date, fiscal_year_start) / 7) + 1 AS INT) AS fiscal_week,
  fiscal_period_start,
  fiscal_period_end
FROM bounded
