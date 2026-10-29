---
id: metric.supplier_on_time_delivery
title: Supplier on-time delivery
type: metric
aliases: [supplier OTD, vendor on-time, vendor performance, supplier reliability, late suppliers, supplier delivery performance]
domain: Procurement
grain: purchase order item
status: draft
last_reviewed: 2026-09-28
---

# Supplier on-time delivery

**Definition.** Share of standard purchase order items whose **first** goods receipt was posted no later than **one day after** the agreed delivery date.

- Population: purchase orders with `EKKO.BSART = 'NB'`. Expedited emergency buys (`BSART = 'ZEX'`) are excluded; they are received the same day by construction.
- Agreed date: `EKET.EINDT`. Receipt date: earliest `MSEG.BUDAT_MKPF` of movement `101` for the order (site-local).
- Grace: one calendar day (`first receipt <= EINDT + 1`).
- Period: by the date of the first goods receipt. Orders not yet received are not in the denominator; report open overdue orders separately (`EKET.WEMNG < EKET.MENGE` and `EINDT` before the as-of date).
- Report per vendor (`EKKO.LIFNR`, names in `LFA1`) or per company code; the two plants buy in different currencies, which does not matter for this rate.

```sql
WITH first_gr AS (
  SELECT ebeln, MIN(budat_mkpf) AS first_receipt
  FROM raw_sap_ecc.mseg WHERE bwart = '101' GROUP BY ebeln
)
SELECT k.lifnr,
       COUNT(*) AS receipts,
       AVG(CASE WHEN g.first_receipt <= date_format(date_add(to_date(e.eindt, 'yyyyMMdd'), 1), 'yyyyMMdd')
                THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM raw_sap_ecc.ekko k
JOIN raw_sap_ecc.eket e ON e.ebeln = k.ebeln
JOIN first_gr g ON g.ebeln = k.ebeln
WHERE k.bsart = 'NB' AND g.first_receipt BETWEEN :period_start AND :period_end
GROUP BY k.lifnr
```

Validation: SQL executed on Databricks against the `small` profile (generator 0.2.0) on 2026-09-28 (runs; results plausible). Not yet checked against evaluation cases.
