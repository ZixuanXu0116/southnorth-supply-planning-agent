-- One row per outbound delivery (LIKP, delivery type ZPSA: the picking list of a production order delivered to
-- the production supply area of the manufacturing department) with its site, planned and actual goods issue
-- dates (shipping-point local dates) and their fiscal periods, on-time status, item counts (LIPS with the goods
-- movement status of VBUP; an item is shipped when WBSTA = 'C', so reversals are not double-counted), receipt
-- confirmation by the work center (the proof of delivery of an internal delivery), and the wave-to-goods-issue
-- lead time (wave release in EWM, UTC, to the final 311 posting in ECC, America/Chicago converted to UTC).
WITH items AS (
  SELECT p.vbeln, COUNT(*) AS delivery_lines, COUNT_IF(u.wbsta = 'C') AS shipped_lines
  FROM {catalog}.raw_sap_ecc.lips p
  JOIN {catalog}.raw_sap_ecc.vbup u ON u.vbeln = p.vbeln AND u.posnr = p.posnr
  GROUP BY p.vbeln
),
cutoff AS (
  -- ECC is extracted nightly at 00:00 America/Chicago, so it holds entries up to the day before that.
  SELECT date_add(to_date(MAX(cpudt), 'yyyyMMdd'), 1) AS ecc_cutoff_date FROM {catalog}.raw_sap_ecc.mkpf
),
wave AS (
  -- The wave of the delivery's confirmed picks (one wave per delivery in this dataset); released_at is UTC.
  SELECT r.refdocno AS vbeln,
         MIN(to_timestamp(concat(cast(h.released_at AS STRING), ' +0000'), 'yyyyMMddHHmmss xx')) AS released_at
  FROM {catalog}.raw_sap_ewm.scdl_db_refdoc r
  JOIN {catalog}.raw_sap_ewm.scwm_ordim_c t ON t.rdocid = r.docid AND t.ritmid = r.itemid
  JOIN {catalog}.raw_sap_ewm.scwm_wavehdr h ON h.lgnum = t.lgnum AND h.wave = t.wave
  WHERE r.refdoccat = 'ERP' AND t.rdoccat = 'PDO' AND t.tostat = 'C'
  GROUP BY r.refdocno
),
final_gi AS (
  -- Latest goods issue posting (311, first item) of the delivery; entry time is ECC system time (America/Chicago).
  SELECT m.le_vbeln AS vbeln,
         MAX(to_utc_timestamp(to_timestamp(concat(m.cpudt, m.cputm), 'yyyyMMddHHmmss'), 'America/Chicago')) AS gi_at
  FROM {catalog}.raw_sap_ecc.mkpf m
  JOIN {catalog}.raw_sap_ecc.mseg s ON s.mblnr = m.mblnr AND s.mjahr = m.mjahr AND s.zeile = '0001'
  WHERE s.bwart = '311'
  GROUP BY m.le_vbeln
),
deliveries AS (
  SELECT
    k.vbeln,
    k.vstel,
    k.lgnum,
    k.kunnr,
    to_date(k.wadat, 'yyyyMMdd') AS planned_gi_date,
    CASE WHEN k.wadat_ist <> '00000000' THEN to_date(k.wadat_ist, 'yyyyMMdd') END AS actual_gi_date,
    to_date(k.lfdat, 'yyyyMMdd') AS planned_delivery_date,
    CASE WHEN k.podat <> '00000000' THEN to_date(k.podat, 'yyyyMMdd') END AS pod_date,
    u.wbstk = 'C' AS is_shipped,
    u.pdstk <> '' AS is_pod_relevant
  FROM {catalog}.raw_sap_ecc.likp k
  JOIN {catalog}.raw_sap_ecc.vbuk u ON u.vbeln = k.vbeln
  WHERE k.lfart = 'ZPSA'
)
SELECT
  d.vbeln AS delivery_id,
  CASE d.vstel WHEN 'U100' THEN 'Round Rock' WHEN 'C100' THEN 'Longgang' WHEN 'C300' THEN 'Dongguan' END AS site,
  d.vstel AS shipping_point,
  d.lgnum AS warehouse,
  CASE d.vstel WHEN 'U100' THEN 'US10' ELSE 'CN10' END AS plant,
  d.kunnr AS work_center,
  d.planned_gi_date,
  d.actual_gi_date,
  d.actual_gi_date IS NOT NULL AS is_gi_posted,
  coalesce(d.actual_gi_date <= d.planned_gi_date, FALSE) AS is_on_time,
  d.planned_gi_date < c.ecc_cutoff_date AS is_due_before_cutoff,
  c.ecc_cutoff_date,
  coalesce(i.delivery_lines, 0) AS delivery_lines,
  coalesce(i.shipped_lines, 0) AS shipped_lines,
  d.planned_delivery_date,
  d.is_pod_relevant,
  d.pod_date,
  d.pod_date IS NOT NULL AS is_pod_confirmed,
  coalesce(d.pod_date <= d.planned_delivery_date, FALSE) AS is_delivered_on_time,
  w.released_at AS wave_released_at,
  g.gi_at AS final_gi_at,
  CASE WHEN d.is_shipped THEN timestampdiff(SECOND, w.released_at, g.gi_at) / 60.0 END AS wave_to_pgi_minutes,
  pd.fiscal_year AS planned_fiscal_year,
  pd.fiscal_quarter AS planned_fiscal_quarter,
  pd.fiscal_period AS planned_fiscal_period,
  pd.fiscal_week AS planned_fiscal_week,
  gd.fiscal_year AS gi_fiscal_year,
  gd.fiscal_quarter AS gi_fiscal_quarter,
  gd.fiscal_period AS gi_fiscal_period,
  gd.fiscal_week AS gi_fiscal_week,
  dd.fiscal_year AS delivery_fiscal_year,
  dd.fiscal_quarter AS delivery_fiscal_quarter,
  dd.fiscal_period AS delivery_fiscal_period,
  dd.fiscal_week AS delivery_fiscal_week
FROM deliveries d
CROSS JOIN cutoff c
LEFT JOIN items i ON i.vbeln = d.vbeln
LEFT JOIN wave w ON w.vbeln = d.vbeln
LEFT JOIN final_gi g ON g.vbeln = d.vbeln
LEFT JOIN {catalog}.curated.dim_date pd ON pd.calendar_date = d.planned_gi_date
LEFT JOIN {catalog}.curated.dim_date gd ON gd.calendar_date = d.actual_gi_date
LEFT JOIN {catalog}.curated.dim_date dd ON dd.calendar_date = d.planned_delivery_date
