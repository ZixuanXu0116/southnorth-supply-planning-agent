---
id: guide.autostore
title: AutoStore at Longgang (controller logs and EWM tasks)
type: guide
aliases: [AutoStore, cube storage, grid, port, bin presentation, robot, AutoStore stop, AutoStore downtime, decanting, AS01]
status: draft
last_reviewed: 2026-09-29
---

# AutoStore at Longgang

The AutoStore grid is EWM storage type `AS01` in warehouse `SZ01`. Robots bring grid bins to 12 ports (`AS-PORT-01` to `AS-PORT-12`), where operators pick from the bin or decant received goods into it. EWM records the warehouse tasks; the AutoStore controller records what the machine did.

| Source | Table | Clock |
|---|---|---|
| EWM tasks at the grid | `raw_sap_ewm.scwm_ordim_c` with `VLTYP = 'AS01'` (picks) or `NLTYP = 'AS01'` (decanting putaways) | UTC (`*_AT`), local (`*_AT_WH`) |
| Bin presentations | `raw_ext.autostore_bin_presentation` | **Longgang local time**, no offset |
| Port status history | `raw_ext.autostore_port_status` | Longgang local time |
| System stops | `raw_ext.autostore_system_status` | Longgang local time |

Joining controller logs to EWM tasks:
- Port: `presentation.port_id` = task `PRSRC`. Bin: `bin_id` = `VLPLA` (picks) or `NLPLA` (decanting).
- Time: a task starts inside its presentation window (`presented_at` ≤ task start ≤ `closed_at`). `STARTED_AT` is UTC: convert it with `from_utc_timestamp(ts, 'Asia/Shanghai')` before comparing; only `CREATED_AT_WH` and `CONFIRMED_AT_WH` are already local.
- The controller does not know EWM task numbers.

Pitfalls:
- **One presentation can serve several tasks** (`task_count`); counting presentations is not counting picks.
- **Port status is a complete timeline**: each port alternates `OPEN` and `CLOSED` intervals; the current interval has `valid_to = NULL`. Closures are planned night maintenance (`MAINTENANCE`, a few hours about every eight weeks per port) or faults (`FAULT`).
- **System stops** (`STOPPED`) halt every port; outside the logged intervals the system is running.
- Picks at the grid are confirmed by `AS-INTERFACE` (not a person); see `concept.system_users`.
