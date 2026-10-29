# Data Model — SouthNorth Solutions Logistics Lakehouse

Status: **Draft v0.1** (layering section pending final review)
Scope: synthetic data only. Structures follow publicly documented SAP standard tables; all values, codes, names, and volumes are fictional.

---

## 1. Business context

**SouthNorth Solutions (SNM)** is a fictional manufacturer of semiconductor equipment and spare parts. This project models its **Global Logistics** department, whose operational data is replicated from SAP into a Databricks lakehouse.

**Business model** (decision record [`003`](decisions/003-manufacturing-supply-model.md), 2026-10-16): each site has a **main warehouse** that issues parts to the company's own manufacturing department (demand = the SAP CO27 picking list of component requirements, replicated every 30 minutes) and **overflow hubs**, cheaper storage with no demand of their own. The main warehouse pulls from its overflow hub when it falls below its minimum, moves surplus out when it stays above its maximum, and purchasing orders or expedites from suppliers when the two together are not enough; supplier deliveries arrive at either. There are no external customers. The planning workflow covers Round Rock's main warehouse `1000` and its overflow hub `2001` only. Generator 0.8 generates the deliveries from the CO27 picking list (no sales orders, no export control); the main-warehouse-to-hub push replenishment and the satellite consumption of the generated data are legacy flows until the inventory step of generator 0.8.

### 1.1 Sites and organizational structure

| Site | Company code | Plant | Storage locations | EWM warehouse | Time zone | Role |
|---|---|---|---|---|---|---|
| Round Rock, TX (US) | 1000 | US10 | `1000` RR main warehouse (EWM-managed), `2001` Pflugerville overflow hub (planning scope), `2002` Georgetown and `2003` Hutto overflow hubs (outside the scope), `2100` RR assembly production supply area (IM-managed, no bins) | `RR01` | America/Chicago | Main warehouse with overflow hubs; low volume, high value, serialized parts; **LTS RFID** scanning. The planning workflow's site |
| Shenzhen Longgang (CN) | 2000 | CN10 | `1000` Longgang main warehouse (EWM-managed, **AutoStore**), `1900` bonded area, `2100` Longgang assembly production supply area (IM-managed) | `SZ01` | Asia/Shanghai | Main warehouse; high volume, small parts. Outside the planning workflow for now |
| Dongguan (CN) | 2000 | CN10 | `3000` Dongguan overflow hub (EWM-managed) | `DG01` | Asia/Shanghai | Overflow hub of Longgang. Outside the planning workflow for now |

Key rules that the agent must respect:

- **Sites are not interchangeable.** Filters, time zones, calendars (holidays), and volumes differ. A conclusion for one site must never be applied to another without an explicit per-site breakdown.
- **Main warehouse and overflow hubs.** In the business model the main warehouse pulls from its overflow hub and moves surplus out (min–max on the main warehouse: `MARC-EISBE` minimum, `MARC-MABST` maximum; engine 1.0, `PROPOSAL.md` §4.2). In the generated data (legacy, until generator 0.8) replenishment still runs the other way: the hubs use storage-location MRP (`MARD-DISKZ = '2'`, reorder point `LMINB`, replenishment quantity `LBSTF`) and are filled from the main warehouse. Main-warehouse fixed bins use EWM min/max (`/SAPAPO/MATLWHST`, `/SCWM/BINMAT`).
- Transfers: Round Rock hub → satellites use one-step transfers (movement type `311`); Longgang → Dongguan uses two-step transfers with stock in transit (`313`/`315`).
- **Manufacturing** (generator 0.7, `PROPOSAL.md` D11) is the assembly building of each hub plant, modeled as the production supply storage location `2100`: finished products (`FERT`) are built from spare-part components with a fixed bill of materials; production orders (`AFKO`/`AFPO`) reserve their components (`RESB`, requirement type `SB`) at `2100` for the order start; the hub stages them two working days before the start (`311` from `1000` to `2100`, as far as hub stock allows) and consumes them against the order (`261`) on the first working day on which every component is staged. Overdue reservations that are not fully staged are the manufacturing backlog. Planned orders (`PLAF`, weeks 2–13 ahead) with dependent requirements (`RESB`, type `AR`) are the MRP forecast.
- **Fiscal calendar:** 52/53-week fiscal year ending on the last Sunday of October (company-wide), modeled via `T009`/`T009B` plus a derived calendar dimension.

### 1.2 Core processes covered

- **Inbound:** inbound delivery → goods receipt (GR) → putaway warehouse task (destination determined by the putaway control indicator, PACI = `PUT_STRA`) → confirmation.
- **Outbound:** production order (CO27 picking list) → outbound delivery to the production supply area (type `ZPSA`) → EWM outbound delivery order → wave release on the staging day → pick → pack → stage → shuttle to the manufacturing building → goods issue (two-item `311` per line; `312` reversal) → receipt confirmation by the work center (POD).
- **Replenishment:** planned / order-related / automatic / direct replenishment tasks; hub-to-spoke stock transfers.
- **Production supply:** staging to production supply areas (PSA).
- **Inventory:** stock by plant / storage location / bin, daily snapshots, physical inventory differences.
- **Automation:** AutoStore bin presentations, port status, system availability (Longgang only).
- **Workforce:** who executed each warehouse task, pick/putaway durations, productivity (pseudonymized).
- **No external customers:** deliveries go to the company's manufacturing department only; there is no export flow (tariff codes `MARC-STAWN` remain master data).

---

## 2. Layered architecture

One Unity Catalog catalog (`snm`, or the Free Edition default catalog with the same schema names if catalog creation is unavailable). Each layer has a single purpose, explicit ownership, and explicit agent visibility.

```
                sources (synthetic generator)
                            │
   ┌────────────────────────▼─────────────────────────┐
   │ L0 RAW      raw_sap_ecc · raw_sap_ewm · raw_ext   │  source-faithful, SAP technical names,
   │             (+ raw_landing volume for files)      │  CDC metadata, intentionally dirty
   └────────────────────────┬─────────────────────────┘
   ┌────────────────────────▼─────────────────────────┐
   │ L1 STAGING  stg (views)                           │  typing, time normalization, dedup,
   │                                                   │  code decoding — 1:1 with raw
   └────────────────────────┬─────────────────────────┘
   ┌────────────────────────▼─────────────────────────┐
   │ L2 CURATED  curated                               │  conformed dims/facts, English names,
   │             restricted                            │  flags (not pre-filtered), site-aware
   └────────────────────────┬─────────────────────────┘
   ┌────────────────────────▼─────────────────────────┐
   │ L3 MARTS    marts                                 │  declared-grain aggregates
   └────────────────────────┬─────────────────────────┘
   ┌────────────────────────▼─────────────────────────┐
   │ L4 SEMANTIC semantic (UC metric views)            │  governed KPIs + semantic metadata
   └──────────────────────────────────────────────────┘
   side schemas:  ops (freshness, DQ results) · eval (hidden) · agent_audit
```

