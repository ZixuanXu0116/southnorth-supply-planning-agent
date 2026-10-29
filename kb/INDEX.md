# Knowledge Base Index

Read this index first, then open only the documents you need. Every document starts with YAML front matter (`id`, `title`, `type`, `aliases`, `status`, `last_reviewed`). Search by alias with `kb_search`.

Business model (decision record 003): each site has a **main warehouse** that issues to the company's manufacturing department and **overflow hubs** that hold what the main warehouse does not need right away; there are no external customers. Outbound deliveries are the picking lists of production orders (CO27) delivered to the work centers of the manufacturing department (storage location `2100`, goods issue `311`). The planning workflow covers Round Rock's main warehouse `1000` and overflow hub `2001`. Older pages may say hub for the main warehouse and spoke or satellite for an overflow hub.

Status values: `draft` (not yet validated against evaluation cases), `reviewed`, `deprecated`.

All tables live in Unity Catalog catalog `snm`; SQL examples use `schema.table` names and assume `USE CATALOG snm` and the UTC session time zone.

## Sites
| id | title | aliases |
|---|---|---|
| site.round_rock | [Round Rock main warehouse (US10 / RR01)](sites/round_rock.md) | RR, Round Rock, US10, RR01, U100, Texas site, main warehouse, overflow hub |
| site.longgang | [Longgang main warehouse (CN10 / SZ01), outside the planning workflow](sites/longgang.md) | SZ, Longgang, Shenzhen, SZ01, C100, AutoStore site |
| site.dongguan | [Dongguan overflow hub (CN10 / DG01), outside the planning workflow](sites/dongguan.md) | DG, Dongguan, DG01, C300, spoke, overflow hub |

## Policies (mandatory)
| id | title | aliases |
|---|---|---|
| policy.site_isolation | [Site isolation](policies/site_isolation.md) | cross-site comparison, all sites, global |
| policy.people_analytics | [People analytics](policies/people_analytics.md) | operator performance, productivity by person, picker ranking |
| policy.export_control | [Export control](policies/export_control.md) | export, ECCN, license, embargo, can we ship abroad, HTS |

## Concepts
| id | title | aliases |
|---|---|---|
| concept.time_zones | [Time zones and timestamps](concepts/time_zones.md) | UTC, local time, DEC15, CPUDT, warehouse time |
| concept.fiscal_calendar | [Fiscal calendar](concepts/fiscal_calendar.md) | fiscal year, fiscal quarter, fiscal period, 4-4-5, 52/53 week, T009B |
| concept.data_freshness | [Data freshness](concepts/data_freshness.md) | latest data, yesterday, incomplete day, replication, extraction |
| concept.units_of_measure | [Units of measure](concepts/units_of_measure.md) | base unit, sales unit, BOX, EA, conversion, UMREZ |
| concept.material_numbers | [Material and product numbers](concepts/material_numbers.md) | MATNR, MATID, product GUID, leading zeros |
| concept.goods_movements | [Goods movements and reversals](concepts/goods_movements.md) | MSEG, movement type, 311, 312, SHKZG, reversal |
| concept.system_users | [System users](concepts/system_users.md) | WF-BATCH, AS-INTERFACE, RFC-EWM, FS-INTERFACE, batch user |
| concept.stock_types | [Stock types and where stock lives](concepts/stock_types.md) | stock, available, quality inspection, consignment, bonded, in transfer, snapshot |
| concept.internal_transfers | [Internal transfers](concepts/internal_transfers.md) | 311, 313, 315, main warehouse to overflow hub, pull, move to overflow, double counting |
| concept.production_supply | [Production supply and manufacturing demand](concepts/production_supply.md) | production order, AFKO, RESB, reservation, planned order, PLAF, staging, 261, 2100, backlog, manufacturing demand |
| glossary | [Glossary](glossary.md) | SAP terms, abbreviations |

## Guides
| id | title | aliases |
|---|---|---|
| guide.outbound_flow | [Outbound flow and join paths](guides/outbound_flow.md) | production order to delivery, picking list, delivery to task, delivery to goods issue |
| guide.replenishment_planning | [Replenishment planning and purchasing data](guides/replenishment_planning.md) | planning, expedite, supplier terms, surcharge, tail supplier, purchasing history, cost model |
| guide.procurement_and_stock | [Procurement and stock join paths](guides/procurement_and_stock.md) | purchase order, goods receipt, vendor, EKKO, MARD, stock history |
| guide.inbound_flow | [Inbound flow, putaway, and join paths](guides/inbound_flow.md) | inbound delivery, putaway, PACI, receiving, decanting |
| guide.autostore | [AutoStore at Longgang](guides/autostore.md) | AutoStore, port, bin presentation, robot, system stop |
| guide.rfid_tracking | [RFID tote tracking at Round Rock](guides/rfid_tracking.md) | RFID, LTS, tote, reader, dock door, physical pick time |

## Metrics (draft)
| id | title | aliases |
|---|---|---|
| metric.on_time_pgi_rate | [On-time PGI rate](metrics/on_time_pgi_rate.md) | on-time goods issue, ship on time, OT-PGI |
| metric.otd_pod | [On-time delivery (POD-based)](metrics/otd_pod.md) | OTD, on-time delivery, delivered on time |
| metric.lines_shipped | [Lines shipped](metrics/lines_shipped.md) | shipped lines, outbound lines, delivery lines shipped |
| metric.pick_cycle_time | [Pick cycle time](metrics/pick_cycle_time.md) | pick time, task cycle time, picking duration |
| metric.wave_to_pgi_lead_time | [Wave-to-PGI lead time](metrics/wave_to_pgi_lead_time.md) | release to ship, wave lead time |
| metric.available_stock | [Available stock](metrics/available_stock.md) | available inventory, available to ship, on-hand available |
| metric.days_of_supply | [Days of supply](metrics/days_of_supply.md) | DOS, days of cover, stock coverage |
| metric.projected_stock | [Projected stock and replenishment cases](metrics/projected_stock.md) | supply projection, shortage date, days short, replenishment case, will we run out, coverage |
| metric.supplier_on_time_delivery | [Supplier on-time delivery](metrics/supplier_on_time_delivery.md) | supplier OTD, vendor performance, late suppliers |
| metric.dock_to_stock_time | [Dock-to-stock time](metrics/dock_to_stock_time.md) | receipt to putaway, putaway lead time, time to shelf |
| metric.autostore_availability | [AutoStore availability](metrics/autostore_availability.md) | AutoStore uptime, downtime, port availability |
