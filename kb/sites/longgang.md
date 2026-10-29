---
id: site.longgang
title: Longgang main warehouse (CN10 / SZ01)
type: site
aliases: [SZ, Longgang, Shenzhen, SZ01, C100, AutoStore site]
status: draft
last_reviewed: 2026-10-12
---

# Longgang hub (Shenzhen)

| Attribute | Value |
|---|---|
| Plant | `CN10` (company currency CNY) |
| Storage locations | `1000` hub (EWM warehouse `SZ01`), `1900` bonded area, `2100` Longgang assembly production supply area (inventory-managed only) |
| Shipping point | `C100` |
| Sales organization | `2000` |
| Time zone | Asia/Shanghai (SAP code `UTC+8`); no daylight saving time |
| Shifts | A 07:00–15:00, B 15:00–23:00, C 23:00–07:00 (seven days) |
| Waves | Every 30 minutes, 24 hours, 7 days (template `SZ-30MIN`) |
| Automation | AutoStore grid = storage type `AS01`; AutoStore picks are confirmed by the interface user `AS-INTERFACE` |

## Site-specific rules
- **Available stock** excludes storage location `1900` / storage type `BND1` (bonded) and storage type `9020` (GI staging).
- Longgang is the main warehouse; transfers to the Dongguan overflow hub (`313`/`315`) are internal movements, not issues to manufacturing.
- The manufacturing building draws components from the main warehouse: staging to `2100` (`311`, the goods issue of a delivery of type `ZPSA`) is the warehouse's issue to manufacturing; consumption against the production order (`261`) is manufacturing's own movement; see `concept.production_supply`.
- Imported materials are often received into the bonded area `1900` and moved to `1000` after customs clearance (`311`).
- Some consumables are vendor consignment stock (`MKOL`, `SOBKZ = K`): vendor-owned, no value, withdrawn with `411`.
- Chinese New Year, Labor Day (May 1–5), and National Day (Oct 1–8) reduce volume sharply; these are expected seasonality.

- AutoStore: 12 ports; controller logs (presentations, port status, system stops) in `guide.autostore`.
- Receipts are put away around the clock: bonded receipts into `BND1`, small parts decanted into AutoStore at the ports (see `guide.inbound_flow`).

## Pitfalls
- AutoStore task confirmations carry no human operator; labor productivity must exclude them or use AutoStore port data.
