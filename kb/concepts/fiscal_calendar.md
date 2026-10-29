---
id: concept.fiscal_calendar
title: Fiscal calendar
type: concept
aliases: [fiscal year, fiscal quarter, fiscal period, 4-4-5, 52/53 week, T009B, FY]
status: draft
last_reviewed: 2026-09-24
---

# Fiscal calendar

- Company-wide fiscal year variant `Z5`: a **52/53-week fiscal year ending on the last Sunday of October**. FY2026 runs 2025-10-27 to 2026-10-25.
- 12 periods per year in a **4-4-5** week pattern per quarter; in a 53-week year the extra week is added to period 12.
- Fiscal quarters: Q1 = periods 1–3, Q2 = 4–6, Q3 = 7–9, Q4 = 10–12.
- "Last quarter", "this year", and "YTD" mean **fiscal** periods unless the user says calendar.
- `T009B` lookup rule: take the rows whose `BDATJ` equals the date's calendar year and pick the first row whose month/day (`BUMON`, `BUTAG`) is on or after the date; `POPER` is the period and fiscal year = calendar year + `RELJR` (`+1` or `0`). Periods that span New Year have an extra row ending Dec 31.
- `MKPF.MJAHR` is the fiscal year of the posting date.
- The last weeks of each fiscal quarter show a goods-issue push (expected, not an anomaly).
