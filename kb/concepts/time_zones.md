---
id: concept.time_zones
title: Time zones and timestamps
type: concept
aliases: [UTC, local time, DEC15, CPUDT, warehouse time, ECC system time]
status: draft
last_reviewed: 2026-09-29
---

# Time zones and timestamps

Three clocks appear in the raw data:

| Source | Fields | Clock |
|---|---|---|
| EWM | `*_AT`, `CRETST`, `RLS_DT`, `CUTOFF_DT`, ... | **UTC**, stored as DEC15 numbers `YYYYMMDDhhmmss`; `0` means "not reached" |
| EWM | `*_AT_WH` (e.g. `CONFIRMED_AT_WH`) | Warehouse local time, DEC15 |
| ECC | `ERDAT`/`ERZET`, `CPUDT`/`CPUTM` | **ECC system time = America/Chicago for every site**, strings `YYYYMMDD` / `HHMMSS` |
| ECC | `BUDAT`, `WADAT`, `WADAT_IST`, `LFDAT`, `PODAT` | Site-local business dates (shipping point / posting date) |

The AutoStore controller (`raw_ext.autostore_*`) logs **Longgang local time** without an offset (`TIMESTAMP_NTZ`). LTS RFID reads (`raw_ext.lts_rfid_scan.scanned_at`) are **UTC** timestamps.

Site time zones: Round Rock = America/Chicago (daylight saving time), Longgang and Dongguan = Asia/Shanghai (UTC+8, no DST).

Rules:
- Convert everything to UTC before computing durations across systems (e.g. wave release in EWM to goods issue in ECC).
- For Chinese sites, `CPUDT`/`CPUTM` are in Chicago time, typically 13–14 hours behind local time; do not use them as the local date.
- "Daily" metrics use the site-local day. EWM `*_AT_WH` fields or ECC business dates give it directly.
- `00000000` (dates) and `0` (DEC15) are SAP initial values, not real dates.

Example (Databricks SQL): `to_timestamp(cast(confirmed_at as string), 'yyyyMMddHHmmss')` gives a UTC timestamp; `from_utc_timestamp(ts, 'Asia/Shanghai')` converts it to local time.
