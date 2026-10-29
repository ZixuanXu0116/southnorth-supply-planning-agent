---
id: guide.procurement_and_stock
title: Procurement and stock join paths
type: guide
aliases: [purchase order, PO, goods receipt, EKKO, EKPO, EKET, LFA1, vendor, supplier, MARD, MKOL, stock history]
status: draft
last_reviewed: 2026-09-28
---

# Procurement and stock join paths

```
LFA1 (vendor) ──LIFNR──> EKKO (PO header) ──EBELN──> EKPO (PO item) ──EBELN, EBELP──> EKET (schedule line: EINDT, WEMNG)
                                                          │
                            MSEG (bwart 101: EBELN, EBELP, LIFNR, INSMK, SOBKZ) ──MBLNR, MJAHR──> MKPF (BUDAT, USNAM)
MARD (stock at extraction, per storage location) · MKOL (vendor consignment) · raw_ext.inventory_snapshot_daily (history)
```

| From | To | Keys | Notes |
|---|---|---|---|
| `ekko` | `ekpo` | `ebeln` | One item (`00010`) per order in this dataset |
| `ekpo` | `eket` | `ebeln`, `ebelp` | `WEMNG` = received so far; `ELIKZ = 'X'` when complete |
| `ekpo` | `mseg` (`bwart = '101'`) | `ebeln`, `ebelp` | A PO can have several receipts; use the first for delivery performance |
| `mseg` | `lfa1` | `lifnr` | Filled on receipts and consignment items |
| `mard` | `mara` / `t001l` | `matnr` / `werks`, `lgort` | Stock at the nightly extraction |

Pitfalls:
- Receipts into quality inspection (`INSMK = 'X'`) or into the bonded area (`LGORT = '1900'`) are not available stock yet.
- Consignment receipts (`SOBKZ = 'K'`, `EKPO.PSTYP = '2'`) have no value (`DMBTR = 0`) and are vendor-owned.
- `561` items on 2024-10-27 are the opening balances of the data, not receipts.