### 2.1 Layer contracts

| Layer | Schema(s) | Purpose | Naming | Materialization | Agent visibility (default role) |
|---|---|---|---|---|---|
| L0 Raw | `raw_sap_ecc`, `raw_sap_ewm`, `raw_ext` | Replicas of source tables, SAP technical table/field names, CDC metadata (`_ingested_at`, `_batch_id`, `_op`), realistic defects (duplicates, late arrivals) | SAP names in snake case: `mseg`, `scwm_ordim_c`, `sapapo_matlwh` | Delta tables | Read-only, for exploration of long-tail questions |
| L1 Staging | `stg` | Type casting (EWM `DEC15` timestamps → UTC `TIMESTAMP`; ECC `DATS`/`TIMS` → site-local and UTC), CDC dedup (latest image), code decoding | `stg_<source>__<table>` | Views (no storage cost) | None (pipeline-internal) |
| L2 Curated | `curated` | Conformed business entities and events in English; both UTC and site-local timestamps; quantities in base UoM; reversal-paired movements; **flags instead of filters** (`is_intercompany_transfer`, `is_system_user`, `is_emergency_pick`, `is_cancelled`) | `dim_*`, `fct_*` | Views over raw (`data/pipelines/curated/*.sql`); the raw tables' row filters and masks apply to the querying principal through them | Read (primary for ad hoc analysis) once the layer is complete; until then internal (sources of the metric views) |
| L2 Restricted | `restricted` | Operator-identifiable labor facts | `fct_*`, `dim_*` | Delta tables | Role-dependent only |
| L3 Marts | `marts` | Aggregates at declared grains for common questions and metric-view performance | `agg_<subject>_<grain>` | Delta tables | Read |
| L4 Semantic | `semantic` | UC metric views with semantic metadata (display names, formats, synonyms, comments) | `mv_<subject>` | Metric views | Read (primary for KPI questions via `query_metric`) |
| Ops | `ops` | `data_load_log` (freshness), `dq_results`, `pipeline_runs` | — | Delta tables | Read (freshness / known-issue tool) |
| Eval | `eval` | Gold answers, anomaly ground truth, generator manifest | — | Delta tables | **No access** (enforced by grants) |
| Audit | `agent_audit` | Query audit log written by the tool server's service identity | — | Delta tables | No SQL access for the agent |

Design principles:

1. **Flags, not filters, in curated.** Business exclusions (test/system users, intercompany transfers, cancelled tasks) are flagged, not removed, so that definition adherence remains observable and testable.
2. **Two clocks everywhere.** Every event carries `*_ts_utc` and `*_ts_local` plus `site_id`; "daily" means the site-local day unless a definition states otherwise.
3. **Single source of truth in the repository.** Table contracts (`data_contracts/*.yaml`) generate DDL, column comments, tags, grants, and data-quality tests. Metric views are deployed from `semantic/metric_views/*.yaml`. Agent-facing knowledge lives in `kb/`. A CI check detects drift between contracts, UC comments, and KB table docs.
4. **Traps survive curation by design.** Some pitfalls are resolved in curated (typing, dedup), others are only flagged, and a few remain only in raw — see §5.

### 2.2 Unity Catalog semantics usage

| Capability | Status (Sep 2026) | Usage in this project | Readable by the agent |
|---|---|---|---|
| Table/column comments | GA | Generated from data contracts for every table and column | Yes — `information_schema` via `describe_table` |
| Governed tags | GA | `domain`, `subdomain`, `layer`, `site_scope`, `sensitivity`, `grain` | Yes — `information_schema.*_tags` |
| Certification / deprecation (`system.certification_status`) | GA | Certify curated/semantic assets; deprecate a legacy mart with an outdated definition (trap) | Yes — agent prefers certified and avoids deprecated assets |
| Metric views + semantic metadata | GA | 8–12 governed KPIs (§4) | Yes — `query_metric` tool and YAML |
| Metric view materialization | Preview | Only if query latency requires it | Transparent |
| Domains / subdomains | Public Preview (account-level preview toggle) | Inbound, Outbound, Inventory, Warehouse Operations, Automation, Workforce, Trade Compliance, Master Data | Indirectly via domain tags |
| Pages | Beta, UI-only (no documented API) | Business concept pages mirrored from `kb/` (e.g., *Dock-to-Stock Time*, *Hub-and-Spoke Replenishment*, *PGI*) | No — `kb/` remains the agent's source; Pages serve humans and Genie One |
| Lineage | GA (automatic) | Visualize raw → curated → semantic flows | Optional |

Verified on Free Edition (2026-09-25): comments, tags, the certification system tag, row filters, column masks, metric views, and volumes all work. Domains and Pages still need a UI check. Masks and filters must use workspace-level group checks (`is_member(...)`): Free Edition has no account-level groups, so `is_account_group_member('admins')` returns false even for workspace admins.

### 2.3 Roles and access

Roles are enforced twice: by the tool server's SQL guard (first line, with error codes the agent can act on) and by Unity Catalog (second line, which holds even if the guard misses a table reference). `snm-data govern` deploys the database side from the contracts and `snm_data/governance.py`:

- **Identities:** per provisioned role, a service principal `snm-agent-<role>` (OAuth machine-to-machine) in a workspace group `snm_<role>`, with `CAN_USE` on the SQL warehouse. The tool server receives only the active role's OAuth client and refuses workspace-admin credentials and credentials of another role. `executive` gets an identity once marts or metric views exist.
- **Grants:** `SELECT` plus `USE CATALOG`/`USE SCHEMA` per contract `access` list, granted to the service principals (Unity Catalog accepts only account-level groups, which Free Edition lacks). Nothing on `eval`; no write privileges.
- **Row filters (site isolation):** on the 25 multi-site tables that declare a `site_key` (plant `werks`/`bwkey`, warehouse `lgnum`, shipping point `vstel`, organization `bukrs`/`vkorg`/`ekorg`). Site managers see their own plant's rows; all-site roles and workspace admins see every row. Single-site tables (AutoStore, RFID, trade compliance) are scoped by their grants. Fifteen multi-site tables have no site column and stay unfiltered: master and configuration data (`mara`, `makt`, `marm`, `kna1`, `lfa1`, `t009`, `t009b`, `sapapo_matkey`) and document headers, items, or status rows that are joined to filtered tables for site attributes (`mkpf`, `eket`, `vbuk`, `vbup`, `scdl_db_proci_i`, `scdl_db_proci_o`, `scdl_db_refdoc`). Residual risk: a site manager can count those tables' rows across sites.
- **Column masks:** `restricted` and `pii` columns (`scwm_ordim_c.confirmed_by`, `workforce_roster.operator_id`, `workforce_roster.sap_uname`, `purchasing_interaction.handled_by`) read as NULL unless the role has people-level access; `commercial` columns (prices, surcharges, freight, cost parameters, case values and lever costs) read as NULL unless the role has cost access (`commercial_amount_mask` for `DECIMAL(15,2)`, `commercial_text_mask` for `STRING`). Metric views may not use commercial columns until a role with cost access is provisioned (checked by `governance.validate`).
- Row filters hide rows silently, so the metadata tools tell site managers their row scope (`list_tables.sites`, `describe_table.row_scope`), and bank v7 checks that other-site questions are declined rather than answered with zero.

