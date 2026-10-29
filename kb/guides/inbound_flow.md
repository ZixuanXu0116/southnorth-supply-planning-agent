---
id: guide.inbound_flow
title: Inbound flow, putaway, and join paths
type: guide
aliases: [inbound delivery, goods receipt in EWM, putaway, putaway task, PACI, putaway control indicator, storage type search, decanting, receiving, dock]
status: draft
last_reviewed: 2026-09-28
---

# Inbound flow, putaway, and join paths

Flow: purchase order (ECC) → shipping notification → EWM inbound delivery → goods receipt (ECC movement `101`, posted from EWM by `RFC-EWM`) → putaway task from the goods receipt zone (storage type `9010`) to a destination bin → confirmation.

```
EKKO/EKPO ──EBELN, EBELP = REFDOCNO, REFITEMNO (REFDOCCAT = 'PO')──> SCDL_DB_REFDOC ──DOCID, ITEMID──> SCDL_DB_PROCI_I ──DOCID──> SCDL_DB_PROCH_I
SCDL_DB_PROCI_I ──DOCID, ITEMID = RDOCID, RITMID (RDOCCAT = 'PDI')──> SCWM_ORDIM_C (PROCTY = '1011')
SAPAPO_MATLWH (PUT_STRA = PACI) ──MATID──> SAPAPO_MATKEY (product number) ──> ECC MATNR (strip leading zeros)
MSEG (BWART = '101') ──EBELN──> the ECC goods receipt of the same purchase order
```

Coverage:
- Purchase-order receipts at Round Rock (`US10/1000` → warehouse `RR01`) and Longgang (`CN10/1000` and the bonded area `CN10/1900` → `SZ01`). One inbound delivery with one item per receipt.
- Dongguan receives only internal transfers (`315`); their EWM inbound documents are **not** in this extract.
- Expedited purchase orders (`ZEX`) arrive as document type `INBX`; their putaway is prioritized.

Putaway tasks (`SCWM_ORDIM_C`):
- `PROCTY = '1011'`, `TRART = '1'`, source `9010` (GR zone), destination `NLTYP` / `NLPLA`, no wave (`WAVE = '0000000000'`).
- The task table also holds picks (`2010`/`2020`, `TRART = '2'`): **always filter by process type**.
- Created automatically at goods receipt by `WF-BATCH`; confirmed by inbound operators, or by `AS-INTERFACE` for AutoStore putaways (decanting into grid bins at a port).
- Cancelled tasks (`TOSTAT = 'A'`) are replaced by a new task; count the confirmed one.
- Round Rock puts away during its shifts only (Mon–Sat 06:00–22:00); Longgang works around the clock.

Destination rules (putaway control indicator, PACI = `SAPAPO_MATLWH.PUT_STRA`):

| Warehouse | PACI | Destination storage type | Intended for |
|---|---|---|---|
| RR01 | `CAGE` | `0030` Secure Cage | Serialized products or standard price above 10,000 USD |
| RR01 | `HRCK` | `0010` High Rack (reach truck) | Gross weight above 15 kg |
| RR01 | `SHLF` | `0020` Shelving | All other products |
| SZ01 | `ASTR` | `AS01` AutoStore | Most products up to 5 kg |
| SZ01 | `BULK` | `0050` Bulk | Gross weight above 15 kg |
| SZ01 | `SHLF` | `0020` Shelving | All other products |
| DG01 | `SHLF` / `BULK` | `0020` / `0050` | As SZ01 (transfer receipts, not in this extract) |

Overrides: Round Rock receipts into quality inspection (`SCDL_DB_PROCI_I.STOCK_CATEGORY = 'Q2'`) go to `8000`; Longgang bonded receipts (`CN10/1900`) go to `BND1`.

Pitfalls:
- `SAPAPO_MATLWH` holds only the configuration at extraction time. To see when putaway behavior changed, look at the destination storage types of putaway tasks over time.
- Vendor consignment receipts have `OWNER` = vendor number on the inbound delivery item; they are not own stock.
- EWM timestamps are UTC (`*_AT_WH` = warehouse local); the ECC receipt time (`MKPF.CPUDT`/`CPUTM`) is America/Chicago.
