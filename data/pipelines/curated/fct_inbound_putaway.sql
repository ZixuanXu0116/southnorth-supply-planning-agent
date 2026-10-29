-- One row per inbound delivery item received into an EWM warehouse (Round Rock and Longgang), with its goods
-- receipt time (creation of the first putaway task, UTC), the site-local receipt day, the confirmed putaway
-- (if any), the destination storage type, and the dock-to-stock time.
WITH putaway AS (
  SELECT
    lgnum, rdocid, ritmid,
    MIN(created_at) AS received_at_raw,
    MIN(created_at_wh) AS received_at_wh_raw,
    MAX(CASE WHEN tostat = 'C' THEN confirmed_at END) AS stored_at_raw,
    MAX(CASE WHEN tostat = 'C' THEN nltyp END) AS nltyp,
    COUNT(*) AS putaway_tasks,
    COUNT_IF(tostat = 'A') AS cancelled_tasks
  FROM {catalog}.raw_sap_ewm.scwm_ordim_c
  WHERE procty = '1011' AND rdoccat = 'PDI'
  GROUP BY lgnum, rdocid, ritmid
),
typed AS (
  SELECT
    p.*,
    to_timestamp(concat(cast(p.received_at_raw AS STRING), ' +0000'), 'yyyyMMddHHmmss xx') AS received_at,
    to_date(substr(cast(p.received_at_wh_raw AS STRING), 1, 8), 'yyyyMMdd') AS received_local_date,
    CASE WHEN p.stored_at_raw IS NOT NULL
         THEN to_timestamp(concat(cast(p.stored_at_raw AS STRING), ' +0000'), 'yyyyMMddHHmmss xx') END AS stored_at
  FROM putaway p
)
SELECT
  p.lgnum AS warehouse,
  CASE p.lgnum WHEN 'RR01' THEN 'Round Rock' WHEN 'SZ01' THEN 'Longgang' WHEN 'DG01' THEN 'Dongguan' END AS site,
  h.docno AS inbound_delivery,
  i.itemno AS item,
  CASE h.doctype WHEN 'INB' THEN 'standard' WHEN 'INBX' THEN 'expedited' END AS receipt_type,
  ltrim('0', i.productno) AS material,
  i.qty AS quantity,
  i.uom AS unit,
  CASE i.stock_category WHEN 'F2' THEN 'unrestricted' WHEN 'Q2' THEN 'quality_inspection' END AS stock_category,
  p.received_at,
  p.received_local_date,
  p.stored_at,
  p.stored_at IS NOT NULL AS is_put_away,
  p.nltyp AS destination_storage_type,
  p.putaway_tasks,
  p.cancelled_tasks,
  CASE WHEN p.stored_at IS NOT NULL THEN timestampdiff(SECOND, p.received_at, p.stored_at) / 60.0 END
    AS dock_to_stock_minutes,
  d.fiscal_year,
  d.fiscal_quarter,
  d.fiscal_period,
  d.fiscal_week
FROM typed p
JOIN {catalog}.raw_sap_ewm.scdl_db_proci_i i ON i.docid = p.rdocid AND i.itemid = p.ritmid
JOIN {catalog}.raw_sap_ewm.scdl_db_proch_i h ON h.docid = p.rdocid
LEFT JOIN {catalog}.curated.dim_date d ON d.calendar_date = p.received_local_date
