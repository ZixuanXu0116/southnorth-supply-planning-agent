-- One row per purchase order schedule line (EKET with its order header EKKO and item EKPO): vendor, receiving
-- site, order type, agreed delivery date, quantities, the order's first goods receipt (earliest 101 posting,
-- site-local date), whether it arrived on time (no later than one day after the agreed date), and whether it
-- is open and overdue at the ECC data cut-off.
WITH first_receipt AS (
  SELECT ebeln, MIN(budat_mkpf) AS first_receipt
  FROM {catalog}.raw_sap_ecc.mseg
  WHERE bwart = '101'
  GROUP BY ebeln
),
cutoff AS (
  SELECT date_add(to_date(MAX(cpudt), 'yyyyMMdd'), 1) AS ecc_cutoff_date FROM {catalog}.raw_sap_ecc.mkpf
),
lines AS (
  SELECT
    e.ebeln, e.ebelp, e.etenr, k.bukrs, p.werks, p.lgort, k.bsart, k.lifnr, p.matnr,
    to_date(e.eindt, 'yyyyMMdd') AS agreed_delivery_date,
    e.menge AS scheduled_qty,
    e.wemng AS received_qty,
    to_date(g.first_receipt, 'yyyyMMdd') AS first_receipt_date
  FROM {catalog}.raw_sap_ecc.eket e
  JOIN {catalog}.raw_sap_ecc.ekko k ON k.ebeln = e.ebeln
  JOIN {catalog}.raw_sap_ecc.ekpo p ON p.ebeln = e.ebeln AND p.ebelp = e.ebelp
  LEFT JOIN first_receipt g ON g.ebeln = e.ebeln
)
SELECT
  l.ebeln AS purchase_order,
  l.ebelp AS item,
  l.etenr AS schedule_line,
  l.bukrs AS company_code,
  l.werks AS plant,
  l.lgort AS storage_location,
  CASE l.werks WHEN 'US10' THEN 'Round Rock' WHEN 'CN10' THEN CASE l.lgort WHEN '3000' THEN 'Dongguan' ELSE 'Longgang' END END AS site,
  CASE l.bsart WHEN 'NB' THEN 'standard' WHEN 'ZEX' THEN 'expedited' END AS order_type,
  l.lifnr AS vendor,
  v.name1 AS vendor_name,
  v.land1 AS vendor_country,
  ltrim('0', l.matnr) AS material,
  l.agreed_delivery_date,
  l.scheduled_qty,
  l.received_qty,
  l.received_qty >= l.scheduled_qty AS is_fully_received,
  l.first_receipt_date,
  l.first_receipt_date IS NOT NULL AS is_received,
  coalesce(l.first_receipt_date <= date_add(l.agreed_delivery_date, 1), FALSE) AS is_on_time,
  l.received_qty < l.scheduled_qty AND l.agreed_delivery_date < c.ecc_cutoff_date AS is_overdue_open,
  c.ecc_cutoff_date,
  d.fiscal_year AS receipt_fiscal_year,
  d.fiscal_quarter AS receipt_fiscal_quarter,
  d.fiscal_period AS receipt_fiscal_period,
  d.fiscal_week AS receipt_fiscal_week
FROM lines l
CROSS JOIN cutoff c
LEFT JOIN {catalog}.raw_sap_ecc.lfa1 v ON v.lifnr = l.lifnr
LEFT JOIN {catalog}.curated.dim_date d ON d.calendar_date = l.first_receipt_date
