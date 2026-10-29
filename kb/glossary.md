---
id: glossary
title: Glossary
type: glossary
aliases: [SAP terms, abbreviations, acronyms]
status: draft
last_reviewed: 2026-09-29
---

# Glossary

| Term | Meaning |
|---|---|
| Bonded area | Longgang storage location `1900`: imported goods before customs clearance; not available stock. |
| Consignment (vendor) | Stock owned by the vendor while stored at our site (`MKOL`, `SOBKZ = K`); becomes own stock with `411`. |
| Days of supply | Available stock divided by the average daily demand of the manufacturing department; see `metric.days_of_supply`. |
| Decanting | Moving received goods into AutoStore bins at a port; confirmed by `AS-INTERFACE`. |
| Dock-to-stock time | Minutes from goods receipt to putaway confirmation; see `metric.dock_to_stock_time`. |
| LTS | Tracking service that records RFID tote reads at Round Rock (`raw_ext.lts_rfid_scan`). |
| Inbound delivery | EWM document for an expected receipt (`SCDL_DB_PROCH_I`/`PROCI_I`, document category `PDI`). |
| PACI | Putaway control indicator (`SAPAPO_MATLWH.PUT_STRA`): selects the destination storage types for putaway; see `guide.inbound_flow`. |
| Putaway task | Warehouse task moving received goods from the GR zone (`9010`) to a bin; process type `1011`. |
| Expedited PO | Emergency purchase order, document type `ZEX`, received the same day. |
| Stock in transfer | Stock between `313` and `315` on the way to the receiving storage location (`MARD.UMLME`). |
| AutoStore | Cube storage system at Longgang; robots bring bins to ports where operators pick. EWM storage type `AS01`. |
| Bin presentation | One AutoStore bin delivered to a port; one presentation can serve several picks (`raw_ext.autostore_bin_presentation`). |
| Port (AutoStore) | Workstation where robots present grid bins (`AS-PORT-01` to `AS-PORT-12`); the EWM resource `PRSRC` of grid tasks. |
| DATS / TIMS | SAP date (`YYYYMMDD`) and time (`HHMMSS`) strings; `00000000` / `000000` mean empty. |
| DEC15 | EWM timestamp number `YYYYMMDDhhmmss` in UTC; `0` means empty. |
| Delivery (outbound) | ECC document (`LIKP`/`LIPS`, type `ZPSA`) that issues the picking list of a production order to a work center of the manufacturing department at the production supply area `2100`; executed in EWM as an outbound delivery order (ODO). |
| ECC | SAP ERP Central Component: orders, deliveries, material master, goods movements, valuation. |
| CO27 | SAP picking list of component requirements for production orders: the manufacturing department's demand as the warehouse sees it (open reservations `RESB`, requirement type `SB`); in the project it stands for all manufacturing demand. |
| EWM | SAP Extended Warehouse Management: bins, tasks, waves, warehouse orders. Decentralized here (separate system). |
| GI / PGI | (Post) goods issue: stock leaves the warehouse; for a delivery to the production supply area, movement type `311` (reversal `312`). |
| HTS / STAWN | Tariff (commodity) code; `MARC.STAWN`. Not an export control classification. |
| Main warehouse and overflow hubs | The main warehouse (Round Rock `1000`, Longgang `1000`) is the issue point for the manufacturing department; overflow hubs (Round Rock `2001`–`2003`, Dongguan `3000`) are cheaper storage with no demand of their own, pulled from when the main warehouse falls below its minimum and filled when it stays above its maximum (min–max). Older pages and the generated data may still say hub (for the main warehouse) and spoke or satellite (for an overflow hub). |
| MATID | EWM product GUID; map via `sapapo_matkey`. |
| MATNR | Material number (18 characters in ECC, 40 in EWM). |
| Movement type (BWART) | Code classifying a goods movement (e.g. `311` goods issue to the production supply area, `312` its reversal, `101` goods receipt). |
| ODO | Outbound delivery order in EWM (`scdl_db_proch_o` / `scdl_db_proci_o`). |
| PACI | Putaway control indicator (`PUT_STRA`): drives which storage types EWM proposes for putaway. |
| POD | Proof of delivery: the work center's confirmation of receipt (`LIKP.PODAT`); only for POD-relevant deliveries. |
| Process type (PROCTY) | EWM warehouse process type; `2010` pick for delivery, `2020` emergency pick. |
| PSA | Production supply area: staging area for production components. |
| Shipping point (VSTEL) | `U100` Round Rock, `C100` Longgang, `C300` Dongguan. |
| Work center (receiving partner) | A work center of the manufacturing department, the receiving partner of a delivery (`LIKP.KUNNR`, `KNA1` account group `ZINT`). There are no external customers. |
| Special stock (SOBKZ) | `E` sales-order stock, `K` vendor consignment; not freely available own stock. |
| Storage type (LGTYP) | Area of a warehouse with common rules (e.g. `0030` secure cage, `AS01` AutoStore, `9020` GI staging). |
| TOSTAT | Warehouse task status in this dataset: `C` confirmed, `A` cancelled. |
| Warehouse order (WHO) | Group of tasks executed together by one resource. |
| Warehouse task (WT) | EWM instruction to move a product (pick, putaway, replenishment); `scwm_ordim_c`. |
| Wave | Group of delivery items released together for picking; `scwm_wavehdr`. |
