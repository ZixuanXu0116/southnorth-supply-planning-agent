---
id: site.round_rock
title: Round Rock main warehouse (US10 / RR01)
type: site
aliases: [RR, Round Rock, US10, RR01, U100, Texas site, main warehouse, Pflugerville overflow hub]
status: draft
last_reviewed: 2026-10-17
---

# Round Rock main warehouse

| Attribute | Value |
|---|---|
| Plant | `US10` (company currency USD) |
| Storage locations | `1000` main warehouse (EWM warehouse `RR01`; the issue point for the manufacturing department); `2001` Pflugerville overflow hub (in the planning workflow's scope); `2002` Georgetown and `2003` Hutto overflow hubs (outside the scope for now); `2100` RR assembly production supply area (the hubs and `2100` are inventory-managed only, no bins) |
| Shipping point | `U100` |
| Sales organization | `1000` |
| Time zone | America/Chicago (SAP code `CST`); observes daylight saving time |
| Shifts | A 06:00–14:00, B 14:00–22:00 (Mon–Sat) |
| Waves | Monday–Saturday. Until 2026-07-12: hourly, 06:00–21:00 local (template `RR-HOURLY`). From 2026-07-13: twice daily at 10:00 and 16:00 local (template `RR-2XDAY`, see below) |
| Profile | Low volume, high value; many serialized parts (serial profile `ZSN1`); secure cage storage type `0030` |

## Site-specific rules
- **Available stock** excludes storage types `8000` (quality inspection) and `9020` (GI staging).
- Overflow hubs hold what the main warehouse does not need right away; the main warehouse pulls from `2001` when it falls below its minimum and moves surplus out when it stays above its maximum, each after a person's review (see `metric.projected_stock`). Pulls (`311` from `2001` to `1000`, posted by `WF-BATCH`; an emergency pull on the demand day by `RFC-EWM`) and moves (`311` from `1000` to `2001` on the Monday review) are internal transfers, not issues to manufacturing; supplier deliveries arrive at either location; `2002` and `2003` are overflow locations without flows.
- The assembly building builds finished products from spare parts: components are issued from the main warehouse to `2100` (the production order's picking list as a delivery of type `ZPSA`, goods issue `311`) and consumed against the production order (`261`); see `concept.production_supply`.
- **Wave policy change on 2026-07-13:** waves are released twice a day instead of hourly. Fewer, larger waves mean longer task queue times and shorter pick durations per task; the goods-issue day is set by the staging date, so delivery lead times move little. Compare periods on either side of that date separately, and do not report the change itself as a fault.
- US holidays (Thanksgiving, Christmas, New Year, Memorial Day, Independence Day, Labor Day) reduce or stop volume; do not report them as anomalies.

- Pick totes carry RFID tags; LTS reads them at the pick-area and cage exits and at the dock doors (see `guide.rfid_tracking`).
- Purchase-order receipts arrive Monday to Friday mornings; putaway happens during the shifts (see `guide.inbound_flow`).
- Deliveries (`LIKP` type `ZPSA`) go to the work centers of the manufacturing building on the same campus; there are no external customers and no export flow (decision record 003).

## Pitfalls
- Round Rock volumes are an order of magnitude smaller than Longgang; never compare absolute counts across sites without normalization.
