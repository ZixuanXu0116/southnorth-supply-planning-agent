---
id: concept.system_users
title: System users
type: concept
aliases: [WF-BATCH, AS-INTERFACE, RFC-EWM, FS-INTERFACE, MIGRATION, batch user, interface user, automated confirmation]
status: draft
last_reviewed: 2026-09-28
---

# System users

| User | Meaning |
|---|---|
| `WF-BATCH` | Background jobs (e.g. wave release, pick and putaway task creation) |
| `AS-INTERFACE` | AutoStore interface; confirms picks at AutoStore ports and putaways into the grid (decanting) at Longgang, storage type `AS01` |
| `RFC-EWM` | Postings triggered by EWM in ECC (e.g. goods issue, goods receipt, emergency spoke replenishment), EWM documents created from ECC or supplier messages (outbound delivery orders, inbound deliveries), and some automated confirmations |
| `FS-INTERFACE` | Field-service consumption posted from the service system (`201` at Round Rock satellites) |
| `MIGRATION` | One-time data load (opening balances, `561`) |

System users are not people. Exclude them from labor productivity and from any per-operator analysis. They do not appear in the workforce roster.
