---
id: metric.wave_to_pgi_lead_time
title: Wave-to-PGI lead time
type: metric
aliases: [release to ship, wave lead time, release-to-goods-issue time]
domain: Outbound
grain: delivery
status: draft
last_reviewed: 2026-10-12
---

# Wave-to-PGI lead time

**Definition.** For shipped deliveries, the time from wave release in EWM (`scwm_wavehdr.released_at`, UTC) to the final goods issue posting in ECC (latest `311` material document for the delivery; `MKPF.CPUDT`/`CPUTM` in America/Chicago, converted to UTC).

- Only deliveries with `VBUK.WBSTK = 'C'` (reversed-and-not-reposted deliveries are not shipped).
- Period: assign deliveries by actual goods issue date (`LIKP.WADAT_IST`, site-local).
- Cross-system time: convert both ends to UTC before subtracting (see time zones concept).
- Join path: `likp` → `scdl_db_refdoc` → `scwm_ordim_c` (confirmed tasks) → `scwm_wavehdr`.
- Round Rock changed its wave policy on 2026-07-13 (hourly waves → two waves a day, template `RR-2XDAY`; see `site.round_rock`): task queue times and the number of waves change with the policy (picking lists are released on the staging day, so the lead time itself moves little); compare the periods before and after separately and do not report the step as a fault.

```sql
-- Assumes the session time zone is UTC.
WITH delivery_wave AS (
  SELECT DISTINCT r.refdocno AS vbeln, t.lgnum, t.wave
  FROM raw_sap_ewm.scdl_db_refdoc r
  JOIN raw_sap_ewm.scwm_ordim_c t ON t.rdocid = r.docid AND t.ritmid = r.itemid
  WHERE t.tostat = 'C'
),
final_gi AS (
  SELECT m.le_vbeln AS vbeln,
         MAX(to_utc_timestamp(to_timestamp(concat(m.cpudt, m.cputm), 'yyyyMMddHHmmss'), 'America/Chicago')) AS gi_utc
  FROM raw_sap_ecc.mkpf m
  JOIN raw_sap_ecc.mseg s ON s.mblnr = m.mblnr AND s.mjahr = m.mjahr AND s.zeile = '0001'
  WHERE s.bwart = '311'
  GROUP BY m.le_vbeln
)
SELECT w.lgnum,
       percentile_approx((unix_timestamp(g.gi_utc)
         - unix_timestamp(to_timestamp(cast(h.released_at AS STRING), 'yyyyMMddHHmmss'))) / 60.0, 0.5) AS median_minutes
FROM delivery_wave w
JOIN raw_sap_ewm.scwm_wavehdr h ON h.lgnum = w.lgnum AND h.wave = w.wave
JOIN final_gi g ON g.vbeln = w.vbeln
JOIN raw_sap_ecc.vbuk k ON k.vbeln = w.vbeln AND k.wbstk = 'C'
GROUP BY w.lgnum
```

Caveat: ECC is extracted nightly; very recent goods issues may be missing even when EWM shows picking complete.

Validation: SQL executed on Databricks against the `small` profile on 2026-09-25 (runs; results plausible). Not yet checked against evaluation cases.
