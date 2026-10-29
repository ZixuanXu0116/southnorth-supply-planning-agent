---
id: metric.lines_shipped
title: Lines shipped
type: metric
aliases: [shipped lines, outbound lines, delivery lines shipped, order lines shipped]
domain: Outbound
grain: delivery item
status: draft
last_reviewed: 2026-09-24
---

# Lines shipped

**Definition.** Number of delivery items (picking-list lines issued to the manufacturing department) whose goods issue is posted, by actual goods issue date (`LIKP.WADAT_IST`, site-local).

- Use `VBUP.WBSTA = 'C'` to identify shipped items. Do **not** count `MSEG` `311` lines: every line has two items, and reversals (`312`) followed by re-posting would double-count.
- Quantities, if needed, come from `LIPS.LGMNG` (base unit).
- Counting `MSEG` `311` issuing items instead overstates shipped lines by roughly 1% in this dataset because of reversals and re-postings.

```sql
SELECT k.vstel, k.wadat_ist, COUNT(*) AS lines_shipped
FROM raw_sap_ecc.lips p
JOIN raw_sap_ecc.vbup u ON u.vbeln = p.vbeln AND u.posnr = p.posnr
JOIN raw_sap_ecc.likp k ON k.vbeln = p.vbeln
WHERE u.wbsta = 'C'
  AND k.wadat_ist BETWEEN :period_start AND :period_end
GROUP BY k.vstel, k.wadat_ist
```

Validation: SQL executed on Databricks against the `small` profile on 2026-09-25 (runs; results plausible). Not yet checked against evaluation cases.
