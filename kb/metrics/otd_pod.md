---
id: metric.otd_pod
title: On-time delivery (POD-based)
type: metric
aliases: [OTD, on-time delivery, delivered on time, POD on time]
domain: Outbound
grain: delivery
status: draft
last_reviewed: 2026-09-24
---

# On-time delivery (POD-based)

**Definition.** Among POD-relevant deliveries with a confirmed proof of delivery (the work center's confirmation that the components arrived at the production supply area), the share whose POD date is on or before the planned delivery date.

- POD-relevant: `VBUK.PDSTK <> ''` (at least one item with `LIPS.PODREL = 'A'`).
- Confirmed: `LIKP.PODAT <> '00000000'` (equivalently `VBUK.PDSTK = 'C'`).
- On time: `PODAT <= LFDAT`. Period by `LFDAT`.
- Report the number of POD-relevant deliveries still **pending** confirmation separately; they are not in the denominator.
- Deliveries that are not POD-relevant are not covered; say so when asked about all deliveries.

Caveat: in this dataset `PODAT` is recorded in the shipping point's time zone.
