---
id: concept.stock_types
title: Stock types and where stock lives
type: concept
aliases: [stock, inventory, on hand, available stock, unrestricted, quality inspection, QI, consignment, bonded, stock in transfer, in transit, MARD, MKOL, snapshot]
status: draft
last_reviewed: 2026-10-12
---

# Stock types and where stock lives

| Stock | Where | Own? | Available to issue? |
|---|---|---|---|
| Unrestricted | `MARD.LABST`; snapshot `UNRESTRICTED` | Yes | Yes, at the issuing storage locations (main warehouse `1000`; Dongguan overflow hub `3000`) |
| Quality inspection | `MARD.INSME`; snapshot `QUALITY` | Yes | No, until released (movement `321`) |
| Stock in transfer | `MARD.UMLME` at the **receiving** storage location; snapshot `IN_TRANSFER` | Yes | No, until received (`315`) |
| Bonded | Longgang storage location `1900` (unrestricted there) | Yes | No, until customs clearance (`311` 1900 → 1000) |
| Vendor consignment | `MKOL.SLABS` (`SOBKZ = K`); snapshot `CONSIGNMENT` | **No**, vendor-owned; value 0 | Only after withdrawal into own stock (`411`) |

Where each source fits:
- `MARD` / `MKOL`: stock at the nightly ECC extraction (the current state).
- `raw_ext.inventory_snapshot_daily`: history; one row per site-local day, storage location, material, and stock category, **only for non-zero stock**. A material with no row on a day had zero stock (use an anti-join or a calendar cross join to count stock-outs).
- Material documents (`MSEG`) are the ledger; stock can be rebuilt from them, but see the transfer conventions in `concept.internal_transfers` before summing.

Storage locations and their role:
- Round Rock `US10`: `1000` main warehouse issues to the manufacturing department; `2001` overflow hub (pulled from when the main warehouse falls below its minimum `MARC.EISBE`, filled by supplier deliveries and by moves when the main warehouse exceeds its maximum `MARC.MABST`; `2002`/`2003` carry no flows); `2100` holds components issued to the manufacturing building (consumed with `261`, see `concept.production_supply`).
- Longgang/Dongguan `CN10`: `1000` Longgang main warehouse issues to the manufacturing department; `1900` bonded area; `2100` components issued to manufacturing; `3000` Dongguan overflow hub, replenished from Longgang and issuing part of Longgang's picking-list lines directly.
