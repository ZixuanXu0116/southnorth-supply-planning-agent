---
id: site.dongguan
title: Dongguan overflow hub (CN10 / DG01)
type: site
aliases: [DG, Dongguan, DG01, C300, spoke, overflow hub]
status: draft
last_reviewed: 2026-10-17
---

# Dongguan overflow hub

| Attribute | Value |
|---|---|
| Plant | `CN10` (same plant as Longgang; company currency CNY) |
| Storage location | `3000` (EWM warehouse `DG01`) |
| Shipping point | `C300` |
| Time zone | Asia/Shanghai (SAP code `UTC+8`) |
| Shifts | A 07:00–15:00, B 15:00–23:00 (Mon–Sat) |
| Waves | Hourly, 07:00–22:00 local, Monday–Saturday (template `DG-HOURLY`) |
| Role | Overflow hub of the Longgang main warehouse; stocks a subset of CN10 materials and issues part of Longgang's picking-list lines directly to the production supply area `2100` (goods issue `311` from `3000`, shipping point `C300`) |

## Site-specific rules
- Dongguan shares plant `CN10` with Longgang. **Filter by storage location (`3000`), shipping point (`C300`), or warehouse (`DG01`), never by plant alone**, when a question is about Dongguan.
- Replenished from Longgang with two-step transfers (`313` out of Longgang, `315` into Dongguan 1–2 days later) using storage-location reorder points (`MARD.DISKZ = '2'`, `LMINB`, `LBSTF`). Emergency transfers are posted by `RFC-EWM` early in the morning.
