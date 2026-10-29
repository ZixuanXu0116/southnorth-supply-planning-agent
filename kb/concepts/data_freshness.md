---
id: concept.data_freshness
title: Data freshness
type: concept
aliases: [latest data, yesterday, today, incomplete day, replication, extraction, as of]
status: draft
last_reviewed: 2026-09-24
---

# Data freshness

- **ECC** tables are extracted **nightly** at 00:00 America/Chicago. They contain everything entered in ECC before that time.
- **EWM** tables are replicated **hourly** and are therefore fresher: EWM can contain delivery orders and tasks for deliveries that do not yet exist in the ECC extract, and pick confirmations for deliveries whose goods issue is not yet in ECC.
- The most recent day is **incomplete** in both systems; exclude it from trends or label it as partial.
- Status fields in ECC (`VBUK`, `VBUP`, `LIKP.WADAT_IST`) reflect the state at ECC extraction time.
- Always state the data cut-off used in an answer.