| Role | Sites | Operator-level data | Trade-compliance details | Raw layer |
|---|---|---|---|---|
| `ops_analyst_global` | All | Aggregated (team/shift) only | Classification counts only | Read |
| `site_manager_rr` | US10 only | Individual (own site) | Classification counts only | Read |
| `site_manager_cn` | CN10 only | Individual (own site) | Classification counts only | Read |
| `trade_compliance` | All | None | Full | None |
| `executive` | All | None | Counts only | None (semantic + marts only) |
| `purchasing` (v2, not yet provisioned) | All | None | Counts only | Read, plus cost access (commercial columns) |

---

## 3. Source tables (L0)

Formats follow SAP conventions: EWM IDs are RAW16 GUIDs (`MATID`, `DOCID`), EWM timestamps are `DEC15` (`YYYYMMDDhhmmss`, UTC), ECC dates/times are local `DATS`/`TIMS`. ECC material numbers are CHAR18.

### 3.1 `raw_sap_ewm`

| Table | Purpose | Key fields |
|---|---|---|
| `scwm_ordim_o`, `scwm_ordim_c` | Open / confirmed warehouse tasks | `LGNUM`, `TANUM`, `TAPOS`, `TOSTAT`, `PROCTY`, `TRART`, `MATID`, `VSOLM`/`MEINS`, `VSOLA`/`ALTME`, `VLTYP`/`VLPLA`, `NLTYP`/`NLPLA`, `WHO`, `WAVE`, `RDOCID`, `CREATED_AT`, `STARTED_AT`, `CONFIRMED_AT`, `CONFIRMED_AT_WH`, `CONFIRMED_BY`, `PRSRC` |
| `scwm_who` | Warehouse orders | `WHO`, `STATUS`, `WCR`, `WAVE`, `QUEUE`, `RSRC`, `CREATED_AT`, `CONFIRMED_AT`, `LSD` |
| `scwm_wavehdr`, `scwm_waveitm` | Waves | `WAVE`, `WAVE_TYPE`, `STATUS`, `RLS_DT`, `RELEASED_AT`, `RELEASED_AT_WH`, `CUTOFF_DT`, `PICK_COMP_DT`, `LOAD_COMP_DT`; items `RDOCID`, `QUAN` |
| `scwm_lagp` | Storage bins | `LGPLA`, `LGTYP`, `LGBER`, `LPTYP`, `AISLE`, `KZLER`, `SKZUE`, `SKZUA`, `MAX_WEIGHT` |
| `scwm_t301`, `scwm_t302`, `scwm_t303` | Storage types, sections, bin types | `LGTYP`, `LGBER`, `LPTYP` |
| `scwm_aqua` | Available stock (quants) | `GUID_STOCK`, `GUID_PARENT`, `LGTYP`, `LGPLA`, `MATID`, `CAT`, `QUAN`, `UNIT`, `OWNER`, `WDATU` |
| `scwm_huhdr` | Handling units | `GUID_HU`, `HUIDENT`, `LETYP`, `G_WEIGHT`, `G_VOLUME`, `CREATED_AT` |
| `scdl_db_proch_i` / `scdl_db_proci_i` | Inbound delivery header / item | `DOCID`, `DOCNO`, `DOCTYPE`, `CRETST`, `DELDATE`; items `ITEMID`, `PRODUCTNO`, `QTY`, `UOM`, `STOCK_CATEGORY` |
| `scdl_db_proch_o` / `scdl_db_proci_o` | Outbound delivery order header / item | same pattern |
| `scdl_db_status` | Document status | `DOCID`, `ITEMID`, `STATUS_TYPE` (`DGR`, `DPI`, `DGI`, `DLO`, `DWM`), `STATUS_VALUE` |
| `scdl_db_date` | Document dates | `DOCID`, `TSTTYPE`, `TSTFR`, `TSTTO`, `TZONE` |
| `scdl_db_refdoc` | Reference to ECC delivery / PO | `REFDOCCAT`, `REFDOCNO`, `REFITEMNO` |
| `sapapo_matlwh` | Warehouse product master | `MATID`, `PUT_STRA` (PACI), `REM_STRA`, `PTDETIND`, `CCIND` |
| `sapapo_matlwhst` | Storage-type-level min/max | `MATID`, `LGTYP`, `MINQTY`, `MAXQTY`, `REPQTY` |
| `scwm_binmat` | Fixed bins with min/max | `LGPLA`, `MATID`, `MINQTY`, `MAXQTY` |
| `scwm_rsrc` | Resources | `RSRC`, `RSRC_TYPE`, `RSRC_GRP`, `UNAME` |
| `scwm_tpsa` | Production supply areas | `PSA`, `TRIGGER_GI` |
| `lm_executed_workload` | Simplified labor-management extract (not a standard table name) | `operator_uname`, `who`, `start_ts`, `end_ts`, `planned_duration_s` |

### 3.2 `raw_sap_ecc`

