---
id: concept.units_of_measure
title: Units of measure
type: concept
aliases: [base unit, sales unit, entry unit, BOX, EA, conversion, UMREZ, UMREN]
status: draft
last_reviewed: 2026-09-24
---

# Units of measure

- Every material has a **base unit** (`MARA.MEINS`, `EA` in this dataset). Some materials also have alternative units such as `BOX` in `MARM`.
- Conversion: quantity in base unit = quantity in alternative unit × `UMREZ` / `UMREN`.
- Field pairs:
  - Deliveries: `LIPS.LFIMG` in the delivery unit `VRKME` (convert with `UMVKZ` / `UMVKN`); `LIPS.LGMNG` in base unit `MEINS`.
  - Material documents: `MSEG.MENGE` in base unit `MEINS`; `MSEG.ERFMG` in entry unit `ERFME`.
  - EWM tasks: `VSOLM` in base unit `MEINS`; `VSOLA` in alternative unit `ALTME`.
- **Always aggregate quantities in the base unit.** Summing sales or entry quantities mixes EA and BOX.
