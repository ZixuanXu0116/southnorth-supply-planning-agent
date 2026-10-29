---
id: guide.rfid_tracking
title: RFID tote tracking at Round Rock (LTS)
type: guide
aliases: [RFID, LTS, tote, tag, reader, scan, dock door, cage exit, physical pick time, tracking]
status: draft
last_reviewed: 2026-10-19
---

# RFID tote tracking at Round Rock (LTS)

Round Rock picks each EWM warehouse order into one tote with an RFID tag. The LTS tracking service records every read in `raw_ext.lts_rfid_scan`.

| Zone | Readers | When the tote is read |
|---|---|---|
| `PICK_EXIT` | `RR-PICK-EXIT-01` to `-03` | Leaving the pick area after the last pick of the warehouse order |
| `CAGE_EXIT` | `RR-CAGE-EXIT` | Leaving the secure cage (storage type `0030`) after the last pick |
| `DOCK` | `RR-DOCK-01`, `RR-DOCK-02` | Passing a dock door at truck loading (shipped deliveries only) |

Joining to EWM and ECC:
- `lts_rfid_scan.who` = `scwm_ordim_c.who` (the warehouse order; one per delivery). One exit read and at most one dock read per warehouse order.
- The warehouse order's tasks give the delivery through `RDOCID`/`RITMID` → `scdl_db_refdoc` (`REFDOCCAT = 'ERP'`).

Pitfalls:
- `SCANNED_AT` is a UTC timestamp with time zone; Round Rock business days are America/Chicago.
- Scans exist only for Round Rock and only for picks (no putaway scans).
- A read is a physical event; EWM confirmation times are entered by people and can differ from it. Many emergency picks (process type `2020`) are confirmed in EWM well after the physical pick, so their EWM cycle time overstates how long they take: measure pick speed by order type from the task creation to the warehouse order's exit read, and say that confirmations lag.