| Table | Purpose | Key fields |
|---|---|---|
| `mara`, `makt`, `marm`, `mbew` | Material master, descriptions, UoM conversions, valuation | `MATNR`, `MTART`, `MATKL`, `MEINS`, `BRGEW`, `NTGEW`, `GEWEI`, `VOLUM`; `MEINH`, `UMREZ`, `UMREN`; `STPRS`, `VERPR` |
| `marc` | Plant data | `WERKS`, `DISMM`, `MINBE`, `EISBE`, `STAWN` (tariff), `SERNP` (serial profile), `HERKL` |
| `mard` | Storage-location stock and spoke MRP | `LGORT`, `LABST`, `INSME`, `SPEME`, `UMLME`, `DISKZ`, `LMINB`, `LBSTF` |
| `mkpf`, `mseg` | Material documents | `MBLNR`, `MJAHR`, `ZEILE`, `BWART`, `SHKZG`, `MENGE`/`MEINS`, `ERFMG`/`ERFME`, `WERKS`, `LGORT`, `SOBKZ`, `BUDAT`, `CPUDT`, `CPUTM`, `SMBLN`, `UMLGO`, `AUFNR` (production order on `261` consumption) |
| `afko`, `afpo` | Production orders: header and item | `AUFNR`, `RSNUM`, `PLNBEZ`, `GAMNG`, `FTRMI` (release), `GSTRP`/`GLTRP` (scheduled start/finish), `GSTRI`/`GETRI` (actual); `POSNR`, `MATNR`, `PSMNG`, `WEMNG`, `PWERK` |
| `resb` | Reservations: components of production orders (`BDART = 'SB'`) and dependent requirements of planned orders (`'AR'`) | `RSNUM`, `RSPOS`, `BDART`, `MATNR`, `WERKS`, `LGORT` (`2100`), `BDTER` (requirement date), `BDMNG` (required), `ENMNG` (withdrawn), `XLOEK` (deleted), `AUFNR`, `PLNUM` |
| `plaf` | Planned orders (MRP output at extraction) | `PLNUM`, `MATNR`, `PLWRK`, `GSMNG`, `PSTTR`/`PEDTR` (start/finish), `PAART`, `RSNUM` |
| `likp`, `lips` | Deliveries to the production supply area (`LFART = 'ZPSA'`; `KUNNR` = work center, `VGBEL` = production order, `VGPOS` = reservation line) | `VBELN`, `LFART`, `KUNNR`, `WADAT`, `WADAT_IST`, `KODAT`, `LFDAT`, `PODAT`, `POTIM`; items `POSNR`, `MATNR`, `LFIMG`, `VRKME`, `LGMNG`, `PODREL` |
| `vbuk`, `vbup` | Delivery / order status (ECC) | `GBSTK`, `KOSTK`, `WBSTK`, `PDSTK`; `KOSTA`, `WBSTA`, `PDSTA` |
| `mska`, `mkol` | Special stock: sales-order (E), vendor consignment (K) | `SOBKZ`, `KALAB`, `SLABS` |
| `crhd`, `t001w`, `t001l` | Work centers, plants, storage locations | `ARBPL`, `WERKS`, `LGORT` |
| `objk`, `ser01`, `ser03`, `equi` | Serial numbers | `OBKNR`, `SERNR`, `MATNR`, `LIEF_NR`, `MBLNR` |
| `t009`, `t009b` | Fiscal year variant and period table | `PERIV`, `BDATJ`, `BUMON`, `BUTAG`, `POPER`, `RELJR` |

### 3.3 `raw_ext`

| Table | Source | Purpose |
|---|---|---|
| `autostore_bin_presentation` | AutoStore controller (Longgang) | Port, bin, robot, presented/closed timestamps, picks per presentation |
| `autostore_port_status` | AutoStore controller | Port open/closed intervals |
| `autostore_system_status` | AutoStore controller | STOPPED intervals (availability) |
| `lts_rfid_scan` | LTS RFID (Round Rock) | Tag / serial, reader location, scan timestamp; fallback timestamps for emergency picks |
| `mrp_pegging_snapshot` | Nightly MRP extract (pegging is computed at runtime in SAP) | Requirement ↔ receipt pegging per material/site |
| `inventory_snapshot_daily` | Nightly extract at site-local midnight | Stock by plant/storage location/bin/stock category |
| `shipping_checklist` | Shipping inspection app | Checklist items per delivery / HU, free-text remarks |
| `fg_pack_ship` | Finished-goods issue records | Packing and shipment records per HU |
| `workforce_roster` | HR extract (pseudonymized) | Operator ID, site, team, shift, active dates |
| `supplier_condition` | Purchasing condition sheet (v0.6.1) | Per vendor: strategic flag, minimum expedite days, surcharge tiers by days gained (commercial), expedited freight (commercial), portal onboarding |
| `cost_parameter` | Planning cost model (v0.6.1) | Line-down cost per criticality class, holding rate, transfer and order costs (commercial); synthetic parameters |
| `purchasing_interaction` | Purchasing team records (v0.6.1) | Exchanges with vendors per purchase order: expedite requests on late orders, emergency buys, confirmation inquiries; rounds, days, days gained, surcharge paid (commercial), pseudonymized buyer (pii) |

---

## 4. Semantic layer (L4) — candidate metric views

| Metric view | Example measures | Key dimensions |
|---|---|---|
| `mv_inbound_flow` | Dock-to-stock time (GR → putaway confirmation), GR lines, PACI compliance rate | site, storage type, material group, fiscal period |
| `mv_outbound_flow` | Wave-release-to-PGI lead time, on-time PGI rate (`WADAT_IST` vs `WADAT`), on-time delivery (POD-based), lines shipped (excluding intercompany transfers) | site, work center, fiscal week |
| `mv_warehouse_tasks` | Task volume, task cycle time, exception rate | site, process type, storage type, shift, operator (site managers only) |
| `mv_labor_productivity` | Tasks / lines per labor hour (excluding system users) | site, team, shift (individual level only in `restricted`) |
| `mv_autostore` | Bin presentations per hour, port availability, picks per presentation | port, hour, shift |
| `mv_inventory_health` | On-hand (own stock, base UoM), days of supply, min/max breach rate, emergency pick rate | site, storage location, material group |
| `mv_hub_spoke_replenishment` | Transfer volume, hub → spoke lead time, spoke stock-out rate | hub, spoke, material group |

Each metric view carries display names, formats, synonyms, and comments. Joins declare cardinality explicitly (`one_to_many` where fan-out is possible).

**Implemented (2026-10-08/09), eight metric views over seven curated views, all `semantic/metric_views/*.yaml` (the Unity Catalog YAML plus the roles that may query it and the knowledge-base documents it implements):**

| Metric view | Source (curated view) | Measures (date basis) | Roles |
|---|---|---|---|
| `mv_outbound_due` | `fct_outbound_delivery` | on-time PGI rate, deliveries due and on time (planned goods issue date; deliveries due on or after the ECC cut-off day left out) | analyst, site managers |
| `mv_outbound_shipped` | `fct_outbound_delivery` | deliveries and lines shipped, lines per delivery, median and p90 wave-to-PGI minutes (actual goods issue date) | analyst, site managers |
| `mv_outbound_pod` | `fct_outbound_delivery` | POD-relevant deliveries, confirmed, pending, on-time delivery rate (planned delivery date) | analyst, site managers |
| `mv_warehouse_tasks` | `fct_warehouse_task` | tasks, confirmed, cancelled, median and p90 cycle and execution minutes; dimensions task type, shift, AutoStore, system user, storage types, material, operator (restricted: people-level roles only) (site-local confirmation day) | analyst, site managers |
| `mv_inbound_putaway` | `fct_inbound_putaway` | items received, put away, open; received quantity; median and p90 dock-to-stock minutes (site-local receipt day) | analyst, site managers |
| `mv_autostore_availability` | `fct_autostore_status_daily` | availability, downtime, maintenance and fault hours, events, for the system and each port (Longgang local day) | analyst, Longgang manager |
| `mv_inventory` | `fct_stock_position_daily` | available stock, average daily demand, days of supply, median days of supply, material counts (as-of day; always filtered to one day) | analyst, site managers |
| `mv_supplier_delivery` | `fct_purchase_schedule_line` | schedule lines, received, on time, late, on-time rate, overdue open lines, quantities (first receipt date) | analyst, site managers |

