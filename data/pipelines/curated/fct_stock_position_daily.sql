-- One row per snapshot day, site, and material at the site's issuing storage location (the main warehouses
-- Round Rock US10/1000 and Longgang CN10/1000, and the Dongguan overflow hub CN10/3000, which issues part of
-- Longgang's lines directly): available (unrestricted) stock at the end of the day, demand of the
-- manufacturing department over the trailing 28 days (goods issues 311 to the production supply area 2100
-- minus reversals 312, by site-local posting date), and the resulting days of supply (NULL without demand).
-- Materials are those ever stocked or issued at the location; days without a snapshot row have zero stock.
WITH locations AS (
  SELECT 'US10' AS werks, '1000' AS lgort, 'Round Rock' AS site
  UNION ALL SELECT 'CN10', '1000', 'Longgang'
  UNION ALL SELECT 'CN10', '3000', 'Dongguan'
),
materials AS (
  SELECT DISTINCT l.site, l.werks, l.lgort, s.matnr
  FROM {catalog}.raw_ext.inventory_snapshot_daily s
  JOIN locations l ON l.werks = s.werks AND l.lgort = s.lgort
  WHERE s.stock_category = 'UNRESTRICTED'
  UNION
  SELECT DISTINCT l.site, l.werks, l.lgort, m.matnr
  FROM {catalog}.raw_sap_ecc.mseg m
  JOIN locations l ON l.werks = m.werks AND l.lgort = m.lgort
  WHERE ((m.bwart = '311' AND m.shkzg = 'H') OR (m.bwart = '312' AND m.shkzg = 'S')) AND m.umlgo = '2100'
),
span AS (
  SELECT MIN(snapshot_date) AS first_date, MAX(snapshot_date) AS last_date
  FROM {catalog}.raw_ext.inventory_snapshot_daily
),
grid AS (
  SELECT d.calendar_date AS as_of_date, m.site, m.werks, m.lgort, m.matnr
  FROM {catalog}.curated.dim_date d
  CROSS JOIN span
  CROSS JOIN materials m
  WHERE d.calendar_date BETWEEN span.first_date AND span.last_date
),
demand AS (
  SELECT werks, lgort, matnr, to_date(budat_mkpf, 'yyyyMMdd') AS posting_date,
         SUM(CASE WHEN bwart = '311' THEN menge ELSE -menge END) AS quantity
  FROM {catalog}.raw_sap_ecc.mseg
  WHERE ((bwart = '311' AND shkzg = 'H') OR (bwart = '312' AND shkzg = 'S')) AND umlgo = '2100'
  GROUP BY werks, lgort, matnr, to_date(budat_mkpf, 'yyyyMMdd')
),
stock AS (
  SELECT snapshot_date, werks, lgort, matnr, quantity
  FROM {catalog}.raw_ext.inventory_snapshot_daily
  WHERE stock_category = 'UNRESTRICTED'
),
position AS (
  SELECT
    g.as_of_date, g.site, g.werks, g.lgort, g.matnr,
    COALESCE(MAX(s.quantity), 0) AS available_qty,
    COALESCE(SUM(dm.quantity), 0) AS demand_28d_qty
  FROM grid g
  LEFT JOIN stock s
    ON s.werks = g.werks AND s.lgort = g.lgort AND s.matnr = g.matnr AND s.snapshot_date = g.as_of_date
  LEFT JOIN demand dm
    ON dm.werks = g.werks AND dm.lgort = g.lgort AND dm.matnr = g.matnr
   AND dm.posting_date BETWEEN date_sub(g.as_of_date, 27) AND g.as_of_date
  GROUP BY g.as_of_date, g.site, g.werks, g.lgort, g.matnr
)
SELECT
  p.as_of_date,
  p.site,
  p.werks AS plant,
  p.lgort AS storage_location,
  ltrim('0', p.matnr) AS material,
  p.available_qty,
  p.demand_28d_qty,
  p.demand_28d_qty / 28.0 AS avg_daily_demand,
  CASE WHEN p.demand_28d_qty > 0 THEN p.available_qty / (p.demand_28d_qty / 28.0) END AS days_of_supply,
  p.demand_28d_qty > 0 AS has_demand,
  p.available_qty <= 0 AS is_out_of_stock,
  d.fiscal_year,
  d.fiscal_quarter,
  d.fiscal_period,
  d.fiscal_week,
  p.as_of_date = d.fiscal_period_end AS is_fiscal_period_end
FROM position p
LEFT JOIN {catalog}.curated.dim_date d ON d.calendar_date = p.as_of_date
