---
id: guide.outbound_flow
title: Outbound flow and join paths
type: guide
aliases: [production order to delivery, picking list, CO27, delivery to task, delivery to goods issue, outbound process, join path]
status: draft
last_reviewed: 2026-10-17
---

# Outbound flow and join paths

```
AFKO (production order) ──AUFNR = VGBEL──> LIPS ──VBELN──> LIKP (delivery, type ZPSA) ──> VBUK/VBUP (status)
RESB (component line) ──RSPOS = VGPOS──┘      │
              scdl_db_refdoc (REFDOCNO = LIPS.VBELN, REFITEMNO = LIPS.POSNR)
                                          │ DOCID, ITEMID
              scdl_db_proch_o / scdl_db_proci_o (EWM outbound delivery order)
                                          │ RDOCID = DOCID, RITMID = ITEMID
              scwm_ordim_c (warehouse tasks) ──(LGNUM, WAVE)──> scwm_wavehdr (waves)
                                          │ MATID ──> sapapo_matkey
LIKP.VBELN = MKPF.LE_VBELN ; LIPS (VBELN, POSNR) = MSEG (VBELN_IM, VBELP_IM)   (goods issue 311 to 2100)
```

A delivery is the picking list (CO27) of one production order at one issuing site: the receiving partner (`LIKP.KUNNR`) is a work center of the manufacturing department (`KNA1` account group `ZINT`), the planned goods issue date is the order's staging date, and the goods issue moves the components to the production supply area `2100`.

| From | To | Keys | Cardinality / notes |
|---|---|---|---|
| `afko` | `lips` | `afko.aufnr = lips.vgbel` | One production order → 1..n deliveries (one per issuing site; split deliveries exist) |
| `resb` (`BDART = 'SB'`) | `lips` | `resb.aufnr = lips.vgbel`, `lpad(resb.rspos, 6, '0') = lips.vgpos` | One reservation line → 0..1 delivery items |
| `likp` | `lips` | `vbeln` | 1 → n |
| `lips` | `scdl_db_refdoc` | `refdocno = vbeln`, `refitemno = posnr` | 1 → 1 once replicated; EWM may know deliveries not yet in the ECC extract |
| `scdl_db_refdoc` | `scwm_ordim_c` | `rdocid = docid`, `ritmid = itemid` | 1 → n: cancelled tasks are followed by a replacement task |
| `scwm_ordim_c` | `scwm_wavehdr` | `lgnum`, `wave` | n → 1 |
| `lips` | `mseg` | `vbeln_im = vbeln`, `vbelp_im = posnr` | 1 → n: two `311` items (issuing `H` at the warehouse, receiving `S` at `2100`), then possibly `312` and another `311` |

Pitfalls:
- Counting tasks per delivery item over-counts when tasks were cancelled; filter `tostat = 'C'`.
- Joining `lips` to `mseg` without handling the two items and the reversals multiplies rows; take the issuing items (`shkzg = 'H'`) net of `312`, or aggregate per item first.
- Joining ECC `MATNR` to EWM product numbers requires normalizing leading zeros.
- Waves are released on the planned goods issue day (the staging date), not when the delivery is created; the wave-to-PGI lead time starts at the release.