**Planning tables (2026-10-12, PROPOSAL v2 Phase A): the replenishment engine (`data/src/snm_data/planning/`, `snm-data plan`) writes three materialized curated tables and two metric views.**

| Table or view | Grain | Content |
|---|---|---|
| `curated.supply_position` | material, plant, day (91-day horizon) | on hand (hub), backlog, in transit, expected receipts (agreed date plus the vendor's median lateness, capped at 14 days), firm demand (open deliveries, open sales-order lines), forecast (trailing 28-day rate, net of firm demand per week), projected stock, safety and maximum stock |
| `curated.replenishment_case` | case (material, plant, condition) | shortage (projected below zero), warning (below safety stock), excess (above the maximum for 8 weeks); gap; recommended lever in the fixed order transfer → expedite → new order → expedited new order (expedite levers for shortage cases only; a warning gets a transfer or a new order), with coverage date, `covers_in_time`, `days_short`; alternatives; deterministic rationale; triage class (`agent_eligible`, `human_handled`, `no_supplier`) and reasons; the demand model (`demand_model`, JSON, Croston-SBA on 26 weeks) and the chance of running out by the trigger date if nothing is done (`stockout_risk_no_action`, engine 1.1.0, record 002); costs as commercial columns |
| `curated.supplier_profile` | vendor | ABC class by trailing 12-month spend (A 70 %, B 20 %, C 10 %), order count, on-time rate, median lateness, interaction effort (median rounds and days), expedite terms, strategic and portal flags, `agent_eligible_supplier` |
| `semantic.mv_supply_position` | from `supply_position` | projected stock, receipts, demand, shortage material-days, materials short or below safety |
| `semantic.mv_replenishment_case` | from `replenishment_case` | cases by condition, lever, triage class, site, vendor; gap, days short, median days to trigger (no costs) |

Engine parameters (`planning/params.py`, overridable by YAML) are stamped on every row as `params_digest`; the defaults are those of `PROPOSAL.md` §14. Spoke and satellite stock is not counted in the projection: it is the transfer lever when a location's stock exceeds its reorder point plus 14 days of its own demand. Inputs come from the raw tables (`PlanningInputs.from_databricks`, or from a generated profile offline); the derivation and the arithmetic are tested with hand-computed references and property tests (`data/tests/test_planning.py`). Finished products built in-house (`MARC-BESKZ = 'E'`) are left out; firm demand includes open production reservations (`RESB` `SB`), the forecast the planned orders' dependent requirements (`RESB` `AR`), and the backlog overdue open reservations. Vendor effort for the triage rule is measured on expedite requests only (median rounds and days to resolution), since emergency buys and confirmation inquiries close in a message or two whatever the vendor. Hub demand includes the hub's replenishment of its spoke and satellites (`311`/`313` issuing items from the hub to a network location; engine 0.2.0, found by the replay against history, `evals/reports/engine-replay-small.md`), not the staging to the production supply area (the reservations count it). Levers with a surcharge are recommended only against a projected stock-out: a warning gets a transfer or a standard order, `days_short` then counts the days below safety stock, and no line-down cost is charged (engine 0.3.0, from the hindsight oracle, `evals/reports/engine-oracle-small.md`). On the `small` profile (generator 0.7.0, engine 0.3.0, as of 2026-09-21): 836 material-plants, 548 cases (245 shortage, 293 warning, 10 excess), 78 agent-eligible, 11 without a supplier; engine run time about 70 seconds including the lakehouse queries (about 4 seconds offline).

Design notes: a metric view has one source grain and one date basis, so outbound metrics are split by the date they are defined on (due, shipped, delivered); point-in-time metrics with a trailing window (days of supply) are precomputed per day in a curated view, and interval metrics (AutoStore availability) are exploded to daily rows, so that any range of days aggregates correctly. The agent reads the menu with `list_metrics` (one line per view with its measure and dimension names), a view's definitions with `describe_metric`, and queries with `query_metric`; the tool server checks every name, refuses `restricted_dimensions` (operator identities) for roles without people-level access, and writes the `MEASURE()` SQL. The column mask of the base table applies through the curated view, so the operator dimension carries values only for people-level roles. Every metric view reproduces the reference answers of the evaluation cases its definitions cover (`tool_server/tests/test_live_metrics.py`).

---

## 5. Data traps (by design)

| # | Trap | Where it bites | Documented in `kb/`? |
|---|---|---|---|
| 1 | EWM `*_AT` fields are UTC, `*_AT_WH` are warehouse-local; ECC `CPUDT`/`CPUTM` are local; sites use different time zones | Daily metrics, cross-system joins | Yes |
| 2 | `SHKZG` sign and reversal documents (`102`, `602`, `312`) | Naive sums over `MSEG` | Yes |
| 3 | Base vs entry UoM (`MENGE` vs `ERFMG`); a material family switches EA → BOX mid-period | Quantity trends | Partially |
| 4 | Special stock `E` / `K` is not own available stock | Inventory KPIs | Yes |
| 5 | Hub → spoke transfers double-count throughput | Outbound/inbound volume | Yes |
| 6 | Site-specific exclusions (RR: QI and GI-staging storage types; SZ: bonded area `1900`) | Available stock, throughput | Yes (per site) |
| 7 | 52/53-week fiscal calendar vs calendar months | Period comparisons | Yes |
| 8 | System users (`WF-BATCH`, `AS-INTERFACE`, `RFC-EWM`) confirm tasks | Labor productivity | Yes |
| 9 | Cancelled vs confirmed tasks; confirmations with differences; split tasks | Task counts, cycle times | Partially |
| 10 | Emergency picks with late confirmation → use RFID scans (`lts_rfid_scan`) | Emergency pick duration (RR) | No (exploration); the RFID table is documented, the late confirmations are not |
| 11 | One AutoStore bin presentation can serve several picks | AutoStore productivity | Partially |
| 12 | POD relevant only for some deliveries (`PODREL`) | On-time delivery denominator | Yes |
| 13 | Serialized parts counted by serial vs by quantity | High-value part counts | Partially |
| 14 | Replication latency: EWM hourly, ECC nightly; last day incomplete | Recent trends | Yes (freshness tool) |
| 15 | Deprecated legacy mart with an outdated OTD definition | Asset selection | Certification tag only |
| 16 | PSA staging and returns tables absent from `kb/` | Unfamiliar-domain questions | No (exploration) |
| 17 | Prompt-injection text inside free-text remarks (`shipping_checklist`) | Safety | Red-team only |

---

## 6. Injected scenarios (ground truth in `eval.anomaly_ground_truth`)

| # | Scenario | Type | Expected signal |
|---|---|---|---|
| A1 | Two AutoStore ports (`AS-PORT-03`, `AS-PORT-07`) down for 5 days at Longgang (2026-03-30 to 2026-04-03) | Real | Ports CLOSED (FAULT), no presentations or tasks at them, queue and cycle time of grid picks ↑. Delays stay within each delivery's slack before goods issue, so SZ01 PGI dates are unaffected |
| A2 | Wrong PACI configuration for one material group at Round Rock (from 2026-06-29) | Real | Putaway to the wrong storage type (high rack `0010` instead of shelving `0020`), dock-to-stock time ↑ for the group. Picks keep their slotted bins (outbound tasks are generated before receipts), so pick times are not affected |
| A3 | Dongguan spoke replenishment parameters of material group `SP-GAS` set too low (reorder point `LMINB` and replenishment quantity `LBSTF` at `CN10/3000` at about 10 % of their intended values, from 2026-06-15) | Real | Scheduled `313` transfers of the group become small and near-daily; emergency transfers (`313` posted by `RFC-EWM`) become the majority for the group; spoke stock of the group near zero. Main warehouse, other groups, and issues to manufacturing unaffected |
| A4 | Round Rock wave release policy change: hourly → twice daily at 10:00 and 16:00 local (template `RR-2XDAY`, from 2026-07-13) | Real (trade-off) | Two waves a day, much larger; task queue time about doubles; pick duration per task about 15 % shorter (batched picks). A policy decision, not a fault |
| A6 | EWM replication delivered the tasks confirmed at `DG01` on 2026-01-14 twice (new task numbers from `900000000001`, identical content) | Data artifact | Task-count spike for one day without downstream effect (ECC goods issues unchanged) |
| A7 | Seals (`SP-SEL`) issued in whole boxes from 2026-05-04: delivery lines whose base quantity is a whole number of boxes use the unit `BOX`; before that date the group was issued by the piece | Definition artifact | Delivery-unit quantities (`LFIMG`, `ERFMG`, `VSOLA`) drop sharply; base-unit quantities continue unchanged |
| A8 | Chinese New Year dip at the Chinese sites (A8a, 2026-02-15 to 2026-02-28); fiscal-quarter-end shipping push at all sites (A8b) | Expected seasonality | Should **not** be flagged as anomalies |

---

## 7. Scale (initial target; tuned after P0 benchmarks)

| Site | Outbound delivery lines / day | Notes |
|---|---|---|
| Round Rock (US10/RR01 + satellites) | ~600 | High value, serialized |
| Longgang (CN10/SZ01) | ~5,000 | AutoStore-heavy |
| Dongguan (CN10/DG01) | ~2,000 | Spoke |

Window: fiscal years FY25–FY26 (from 2024-10-28), ending a few days before "today" to reproduce incomplete-latest-day behavior. Target total: **50–70 M rows**, ~2–4 GB Delta. A `small` profile (~1 %) with identical schemas supports fast development.

---

## 7a. Case store (`cases` schema, Phase B)

The only writable schema (PROPOSAL §4.4, D17). Four append-only tables (Delta `delta.appendOnly = true`, created with `CREATE TABLE IF NOT EXISTS`, never replaced; a contract that grows is applied with `ALTER TABLE ADD COLUMNS` by `snm-cases deploy`) and one volume, written by the case server (`case_server/`, package `snm-cases`):

| Table | Content | Readable by |
|---|---|---|
| `cases.case_event` | One row per event: `case_id`, `seq`, `event_type`, `state_after`, `plant`, `material`, actor kind and role, `facts` (non-commercial JSON), `payload` (full JSON, commercial), `run_id`, `created_at` | purchasing; logistics roles without `payload` (mask) and site managers for their plant (row filter) |
| `cases.supplier_message` | Messages in both directions (body commercial; inbound bodies are untrusted) | purchasing |
| `cases.supplier_proposal` | Structured proposals, one row per option (prices, surcharges, freight, conditions commercial), recorded by the intake agent, purchasing, or the supplier on the portal; `within_mandate` holds the per-field comparison with the mandate in force (decision record 004) | purchasing; logistics roles without the commercial columns |
| `cases.supplier_attachment` | Files exchanged with suppliers (the request sheet, the returned sheet): name, type, size, SHA-256, storage path in the volume `cases.attachments`, options parsed, uploader (decision record 005) | purchasing |

The current state of a case is derived from its events; the decision is the `decision_recorded` event (no separate table). Since decision records 004 and 005 the event types also include `mandate_set` (purchasing's envelope for the supplier exchange; its ceilings are commercial) and `attachment_recorded`; the hand-off of a decided order case or a prepared transfer case and the receipt that closes it are system events, because logistics has no page and works from these tables. The state machine, the actors and roles allowed per event type, and the payload schemas live in `snm_cases.model` and are enforced by `snm_cases.service` on every append: agents draft and propose, people send, decide, hand off, and confirm (today through `snm-cases act`, the stand-in for the web app). Engine runs become events with `snm-cases sync` (one open case per plant, material, and condition; the identifier keeps the first trigger date; changes append `case_updated`, a vanished condition `condition_cleared`). A local JSONL store with the same rules serves tests, offline runs, and replay.

---

## 8. Build and governance tooling (Python)

```
data/
  generator/          seeded, scale-factor-driven synthetic generation → Parquet
  load/               upload to UC volume → raw Delta tables
  pipelines/sql/      stg views, curated and marts builds (DAG order)
data_contracts/       one YAML per table: columns, types, comments, tags, grain, keys, tests, access
semantic/metric_views/  metric view YAML (+ semantic metadata)
kb/                   agent-facing knowledge (metrics, tables, sites, glossary, policies, examples)
```

Commands: `generate`, `load`, `govern` (role identities, grants, row filters, masks; `--check` for drift), `build` (curated views and metric views, then grants), and `plan` (the replenishment engine: inputs from the lakehouse or `--from-profile` offline, outputs published as curated tables, then grants) are implemented; planned: certification in `govern`, `dq` (contract tests → `ops.dq_results`), `drift` (contracts ↔ UC ↔ KB consistency).

---

## 9. Implementation status (generator v0.8.0)

Implemented (`snm-data generate --profile test|small|full`):

- **v0.8.0 deliveries from the CO27 picking list, no external customers (decision record 003, step 1):** the production plan drives the outbound flow. Every stocked spare part belongs to the bill of material of a product made at its plant (3–8 drawn by popularity, the rest assigned; boxed consumables in whole boxes, seals preferred so that A7 keeps its group), orders are created, released, and staged inside the simulated period, and one outbound order per production order and issuing site is the CO27 picking list (`VBAK`/`VBAP` are gone): its delivery (`LIKP.LFART = 'ZPSA'`, `KUNNR` = a work center of the manufacturing department, `KNA1.KTOKD = 'ZINT'`) is planned for the staging date, its items reference the order and the reservation line (`LIPS.VGBEL = AUFNR`, `VGPOS = RSPOS`), the waves are released on the staging day, and the goods issue posts a two-item `311` per line (issuing `H` at the main warehouse, or at the Dongguan overflow hub for 30 % of Longgang's lines; receiving `S` at `2100`, `XAUTO = 'X'`; `AUFNR` and the delivery on both) when the next shuttle to the production supply area leaves (`PSA_SHUTTLE_HOURS`); a reversal is a mirrored `312` referencing the original items; the inventory simulation replays the goods issues into `2100` and starts an order with one `261` posting per order on the first working day with every line staged. The trade-compliance extract and scenario A5 are removed (no export flow); A4 keeps its wave, queue, and pick signals but no longer claims a longer delivery lead time; the Dongguan hub's reorder point and replenishment quantity cover its largest single issue. Contracts: `kna1.ktokd` `ZINT`, `likp.lfart` `ZPSA`, `lips.vgbel`/`vgpos` → `afko`/`resb`, `mseg` movement `312`; the curated outbound delivery view carries `work_center` instead of a ship-to customer and `fct_stock_position_daily` takes its demand from the `311`/`312` issues to `2100`. Step 2, the inventory network of Round Rock: the main warehouse `1000` and the overflow hub `2001` follow the min-max rule of decision record 003 (`2002`/`2003` keep no flows). The simulation derives the main warehouse's minimum (`MARC.EISBE`: 14 days of demand plus a safety stock capped at 30 days, never below the largest single daily issue), its maximum (`MABST`: the minimum plus 45 days), and the plant's reorder point (`MINBE` for reorder-point materials: the minimum plus the lead-time demand) from the issues to the production supply area, and writes them back to `MARC`. On working days logistics pulls from the hub up to the maximum when the main warehouse is below its minimum (`311` from `2001`, `WF-BATCH`), an emergency pull on the demand day (`RFC-EWM`) precedes any emergency purchase (`ZEX`), and on the Monday review the stock above the maximum moves out (`311` to `2001`); 30 % of supplier deliveries are received at the hub; the main warehouse opens between its minimum and maximum with a month of demand at the hub. Purchasing is MRP-like: an order is placed when the plant's position (both locations, open orders, minus the production-order reservations due within the planned delivery time) falls below the reorder point, for the lot plus the shortfall. Satellite consumption (`201`) and the push replenishment of the satellites are gone. On `small`: 2,501 pulls, 2,255 moves, 83 emergency orders of 14,869 (58 at Round Rock), 30 % of Round Rock's stock at the hub at the extraction.
- **v0.7.0 manufacturing demand and scenarios A3–A5:** the assembly building of each hub plant is the production supply storage location `2100` (§1.1). Finished products (`FERT`, `BESKZ = 'E'`) get a bill of materials of 3–8 spare-part components (usage 1–4 each); production orders arrive per working day (Poisson, 0.08 per product and day), for 1–3 units, created 3–10 days before their start and released the next day; `AFKO`/`AFPO` carry the schedule and actual dates, `RESB` (`SB`) the component reservations at `2100` due at the order start. The inventory simulation stages components from the hub two working days before the start as far as stock allows (`311` from `1000` to `2100`, `MSEG.AUFNR` blank), and consumes them against the order (`261`, `AUFNR` set, posted by `WF-BATCH`) on the first working day on which every component is staged; `RESB.ENMNG` is the withdrawn quantity, `XWAOK` marks consumed lines. Planned orders `PLAF` (one per product and week, weeks 2–13 ahead, weekly Poisson quantities) carry dependent requirements `RESB` (`AR`); together with the released orders' open reservations they feed the replenishment engine (firm demand = `SB`, forecast = `AR`, backlog = overdue open `SB`). The manufacturing stream has its own random seed (component 70) and the A5 grants theirs (12). A4 is applied by remapping the regular wave slots and dropping late tasks only after every draw, so the other sites' orders, deliveries, waves, and tasks are unchanged; the stages after the tasks (operator assignment per site, AutoStore ports and system confirmations, goods issue with holds, reversals, and transit noise) moved to their own streams (13, 15, 14) so that they no longer depend on Round Rock's task times, which redraws them once at every site. The planning-data stream moved from component 30 (which the inbound generator also used) to 80, so `supplier_condition` and `purchasing_interaction` values are redrawn; inbound putaways follow the changed receipts. Scenarios A3, A4, and A5 are injected (§6): A3 lowers the spoke replenishment parameters of one group, A4 switches Round Rock to twice-daily waves (and the task queue and pick models with it), A5 reclassifies one group and holds its licensed-destination deliveries until the customer's grant, which the trade extract records as licenses and `LICENSE_PENDING` holds (`trade_classification` gains `previous_eccn`). This is the one regeneration that changes ECC documents since v0.5.0; the reference answers were refreshed and reviewed (`STATUS.md`).
- **v0.6.1 planning data (additive):** `marc` gains `maabc` (ABC indicator by annual consumption value, price times popularity: A covers the top 70 % of value, B the next 20 %, C the rest; the criticality class of the cost model) and `mabst` (maximum stock, about two months of demand plus safety stock); new `raw_ext.supplier_condition` (the three vendors with the largest order value are strategic and never portal-onboarded; surcharge tiers around 10 / 15 / 25 %, minimum expedite 3–7 days, freight 80–250 USD), `raw_ext.cost_parameter` (the defaults of `PROPOSAL.md` §14), and `raw_ext.purchasing_interaction` (derived from the purchase orders: late standard orders get an expedite request, emergency buys a record, 15 % of the others a confirmation inquiry; rounds and days follow a latent effort per vendor, low for strategic vendors). Every other table is byte-identical; all reference answers unchanged.
- **v0.6.0 trade compliance and scenarios A6, A7, A8:** a GTS-like extract for Round Rock exports: `raw_ext.trade_classification` (one ECCN-style code per `US10` material, drawn per material group; mostly `EAR99`), `raw_ext.trade_license_requirement` (synthetic company configuration: `3B001` needs a license to IL, KR, SG, TW; `3A001` to IL, SG; this is not a statement of real regulations), and the restricted `raw_ext.export_license` and `raw_ext.export_hold` (`trade_compliance` role only). The extract is derived from the existing outbound flow without changing it: per ship-to customer and classification, a license timeline is built so that every shipment that needs a license ships on a day a license is valid; a delivery that was already held (the 3 % outbound holds) becomes a compliance hold when no license covered its screening day, and its release is the new license's grant; unshipped holds stay pending (blank license number, open end). A6 appends duplicate warehouse tasks for one Dongguan day; A7 changes only sales-unit fields (no random draws), so base-unit quantities, counts, and timestamps are unchanged; A8a/A8b record the seasonality already built into demand as ground truth. Scenarios A3–A5 change ECC documents and were added in v0.7.0.
- **v0.5.0 RFID tote tracking (trap 10):** `raw_ext.lts_rfid_scan` for Round Rock: each pick warehouse order is one tagged tote, read when it leaves the storage area after its last pick (`PICK_EXIT`, or `CAGE_EXIT` when that pick was from the secure cage) and at a dock door at truck loading (`DOCK`), in UTC. 40 % of Round Rock emergency picks (process type `2020`) are confirmed in EWM 15–90 minutes after the physical pick (capped at least 20 minutes before the delivery's goods issue; wave pick completion follows), so EWM overstates emergency pick durations while the exit reads keep the physical time. Unlike earlier increments this changes existing EWM confirmations, and with them the Round Rock pick cycle time reference answers. The shift-and-cap logic is shared with scenario A1 (`taskshift.py`).
- **v0.4.0 AutoStore logs and scenario A1:** `raw_ext.autostore_bin_presentation` (tasks on the same grid bin within three minutes of the previous one share a presentation; picks, decanting, or mixed), `raw_ext.autostore_port_status` (complete OPEN/CLOSED timeline per port: night maintenance about every eight weeks per port, the A1 fault), and `raw_ext.autostore_system_status` (about 1.5 short stops per 30 days, placed in quiet gaps). The controller logs Longgang local time (`TIMESTAMP_NTZ`). Every AutoStore task's resource (`PRSRC`) is the port of its presentation, chosen among open, free ports. Scenario A1 closes `AS-PORT-03` and `AS-PORT-07` from 2026-03-30 06:30 to 2026-04-03 21:00 local and adds 5–40 minutes of queue to grid picks created in the window, capped so that every pick stays at least 20 minutes before its delivery's goods issue; wave pick completion follows. ECC tables are unchanged. Also fixed: random draws in the inbound generator depended on set iteration and join row order, so a few putaway destination bins differed between runs; a cross-process test now checks that output does not depend on the interpreter's hash seed.
- **v0.3.0 EWM inbound and scenario A2:** every purchase-order receipt (`101`) into an EWM-managed storage location (Round Rock `US10/1000` → `RR01`; Longgang `CN10/1000` and bonded `CN10/1900` → `SZ01`) gets an inbound delivery (`scdl_db_proch_i`/`scdl_db_proci_i`, referenced to the purchase order in `scdl_db_refdoc` with `REFDOCCAT = 'PO'`) and a putaway task in `scwm_ordim_c` (`PROCTY = '1011'`, `TRART = '1'`, from the GR zone `9010`, no wave). Destinations follow the putaway control indicator in the new `sapapo_matlwh` (PACI `CAGE`/`HRCK`/`SHLF` at RR01, `ASTR`/`BULK`/`SHLF` at SZ01), with quality-inspection receipts at Round Rock into `8000` and bonded receipts into `BND1`. Round Rock puts away during its shifts; AutoStore putaways are confirmed by `AS-INTERFACE`; 1 % of putaway tasks are cancelled and replaced. Dongguan transfer receipts (`315`) have no EWM inbound documents yet. Scenario A2 (§6) is injected, and its ground truth is written to `eval.anomaly_ground_truth` (hidden from every agent role by the tool server). The new generator uses its own random stream, so all tables of v0.2.0 are unchanged apart from the appended inbound rows.
- **v0.2.0 inventory and procurement:** a daily simulation per plant writes every stock movement as a material document (`101` receipts for purchase orders, `321` quality-inspection release, `311` bonded clearance and hub → satellite transfers, `313`/`315` Longgang → Dongguan two-step transfers, `411` consignment withdrawal, `201` satellite consumption, `561` opening balances), and replays the outbound `601`/`602` as demand. `MARD`, `MKOL`, and `raw_ext.inventory_snapshot_daily` are derived from that ledger and reconcile with it exactly (tested every day). New tables: `lfa1`, `ekko`, `ekpo`, `eket`, `mard`, `mkol`, `raw_ext.inventory_snapshot_daily`; `MSEG` gains `INSMK`, `XAUTO`, `UMWRK`, `UMLGO`, `LIFNR`, `EBELN`, `EBELP`, `KOSTL`.
- Replenishment: hubs order from 20 fictional vendors with an (s, Q) policy (safety stock from demand variability; vendor-specific lateness); about 1 % of purchase orders are expedited buys (`ZEX`); Dongguan and the satellites use storage-location reorder points; about 10 % of Dongguan transfers are emergency transfers (posted by `RFC-EWM`).

- **30 raw tables** with contracts: ECC organization (`t001w`, `t001l`, `t320`), fiscal calendar (`t009`, `t009b`), materials (`mara`, `makt`, `marm`, `marc`, `mbew`), customers (`kna1`, `knvv`), outbound (`vbak`, `vbap`, `likp`, `lips`, `vbuk`, `vbup`, `mkpf`, `mseg`); EWM layout (`scwm_t301`, `scwm_t301t`, `scwm_lagp`), product keys (`sapapo_matkey`), outbound execution (`scdl_db_proch_o`, `scdl_db_proci_o`, `scdl_db_refdoc`, `scwm_wavehdr`, `scwm_ordim_c`); `raw_ext.workforce_roster`.
- Contract validation includes SAP format checks derived from `sap_type` (DATS/TIMS/NUMC patterns, CHAR lengths).
- Output is deterministic for a given profile and seed (verified by tests).

Conventions fixed during implementation:

- ECC creation and entry fields (`ERDAT`/`ERZET`, `CPUDT`/`CPUTM`) use the **ECC system time zone (America/Chicago) for all sites**; business dates (`BUDAT`, `WADAT`, `WADAT_IST`, `PODAT`) are site-local.
- ECC is extracted nightly (00:00 America/Chicago); EWM hourly and later, so EWM contains deliveries and tasks not yet present in ECC.
- `T009B` is maintained per calendar year; periods spanning New Year get an extra Dec 31 row.
- `MKPF.MJAHR` is the fiscal year of the posting date.
- EWM product numbers are 40-character zero-padded strings; ECC material numbers are 18 characters.
- `ORDIM_C` holds only confirmed and cancelled tasks; open tasks (`ORDIM_O`) are not generated yet.
- Planned goods issue date (`WADAT`): same day for deliveries created before the site's cut-off hour (Round Rock 14:00, Longgang 16:00, Dongguan 15:00 local) and for rush orders, otherwise the next working day. Goods issue is posted at truck loading, never before 07:00 local on the planned day; about 3 % of deliveries are held for 1–3 days.
- Only small commodity parts (fasteners, seals, consumables, fittings, gas-line filters) have a `BOX` alternative unit; instruments and assemblies are sold by the piece.
- `PODAT`/`POTIM` record the receipt at the work center; the POD confirmation reaches ECC up to three days later, which drives `PDSTK`.

Not yet implemented: EWM inbound for Dongguan transfer receipts, EWM quants (`AQUA`), sales-order special stock (`E`), serial numbers, EWM production supply (staging is an IM transfer without warehouse tasks), labor-management extract, and chunked generation for the `full` profile (peak memory currently exceeds 15 GB at full scale; `small` needs ~0.5 GB).
