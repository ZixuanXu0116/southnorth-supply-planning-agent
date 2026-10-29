-- One row per EWM warehouse task (SCWM_ORDIM_C): picks, emergency picks, and putaways, confirmed or cancelled,
-- with UTC timestamps, the site-local confirmation day, hour, and shift, the product, and the cycle and
-- execution times of confirmed tasks. For cancelled tasks, confirmed_at holds the cancellation time.
-- operator is the confirming user: the column mask of SCWM_ORDIM_C.confirmed_by applies through the view, so
-- roles without people-level access see NULL (system users excepted).
WITH tasks AS (
  SELECT
    t.lgnum, t.tanum, t.tapos, t.procty, t.tostat, t.wave, t.who, t.vltyp, t.nltyp, t.prsrc, t.confirmed_by,
    k.matnr AS product_number,
    to_timestamp(concat(cast(t.created_at AS STRING), ' +0000'), 'yyyyMMddHHmmss xx') AS created_at,
    CASE WHEN t.started_at > 0
         THEN to_timestamp(concat(cast(t.started_at AS STRING), ' +0000'), 'yyyyMMddHHmmss xx') END AS started_at,
    to_timestamp(concat(cast(t.confirmed_at AS STRING), ' +0000'), 'yyyyMMddHHmmss xx') AS confirmed_at,
    to_date(substr(cast(t.confirmed_at_wh AS STRING), 1, 8), 'yyyyMMdd') AS confirmed_local_date,
    CAST(substr(cast(t.confirmed_at_wh AS STRING), 9, 2) AS INT) AS confirmed_local_hour
  FROM {catalog}.raw_sap_ewm.scwm_ordim_c t
  LEFT JOIN {catalog}.raw_sap_ewm.sapapo_matkey k ON k.mandt = t.mandt AND k.matid = t.matid
)
SELECT
  t.lgnum AS warehouse,
  CASE t.lgnum WHEN 'RR01' THEN 'Round Rock' WHEN 'SZ01' THEN 'Longgang' WHEN 'DG01' THEN 'Dongguan' END AS site,
  t.tanum AS task_number,
  t.tapos AS task_item,
  CASE t.procty WHEN '2010' THEN 'pick' WHEN '2020' THEN 'emergency_pick' WHEN '1011' THEN 'putaway' END AS task_type,
  CASE t.tostat WHEN 'C' THEN 'confirmed' WHEN 'A' THEN 'cancelled' END AS status,
  t.tostat = 'C' AS is_confirmed,
  t.procty IN ('2010', '2020') AS is_pick,
  t.vltyp = 'AS01' AS is_autostore,
  coalesce(t.confirmed_by IN ('WF-BATCH', 'AS-INTERFACE', 'RFC-EWM'), FALSE) AS is_system_user,
  t.confirmed_by AS operator,
  ltrim('0', t.product_number) AS material,
  t.vltyp AS source_storage_type,
  t.nltyp AS destination_storage_type,
  t.wave,
  t.who AS warehouse_order,
  t.prsrc AS resource,
  t.created_at,
  t.started_at,
  t.confirmed_at,
  t.confirmed_local_date,
  t.confirmed_local_hour,
  CASE
    WHEN t.lgnum = 'RR01' AND t.confirmed_local_hour BETWEEN 6 AND 13 THEN 'A'
    WHEN t.lgnum = 'RR01' AND t.confirmed_local_hour BETWEEN 14 AND 21 THEN 'B'
    WHEN t.lgnum = 'SZ01' AND t.confirmed_local_hour BETWEEN 7 AND 14 THEN 'A'
    WHEN t.lgnum = 'SZ01' AND t.confirmed_local_hour BETWEEN 15 AND 22 THEN 'B'
    WHEN t.lgnum = 'SZ01' THEN 'C'
    WHEN t.lgnum = 'DG01' AND t.confirmed_local_hour BETWEEN 7 AND 14 THEN 'A'
    WHEN t.lgnum = 'DG01' AND t.confirmed_local_hour BETWEEN 15 AND 22 THEN 'B'
  END AS shift,
  CASE WHEN t.tostat = 'C' THEN timestampdiff(SECOND, t.created_at, t.confirmed_at) / 60.0 END AS cycle_minutes,
  CASE WHEN t.tostat = 'C' AND t.started_at IS NOT NULL
       THEN timestampdiff(SECOND, t.started_at, t.confirmed_at) / 60.0 END AS execution_minutes,
  d.fiscal_year,
  d.fiscal_quarter,
  d.fiscal_period,
  d.fiscal_week
FROM tasks t
LEFT JOIN {catalog}.curated.dim_date d ON d.calendar_date = t.confirmed_local_date
