---
id: concept.material_numbers
title: Material and product numbers
type: concept
aliases: [MATNR, MATID, product GUID, product number, leading zeros, part number]
status: draft
last_reviewed: 2026-09-24
---

# Material and product numbers

- ECC material number `MATNR`: 18 characters, zero-padded (e.g. `000000000040012345`).
- EWM uses a product GUID `MATID` (32 hexadecimal characters) in task and stock tables. Map it with `raw_sap_ewm.sapapo_matkey` (`MATID` → `MATNR`).
- The EWM product number (`sapapo_matkey.matnr`, `scdl_db_proci_o.productno`) is **40 characters**, zero-padded. It does not join directly to ECC `MATNR`; compare after stripping leading zeros or re-padding to 18 characters.
- Users usually quote material numbers without leading zeros ("40012345"); normalize before filtering.
