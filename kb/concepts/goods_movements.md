---
id: concept.goods_movements
title: Goods movements and reversals
type: concept
aliases: [MSEG, MKPF, movement type, BWART, 311, 312, 101, 201, 261, 561, SHKZG, reversal, goods issue, goods receipt, PGI]
status: draft
last_reviewed: 2026-10-12
---

# Goods movements and reversals

- Material documents: header `MKPF`, items `MSEG`. Key: `MBLNR`, `MJAHR`, `ZEILE`.
- `BWART` = movement type:

| Type | Meaning |
|---|---|
| `311` to `2100` / `312` | Goods issue of a delivery to the production supply area `2100` (the picking list of a production order): per delivery line one issuing item `H` at the warehouse and one receiving item `S` at `2100` (`XAUTO = 'X'`), both with `AUFNR` and the delivery (`VBELN_IM`/`VBELP_IM`) / its reversal, items mirrored |
| `101` | Goods receipt for a purchase order (into unrestricted, quality inspection `INSMK = 'X'`, the bonded area, or vendor consignment `SOBKZ = 'K'`) |
| `201` | Goods issue to a cost center (field-service consumption at Round Rock satellites) |
| `261` | Goods issue to a production order (`AUFNR`): component consumption at the production supply area `2100`; see `concept.production_supply` |
| `311` (other than to `2100`), `313`/`315`, `321`, `411` | Internal transfers; see `concept.internal_transfers` |
| `561` | Opening balances loaded on 2024-10-27 (not receipts) |
- `SHKZG` = direction: `H` credit (stock decrease), `S` debit (stock increase). `MENGE` is always positive.
- A reversal item references the reversed document in `SMBLN` / `SJAHR` / `SMBLP`. After a reversal, the delivery is usually posted again with a new `311` document.
- **Counting `311` items overstates issues**: a goods issue has two items per line and a reversal reposts. Use delivery status (`VBUP.WBSTA = 'C'`) or the issuing items (`SHKZG = 'H'`, `UMLGO = '2100'`) net of `312` (`SHKZG = 'S'`) instead.
- `DMBTR` is in the plant's local currency (`WAERS`: USD for US10, CNY for CN10); never sum across plants without conversion. Vendor consignment items have `DMBTR = 0`.
- Filter by movement type before summing quantities: an unfiltered sum of `H` items mixes issues to manufacturing, transfers, and consumption.
