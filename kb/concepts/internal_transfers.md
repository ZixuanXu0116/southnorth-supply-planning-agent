---
id: concept.internal_transfers
title: Internal transfers (main warehouse and overflow hubs)
type: concept
aliases: [transfer, stock transfer, replenishment, 311, 313, 315, 411, main warehouse, overflow hub, pull, move to overflow, satellite replenishment, emergency transfer, stock in transfer, double counting, staging]
status: draft
last_reviewed: 2026-10-19
---

# Internal transfers

| Route | Movement types | Timing |
|---|---|---|
| Round Rock main warehouse `1000` ↔ overflow hubs `2001`–`2003` | `311` (one step) | Same day. Business model (decision record 003): the main warehouse pulls from the overflow hub when it falls below its minimum and moves surplus out when it stays above its maximum, both after a person's review. Generated data (generator 0.7, legacy): the main warehouse replenishes the hubs when they fall below their reorder point (`MARD.LMINB`); generator 0.8 will produce the pull and move flows |
| Main warehouse `1000` → production supply area `2100` (both plants) | `311` (one step, `MSEG.AUFNR` blank) | Two working days before the production order starts, as far as hub stock allows; consumption at `2100` is `261` against the order (see `concept.production_supply`) |
| Longgang main warehouse `1000` → Dongguan overflow hub `3000` (outside the planning workflow) | `313` removal, then `315` placement | Arrives 1–2 days later; in between the stock is "in transfer" at `3000` |
| Longgang bonded `1900` → main warehouse `1000` | `311` | After customs clearance, 2–7 days after receipt |
| Quality inspection → unrestricted | `321` | 1–4 days after receipt |
| Vendor consignment → own stock | `411` (`SOBKZ = K` on the issuing item) | When own stock runs low |

Posting conventions (material documents):
- `311`, `313`, `321`, `411` have **two items**: the issuing side (`SHKZG = H`) and the receiving side (`SHKZG = S`, `XAUTO = 'X'`). Counting both items double counts the transfer.
- `313` puts stock into transfer at the receiving location; `315` is **one** receipt item that moves it from transfer to unrestricted. Adding the `313` receiving item and the `315` item counts the same goods twice.
- Round Rock: pulls from the overflow hub `2001` into the main warehouse and moves out of it are `311` posted by `WF-BATCH` (`MKPF.USNAM`); an **emergency** pull on the demand day is posted early in the morning by `RFC-EWM`. Longgang: the Dongguan overflow hub `3000` is replenished with two-step `313`/`315` transfers posted by `WF-BATCH`; emergency transfers are posted by `RFC-EWM` with `313` and `315` on the same day.

Rules for analysis:
- Transfers between storage locations are internal movements. **Exclude them from issues to manufacturing, throughput, and demand**; the issues are the `311` goods issues to `2100` net of `312` (or delivery status, see `metric.lines_shipped`).
- Demand for replenishment planning = issues from the main warehouse to the manufacturing department (`311` staging to `2100`, see `metric.projected_stock`); transfers between the main warehouse and the overflow hub are not demand.
- When a question explicitly includes the replenishment of an overflow hub, answer with both parts and their sum, in quantities: the issues to manufacturing as above, plus the transfers from the main warehouse to that hub counted once on the issuing side (Longgang: the `313` issuing items at `1000`, never the `313` receiving item or the `315` placement; Round Rock: the `311` issuing items from `1000` to `2001`). Say that the transfers are internal movements, not demand by definition.
