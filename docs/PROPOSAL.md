# Verified Supply-Planning Agent for Warehouse Logistics — Design Proposal

Status: **Draft v2.0** · Last updated: 2026-10-12 · Supersedes v1 (the analytics-only design, archived as [`archive/PROPOSAL_v1.md`](archive/PROPOSAL_v1.md))
Companion documents: [`DATA_MODEL.md`](DATA_MODEL.md) (v2 update planned in Phase A), [`STATUS.md`](STATUS.md)

---

## 1. Summary

A **planner copilot** for the Global Logistics and Purchasing teams of the fictional semiconductor-equipment manufacturer **SouthNorth Solutions**. A deterministic engine projects, per part and site, whether the main warehouse's stock, its overflow hub's stock, and open purchase orders cover the manufacturing department's backlog and demand, and turns every gap or excess into a **case** with a recommended lever: transfer, expedite an open purchase order, place a new order, or return excess to the hub. Two agents work the cases. The internal **planner agent** prepares each case for the people who decide (it explains the gap, drafts supplier requests, evaluates the options that come back, and drafts the handoff to logistics). The external-facing **intake agent** exchanges information with suppliers on the cases that purchasing hands to it, the long tail and the time-consuming ones, and brings structured proposals back. Every decision is made by a person, every number in an answer traces to a query result, and the agent that talks to suppliers can read no internal data.

**Business model and scope** (decision record [`003`](decisions/003-manufacturing-supply-model.md), 2026-10-16): each site has a **main warehouse** that issues to the company's own manufacturing department (demand as the SAP CO27 picking list, replicated every 30 minutes) and **overflow hubs** that hold what the main warehouse does not need right away. When the main warehouse is short it pulls from its overflow hub; when the two together are not enough, purchasing expedites an open order or places a new one; when the main warehouse holds more than its maximum, the surplus is moved to the overflow hub after a person's review (min–max). There are no external customers. The workflow covers Round Rock's main warehouse `1000` and its overflow hub `2001`; Longgang, Dongguan, and the other Round Rock hubs are outside it for now. The v1 data still carries customer deliveries as legacy flows until generator 0.8.

The verified analytics agent of v1 (governed definitions, guarded SQL, metric views, numeric faithfulness) stays in full as the planner agent's analytical capability and as the measured foundation the new workflow is built on.

The project has three goals:

1. **Business value that can be measured in money and hours.** Fewer stockout days for manufacturing, less expedite spend, and purchasing time freed from tail-supplier back-and-forth, measured against a hindsight oracle and a human baseline on synthetic data where the future is known.
2. **Agent engineering depth:** deterministic core plus LLM edges, multi-party workflow with asymmetric permissions, an untrusted external counterpart, append-only context, tool design, guardrails, tracing, and evaluation, each justified by a concrete failure mode.
3. **Demonstrable results:** decision quality, conversation quality, safety, cost, and latency against baselines, with ablations that explain the design.

### 1.1 Changes from v1

- **Purpose:** from answering questions about the past to preparing and tracking replenishment decisions. Question answering remains a capability inside the workflow.
- **New data domain:** manufacturing demand (production orders, component reservations, planned independent requirements, backlog), supplier conditions (lead times, prices, expedite surcharges), purchasing history (requests, responses, outcomes, effort), and a cost model.
- **New deterministic core:** the supply projection and case engine (§4.2) and the triage rules (§4.3).
- **Two agents instead of one:** the planner agent (internal) and the intake agent (external-facing, isolated), §5.4–5.5.
- **A case store with append-only writes** (§4.4, §7.1), the only writes the system makes; source and curated data stay read-only.
- **Three new roles:** `purchasing`, `supplier_<vendor>` (per onboarded tail supplier), and the logistics endpoint mapped onto the existing roles (§7.3).
- **Evaluation of value:** cost model, hindsight oracle, closed-loop replay, supplier simulator, human baseline (§10.3–10.6).
- **Delivery plan** rebased (§12); demo-readiness moves later in favor of finishing the workflow.

### Non-goals

- Replacing purchasing or planning staff. The agent takes the tail and the time sinks; strategic suppliers stay with people.
- Autonomous negotiation or commitments. The agent never accepts a price, places or changes a purchase order, or sends anything a person has not approved.
- Write-back to SAP or to any source or curated data. Decisions are recorded in the case store; purchase-order changes are made in SAP by people, outside this system.
- Replacing BI dashboards or a production semantic layer.
- Real company data. All data is synthetic; structures follow public SAP standards.
- A general-purpose agent framework.

---

## 2. Decisions log

Decisions D1–D10 of v1 stand (TypeScript harness on PI core packages, MCP over stdio, Databricks Free Edition, Unity Catalog semantics plus `kb/`, SAP ECC + decentralized EWM, the three sites, the 52/53-week fiscal year, the domain policies, English artifacts). New decisions:

| # | Decision | Rationale |
|---|---|---|
| D11 | Manufacturing is modeled as a **production supply storage location** (`2100`) of each hub plant (Round Rock `US10`, Longgang `CN10`); demand comes from production orders and their component reservations, forecast from planned independent requirements, backlog from overdue open reservations | Matches "two buildings on one campus" in SAP terms without a second plant and stock-transport orders; reuses the existing inventory ledger |
| D12 | The **supply projection engine is deterministic Python** with parameters in master data and configuration, never in code; the agent reads its outputs and never recomputes them | The calculation has a clear specification; explainability and testability require it |
| D13 | **Triage is a deterministic rule** on supplier class, case value, and historical interaction effort; purchasing can override either way | The scope boundary must be inspectable and auditable, not a model judgment |
| D14 | **Every decision is made by a person.** Sending to a supplier and recording a decision are UI actions by purchasing (or logistics for transfers); the agents only draft, evaluate, and flag | Commercial and legal weight of commitments; the Walmart/Pactum precedent limits even negotiation bots to standard terms |
| D15 | **Two agents with disjoint tools and credentials.** The intake agent receives only a case's outward fields and has no database access; supplier content is untrusted input | External-party isolation; prompt injection and data leakage are first-order risks |
| D16 | Supplier exchange is a **channel-independent structured message**; the web portal is the first channel, an e-mail adapter is a later one | Real suppliers reply to e-mail; the structure, not the channel, is the contract |
| D17 | The **case store** (`cases` schema) is the only writable surface, append-only, written by a separate case server with its own identity; the data tool server stays read-only | Keeps the v1 read-only guarantee for all source and curated data; one audited write path |
| D18 | Value is measured on synthetic data with a **hindsight oracle, a closed-loop replay, a supplier simulator, and a human baseline**, under an explicit cost model with sensitivity analysis | Real deployments publish no independent evaluation; here the future is known by construction |
| D19 | The **cost model, triage thresholds, and escalation rules start from the defaults in §14** and are tuned by evaluation | Owner decision 2026-10-12: start from defaults |
| D20 | The v1 analytics agent, its evaluation bank, and its governance remain in place and are regression-tested | They are the planner agent's analytical tool and the foundation of the new roles |

---

## 3. System architecture

```
┌──────────────────────────── TypeScript (Node 22) ─────────────────────────────┐
│ apps/cli (headless JSON mode for evals)   apps/web (case queue · drafts ·      │
│                                           decisions · supplier view, per role) │
│ harness/                                                                        │
│   agent        pi-agent-core Agent + pi-ai (Anthropic); two agent profiles:    │
│                planner (internal) · intake (external-facing, isolated)         │
│   hooks        prepareRequest · beforeToolCall · afterToolCall · finishTurn     │
│   context/     append-only transcript · task card (= case state) · KB tools    │
│   subagents/   explorer · verifier (P2 depth work, unchanged from v1)          │
│   policy/      roles · budgets · tool allowlists · approvals · output checks   │
│   telemetry/   JSONL traces (source of truth) · MLflow spans                   │
│   mcp-bridge/  MCP client → AgentTool adapter (one bridge per MCP server)      │
└───────────────┬───────────────────────────────┬────────────────────────────────┘
                │ MCP over stdio                │ MCP over stdio
┌───────────────▼───────────────┐ ┌─────────────▼──────────────────────────────┐
│ Python data tool server        │ │ Python case server                          │
│ (read-only, per-role OAuth    │ │ (append-only writes to the `cases` schema,  │
│  client)                      │ │  writer identity `snm-app-cases`)           │
│ schema tools · run_sql ·      │ │ list_cases · get_case · explain_case ·      │
│ metric tools · checks         │ │ draft_* · propose_recommendation ·          │
│ SQL guard · masks · handles   │ │ request_decision · record_proposal          │
└───────────────┬───────────────┘ └─────────────┬──────────────────────────────┘
                │                               │
                ▼                               ▼
     Databricks SQL warehouse · Unity Catalog (raw · curated · semantic · cases) · MLflow

Python (separate processes): data generator · supply projection engine (nightly) ·
pipelines · governance deploy · evaluation (scoring, replay, supplier simulator)
```

### 3.1 Responsibilities

| Concern | Owner | Notes |
|---|---|---|
| Loop, context, prompts, agent profiles, sub-agent orchestration | TS harness | Exact control of request bytes (append-only, §5.3) |
| Knowledge-base retrieval (`kb/`) | TS harness | No credentials required |
| Data access, SQL safety, result handling | Python data tool server | Enforced at the layer that owns the credential; unchanged from v1 |
| Case reads and append-only case writes | Python case server | Schema-validated events; the only write path |
| Supply projection, case generation, triage | Python engine (nightly job and on demand) | Deterministic, parameterized, tested against hand-computed references |
| Agent-level policy (budgets, roles, approvals, output validation) | TS harness | Hooks |
| Web app (purchasing case page, supplier page; decision record 005) | Python `apps/web` (FastAPI, Jinja2) | Thin; calls the case service in process and the harness through its headless CLI; role play in local mode, role from login later; no page for logistics |
| Data generation, pipelines, governance deployment | Python CLI | Deterministic, seeded |
| Evaluation scoring, replay, supplier simulation, statistics | Python | Calls the TS agents through the headless CLI |

### 3.2 Secrets separation

- The Anthropic API key exists only in the TS process (`SNM_ANTHROPIC_API_KEY`).
- The data tool server receives only the active role's service principal (an OAuth client, read-only by its grants). The case server receives the case-writer identity, which has `SELECT` and `INSERT` on the `cases` schema and nothing else. The owner's token is used only by data, governance, and evaluation tooling.
- The intake agent's process has no database credential at all: its context is handed to it by the harness from the case store, and its only write goes through `record_proposal` on the case server, scoped to the case it was started for.
- Logs and traces redact secrets; keys never enter the repository or chat.

### 3.3 Dependency hygiene

- PI packages are pinned to exact versions and upgraded deliberately; all PI usage goes through a thin adapter layer.
- The case server shares the contract and governance modules with the data tool server; the engine shares the calendar, site, and unit modules with the generator.

---

## 4. Replenishment engine and end-to-end flows

### 4.1 Demand and supply model

New synthetic data (generator v0.7, Phase A), all in public SAP structures with fictional values:

| Element | SAP objects | Notes |
|---|---|---|
| Manufacturing demand | Production orders `AFKO`/`AFPO`; component reservations `RESB` (material, plant, requirement date `BDTER`, required `BDMNG`, withdrawn `ENMNG`) at the production supply storage location `2100` | Staging hub → `2100` as `311`; consumption as `261` against the order. Orders are released 1–3 weeks ahead; about 5 % are rush orders with short notice |
| Backlog | Open reservation quantity with `BDTER` before the as-of day | Counted on day 0 of the projection |
| Forecast | Planned orders `PLAF` per product and week (weeks 2–13 ahead) with their dependent requirements (`RESB`, requirement type `AR`) per component and plant; plus the trailing customer demand rate for spare parts | Implemented as planned orders rather than planned independent requirements (`PBIM`/`PBED`): the MRP output already nets them, so no consumption logic is needed |
| Customer demand | Legacy sales orders and outbound deliveries of v1 | Outside the business model (record 003); ignored by the engine unless `include_customer_demand` is set; removed by generator 0.8 |
| Supply on hand | `MARD`, `inventory_snapshot_daily`, EWM quants | Available stock at the main warehouse (`1000`) and at its overflow hub (`2001`), per site rule (QI, bonded, GI staging excluded as today) |
| Supply in the network | Overflow hub stock and stock in transit | The overflow hub keeps nothing back: its stock is pooled with the main warehouse's; a pull is same-day on the Round Rock lane |
| Open purchase orders | `EKKO`/`EKPO`/`EKET` open quantity (`MENGE − WEMNG`), delivery date `EINDT`, confirmed date when present | Expected receipt = `EINDT` shifted by the supplier's median lateness over the trailing 12 months, capped at 14 days |
| Supplier conditions | `EINA`/`EINE` (planned delivery time `PLIFZ`, minimum order quantity, price), `LFA1`, a supplier condition table with expedite surcharge tiers and minimum expedite lead time | Vendor-specific; 20 vendors exist, about 8 are tail vendors |
| Purchasing history | New `raw_ext.purchasing_interaction` (request, rounds, days to resolution, outcome, surcharge paid, who handled it) and `raw_ext.supplier_quote` (historical quotes) | The human baseline for §10.6; `purchasing` role only; price fields masked for other roles |
| Cost parameters | `raw_ext.cost_parameter` (line-down cost per criticality class, holding rate, transfer cost, order cost) and `marc`-level criticality (`A`/`B`/`C`) | §10.3 and §14 defaults |

The `DATA_MODEL.md` update in Phase A specifies tables, contracts, generator components (each with its own random stream so existing tables stay byte-identical), and the ground truth written to `eval` (true supplier capability per case, realized demand, oracle actions).

### 4.2 Supply projection and case generation

Runs nightly after the data load and on demand; deterministic Python (`data/src/snm_data/planning/`), outputs as Delta tables in `curated` (materialized, not views, because the agent and the UI read them repeatedly and the cases need stable identifiers).

**Projection.** For each material × plant, two daily lines over a 91-day horizon (engine 1.0, record 003):

```
projected_main[d] = available at the main warehouse (day 0)
                  + stock in transfer to the main warehouse (day 1)
                  + Σ open PO quantity with expected receipt ≤ d
                  − backlog (open component reservations due on or before day 0)
                  − Σ firm demand with requirement date ≤ d (open production-order reservations: the CO27 picking list)
                  − Σ forecast with bucket end ≤ d (planned-order requirements on their dates, plus the trailing
                    staging rate netted against the explicit requirements of the same week)
projected[d]      = projected_main[d] + available at the overflow hub        (the pooled line)
```

The overflow hub has no demand of its own, so all of its stock counts for the main warehouse. Supplier deliveries may arrive at either; in the generated data they arrive at the main warehouse.

**Demand model and stock-out risk** (engine 1.1.0, decision record [`decisions/002-demand-variability-term.md`](decisions/002-demand-variability-term.md), phase 3). The forecast above is one average line; parts with lumpy demand run out well before or after it. Each material therefore also gets a demand model: Croston-SBA on the 26 weekly buckets of staging before the as-of date (the probability that a week has demand, the sizes of the weeks with demand). Simulated, it gives 2,000 demand paths per case, netted against the explicit requirements week by week as the forecast is. The case carries the model (`demand_model`) and the share of paths that go below zero by the trigger date if nothing is done (`stockout_risk_no_action`); the case server prices every option on the same paths: its stock-out risk up to the later of the trigger date and the option's last arrival, and its expected line-down over the horizon. Only segments whose forecast the backtest calibrated are priced (outflow in at least one of the last 13 weeks); the model's tail is too thin, so every probability goes through a calibration curve per demand-frequency segment (the share of past windows that ran out at the same model probability, fitted on the first half of the backtest and conservative on the second); the calibrated probability is shown and checked against the service targets (A 1 %, B 3 %, C 10 %, record 002 as amended on 2026-10-19). The average line still decides when a case opens: with the model's average instead of the trailing rate the replay caught none of six realized emergencies instead of two. Purchasing's ranking rule does not use the risk yet; phase 4 replays a risk-aware rule against it first.

Thresholds per material and plant: safety stock (`MARC-EISBE`) and maximum stock (`MARC-MABST`, or hub max from `SAPAPO/MATLWHST`). Four conditions open a case, the first that applies: **shortage** when the pooled `projected[d] < 0` for some `d` (first such day = trigger date, gap = `−min projected`); **warning** when the pooled line goes below the minimum (`safety`) without going negative; **pull** when the pooled line stays at or above the minimum but `projected_main[d] < safety` for some `d` (gap = the main warehouse's shortfall, never more than the overflow hub holds); **excess** when `projected_main[d] > maximum` for every day of the next 8 weeks.

**Lever selection** (deterministic). Shortage and warning cases get an order lever, the first in the order below that covers the gap by the trigger date, the others listed as alternatives with their coverage date; pull and excess cases get a move, which logistics reviews before it is triggered:

1. **Pull** (`transfer`) from the overflow hub into the main warehouse for a pull case: same day on the Round Rock lane, transfer cost only.
2. **Expedite** an open purchase order whose expected receipt is after the trigger date and whose supplier's minimum expedite lead time (default: half the planned delivery time, at least 3 days) reaches before it.
3. **New order** when the planned delivery time reaches before the trigger date; **expedited new order** when only the minimum expedite lead time does.
4. **Unavoidable shortage** when no lever reaches: the case carries the earliest feasible date and the expected stockout days.
5. **Move to the overflow hub** (`move_to_overflow`) for an excess case: the surplus above the maximum leaves the main warehouse after a person's review.

**Warnings get no expedite** (engine 0.3.0, decision record [`decisions/001-expedite-only-against-stockout.md`](decisions/001-expedite-only-against-stockout.md)). For a warning case (below safety stock, never below zero) only the standard levers are recommended: a transfer, else a new order at the planned delivery time, even when it arrives after the stock first falls below safety stock; the expedite levers stay listed as alternatives with their costs, and no line-down cost is counted. The hindsight oracle on the generated history (§10.4, `evals/reports/engine-oracle-small.md`) showed that expediting to restore safety stock paid surcharges that bought nothing: almost all of the engine's regret was on warning cases.

**Outputs.**

| Table | Grain | Content |
|---|---|---|
| `curated.supply_position` | material × plant × day | every element of the projection and the projected stock; the engine version and parameter digest |
| `curated.replenishment_case` | material × plant × condition (one open case at a time) | shortage date, gap, horizon, recommended lever and alternatives with coverage dates, the rows behind each element (`result_id`-style references), rule rationale, triage class (§4.3), supplier, value |
| `curated.supplier_profile` | vendor | class (A/B/C), trailing spend, on-time rate, median lateness, median interaction rounds and days (from purchasing history), minimum expedite lead time, surcharge tiers |

Every number in a case is reproducible from `supply_position` and the source rows; `explain_case` (§6) returns the deterministic explanation, which the planner agent quotes rather than recomputes.

### 4.3 Triage and escalation

A case is **agent-eligible** (the intake agent may handle the supplier exchange) when all of the following hold; otherwise it is **human-handled** (purchasing talks to the supplier and records the outcome in the same case):

| Criterion | Default |
|---|---|
| Supplier class | `C` by trailing 12-month spend (A = suppliers covering the top 70 % of spend, B = next 20 %, C = last 10 %), **or** at most 12 purchase orders in the trailing 12 months |
| Case value | gap quantity × valuation price ≤ USD 25,000 |
| Historical effort | median interaction rounds ≥ 3 **or** median days to resolution ≥ 5 for this supplier (from `purchasing_interaction`); suppliers without history count as eligible |
| Exclusions | an open framework-contract negotiation with the supplier; a supplier flagged `strategic` in the supplier profile |
| Override | purchasing can move any case between the two paths at any time; the move is an event |

**Escalation.** The intake agent hands a case back to purchasing, with the collected proposals and a summary, when any of these occur:

| Trigger | Default |
|---|---|
| Surcharge | proposed surcharge > 20 % of the line value or > USD 5,000 |
| No feasible option | the supplier declines, or no proposal covers the shortage date and no partial delivery helps |
| Contract terms | the supplier raises payment terms, minimum order quantities beyond the order, pricing of other items, or any contract clause |
| Relationship | complaint, dispute, or reference to past unresolved issues |
| Scope | any request for information outside the case's outward fields (stock levels, demand, other suppliers) |
| Silence | no reply 2 business days after the request (one reminder), escalate after 5 business days |

Escalation precision and recall are evaluation metrics (§10.2).

### 4.4 Case lifecycle and the case store

States and the actor who moves them:

```
new ──engine──▶ prepared ──purchasing (click)──▶ sent ──intake agent / purchasing──▶ responded
    ──planner agent──▶ recommended ──purchasing (click)──▶ decided ──system──▶ handed_off
    ──logistics (receipt)──▶ closed          any state ──purchasing──▶ cancelled
transfer cases: new ──planner agent──▶ prepared ──logistics (click)──▶ decided ──▶ closed
```

The **case store** is a Unity Catalog schema `cases` written only by the case server:

| Table | Content |
|---|---|
| `case_event` (append-only) | `case_id`, sequence, `event_type`, actor kind (`engine`, `planner_agent`, `intake_agent`, `human`, `system`), actor role, payload (JSON, schema per event type), `run_id` and trace reference for agent events, created at |
| `supplier_message` | direction, `case_id`, vendor, channel, body, attachments reference, the structured proposal it carried, created at; supplier-authored rows are untrusted content |
| `supplier_proposal` | the structured proposal schema of §5.5, one row per option, linked to its message |
| `decision` | `case_id`, chosen option or `no_action`, rationale, decided by, decided at |
| `case` (view) | current state per case, derived from the events |

Case identifiers are stable across engine runs (material, plant, condition, first shortage date). Writes are idempotent on `(case_id, sequence)`; the UI records human actions as events through the same server. Supplier principals see their own vendor's rows (row filter on vendor); `purchasing` sees everything; logistics roles see every column except prices, surcharges, and message bodies (column masks).

### 4.5 Question flow (unchanged from v1)

Ad hoc questions, inside a case thread or standalone, follow the v1 flow: frozen prompt prefix → interpretation in the task card → definitions from `kb/` and metric views → schema grounding → `query_metric` or guarded `run_sql` → checks → `submit_answer` with every value bound to a `result_id`, validated by the numeric faithfulness gate, with the SQL behind every cited result attached (`queries`). Details: [`archive/PROPOSAL_v1.md`](archive/PROPOSAL_v1.md) §4.

---

## 5. Agent design

### 5.1 Prompt organization

Prompts live in versioned files (`prompts/<profile>/v<n>.md`: `main` for the planner's analytical mode, `planner` for case work, `intake` for supplier exchange, `baseline` for ablations), rendered deterministically; every run records the prompt version. Role and site scope are part of the frozen prefix; changes mid-session are appended as system messages, never edited in place.

### 5.2 Task card = case state

The task card of v1 becomes the case's working state when the agent is started for a case: the engine's recommendation, the outward fields, the proposals so far, open questions, and the interpretation of any question asked in the thread. It is updated only through `update_task_card`, appended on change, and preserved across compaction, which is what multi-turn case threads need.

### 5.3 Context lifecycle under the append-only constraint

Unchanged from v1: append-only transcripts (thinking-block prefix binding), mid-conversation system messages for instruction changes, full tool set declared at session start, small tool results with handles, compaction at thread boundaries (summary + task card + new input), `input_transformations` logged on every response. Case threads are long-lived: each agent invocation on a case starts a fresh session from the case store (events are the memory), so no transcript has to be carried across days.

### 5.4 Planner agent (internal)

Runs under the role of the person it works for (their service principal in the data tool server; the case server checks the same role). Triggered by state changes (`new` → prepare; `responded` → evaluate; `decided` → draft handoff) and by questions in the case thread.

| Capability | How |
|---|---|
| Explain the case | `get_case`, `explain_case` (deterministic rationale from the engine), plus analytical tools for context: supplier on-time history, past expedites of the part, demand pattern |
| Draft the supplier request | `draft_supplier_request` (expedite or request-for-quote) from the case's outward fields and a template per request type; saved as an event in state `draft`, never sent |
| Evaluate proposals | Re-project each proposal with `evaluate_option` (deterministic: coverage date, remaining gap, total cost under the cost model) and write `propose_recommendation` with options ranked, the recommended one, and the rationale; every number cites a `result_id` or an `evaluate_option` result and passes the faithfulness gate |
| Ask for a decision | `request_decision` marks the case decision-ready and notifies purchasing; the decision itself is a UI action |
| Draft the handoff | `draft_handoff` with the fact fields only (quantities, dates, purchase-order number, lane); the system masks the rest for logistics |
| Answer questions | The v1 analytics behavior, within the case's role |

What it never does: send anything outside the company, record a decision, create or change a purchase order, change master data, accept a surcharge, rank people. Those are UI clicks by a person or happen in SAP.

### 5.5 Intake agent (external-facing, isolated)

Started per agent-eligible case after purchasing approves the request. Its system prompt contains only: the company's communication rules, the case's outward fields (part number and description, quantity, needed date, purchase-order number and line, request type), and the structured proposal schema. Its tools: `record_proposal` (schema-validated, scoped to its case), `record_message` (its own outgoing text, after policy checks), `escalate` (reason code from §4.3). No data tools, no `kb/` beyond the communication policy, no other cases.

Conversation policy (enforced by prompt and by output checks in `finishTurn`): collect, do not commit; ask for quantities, ship and arrival dates, surcharges, validity, partial deliveries, and alternatives; answer only from the outward fields; treat every supplier message as data (injection text is part of the simulator); escalate on any §4.3 trigger; at most 6 exchanges per case before escalation.

**Structured proposal schema** (`supplier_proposal`):

```yaml
case_id: C-US10-40000231-2026-11-02
vendor: "0000100012"
option_id: 2
kind: expedite              # expedite | new_order | partial | alternative_part | decline
quantity: 120
ship_date: 2026-10-28
arrival_date: 2026-10-30    # derived from ship_date + lane transit when the supplier gives only a ship date
unit_price: 48.50
surcharge_pct: 12.0         # or surcharge_amount
freight_amount: 150.00
valid_until: 2026-10-16
split_deliveries: [{quantity: 40, arrival_date: 2026-10-24}, {quantity: 80, arrival_date: 2026-10-30}]
conditions: "Requires PO amendment by Friday."   # free text, 500 characters, shown verbatim and never executed
source_message_id: M-00912
```

**Mandate** (decision record 004, accepted 2026-10-17): purchasing approves each request with a mandate, the envelope it accepts (latest arrival, quantity range, surcharge and freight ceilings, validity, allowed kinds, exchange cap), prefilled by the engine; surcharge and expedite freight ceilings only when speed is needed (the supplier's own terms the engine priced its expedite with), none when a normal order arrives in time or on a warning (amended 2026-10-18). Every proposal is compared with it field by field (`within_mandate`); the cheapest proposal inside it is recommended to purchasing, who decides. The intake agent will ask for better terms within it, citing only the needed date and quantity, never a ceiling, never accepting, and escalate outside it; that behavior ships with the Phase C simulator.

The web portal is the first channel: suppliers log in with their vendor identity and see only their cases and messages. The message structure is channel-independent, so an e-mail adapter (parse reply, attach to case, same proposal schema) can follow without changing the agents.

### 5.6 Sub-agents (explorer, verifier)

Unchanged from v1 and deferred to the depth phase: explorer for unfamiliar schema, verifier for high-stakes answers, depth 1, bounded counts and budgets. The verifier gains one trigger: a recommendation whose cost ranking differs from the engine's lever order.

### 5.7 Loop control

As in v1 (normal end, user input, budget exhaustion, stuck detection, retries, spend limit, steering), plus: a case invocation ends with exactly one case event (or `escalate`); the intake agent ends every exchange with `record_message` or `escalate`; a supplier conversation has a hard cap of 6 exchanges and 48 hours of agent activity.

### 5.8 Model allocation (tiers; exact IDs live in configuration)

| Role | Tier | Rationale |
|---|---|---|
| Planner agent | Mid for development runs, strong for milestones | Same as the v1 main agent; the deterministic engine carries the arithmetic |
| Intake agent | Mid | Bounded, scripted conversation; errors are caught by schema checks and escalation rules |
| Supplier simulator (evaluation) | Mid | Persona with hidden ground truth; cost matters because it runs in every conversation eval |
| Verifier | Strong, high effort | Last line of defense on recommendations |
| LLM judge (evaluation) | Mid, via Batch API | Commitment and tone rubrics only; calibrated against human labels |

---

## 6. Tool catalog

| Tool | Server | Purpose | Guards |
|---|---|---|---|
| `kb_search`, `kb_read` | TS | Bounded grep / section read over `kb/` | Path allowlist, caps |
| `update_task_card` | TS | Case working state | Schema validation |
| `ask_user`, `submit_answer` | TS | Clarification; final structured answer | Strict schema, faithfulness gate |
| `list_tables`, `describe_table`, `profile_column`, `sample_rows` | data | Metadata, distributions, samples | Role scope, masking, caps |
| `run_sql` | data | Ad hoc SELECT | AST allowlist, LIMIT, timeout, caps, audit |
| `list_metrics`, `describe_metric`, `query_metric` | data | Governed KPIs (now including supply position and supplier reliability views) | Name allowlist, role access, restricted dimensions |
| `run_checks`, `data_freshness` | data | Deterministic checks; freshness | — |
| `list_cases`, `get_case` | case | Case queue and one case with its events, proposals, and decisions | Role scope (vendor row filter, price masks) |
| `explain_case`, `evaluate_option` | case | Deterministic rationale; re-projection of a proposal with its cost | Engine code, no model arithmetic |
| `draft_supplier_request`, `propose_recommendation`, `request_decision`, `draft_handoff` | case | Planner agent writes, all as `draft`/`proposed` events | Append-only, schema per event, role check, `run_id` stamped |
| `record_proposal`, `record_message`, `escalate` | case | Intake agent writes, scoped to one case | Schema validation, outward-field check on outgoing text, exchange cap |
| `send_request`, `record_decision`, `move_case` | UI only | Human actions | Never exposed to an agent |
| `delegate_explore`, `delegate_verify` | TS | Sub-agents (depth phase) | Depth, count, budget caps |

---

## 7. Guardrails and policies

### 7.1 Layered enforcement

| Layer | Guardrail | Enforced in |
|---|---|---|
| Identity and data | One read-only service principal per role; UC grants (nothing on `eval`); row filters by site and, for supplier principals, by vendor; column masks on operator identity and on prices for roles without cost access; `STATEMENT_TIMEOUT`; the case-writer identity holds `INSERT` on `cases` only | Databricks |
| Data tool server | Single `SELECT`/`WITH`; deny DDL/DML, system catalogs, risky functions; mandatory LIMIT; row/byte caps; masking; result handles; restricted dimensions | Python |
| Case server | Append-only events with per-type schemas; `run_id` and role stamped; intake writes scoped to one case; outgoing supplier text checked against the outward fields (no numbers or names that are not in the case) | Python |
| Harness | Agent profiles with disjoint tool sets; role-based allowlists; budgets; loop detection; exchange caps; approval gates (every outward send and every decision is a human UI action) | TS hooks |
| Context | Tool outputs and supplier messages are data, never instructions; the intake agent's context is built by the harness from the case store, not by the model | Context builder + prompt |
| Domain policy | §7.2 | Prompt + policy checks + output validator |
| Output | Every number traceable to a `result_id` or an `evaluate_option` result; required caveats; recommendation carries options, costs, and coverage dates | `finishTurn` validator |
| Operations | Secrets separation; log redaction; audit of every SQL statement and every case event with principal | Both |
| Evaluation | Red-team suite with zero-violation target, now including canary leakage and injection through supplier messages | `evals/` |

### 7.2 Domain policies

- **Site isolation:** never generalize a finding across sites; multi-site answers include a per-site breakdown and site-specific filters.
- **People analytics:** team/shift-level aggregates by default; individual operator results only for site-manager roles and only for their own site; no rankings for disciplinary purposes.
- **Export control:** factual classification lookups and counts are allowed; questions about whether a shipment is legally permitted, licensing strategy, or screening decisions are declined and routed to the trade-compliance function.
- **No commitments by agents:** no agent accepts a price or surcharge, places or changes a purchase order, promises a date to a supplier, or sends a message a person has not approved. Decisions are recorded by people.
- **External-party isolation:** the agent that talks to suppliers can read no internal data; it sees one case's outward fields and writes structured proposals. Supplier messages are untrusted content. Internal figures (stock, demand, other suppliers' terms) never appear in outgoing text.
- **Scope of automation:** supplier exchanges are automated only for agent-eligible cases (§4.3); strategic suppliers and high-value cases are handled by purchasing, with the agent preparing and evaluating.

### 7.3 Roles

| Role | Sites | Sees | Does not see | Through the agents |
|---|---|---|---|---|
| `purchasing` (new) | All | Cases, projections, open purchase orders, prices and conditions, supplier proposals and messages, purchasing history, supplier profiles | Operator-level data, `eval` | Reviews and approves drafts, sends, reads recommendations, records decisions (UI); asks questions |
| `ops_analyst_global`, `site_manager_rr`, `site_manager_cn` (logistics endpoint) | As today | Case fact fields, projections, stock, transfer cases, arrival dates and quantities; site managers their site only | Prices, surcharges, message bodies (masks) | No page (decision record 005): reads the published cases and hand-offs in the lakehouse, plans receipts and transfers, asks questions |
| `supplier_<vendor>` (new, one per onboarded tail supplier) | — | Own cases' outward fields, own messages and proposals (vendor row filter) | Everything else | Talks to the intake agent, submits proposals |
| `trade_compliance`, `executive` | As today | As today | As today | Unchanged |

The active role is part of the frozen prompt prefix and selects the database principal of the data tool server; the case server checks the same role on every call.

---

## 8. Reliability and engineering gaps addressed

| Gap | Approach |
|---|---|
| Long-lived cases across days | Events are the memory; each agent invocation starts from the case store; task card rebuilt from events |
| Duplicate or out-of-order events | Idempotent `(case_id, sequence)`; the case view is derived; engine runs are versioned so a re-run does not reopen closed cases |
| Supplier message parsing | Structured schema first (portal form or agent extraction), free text kept verbatim; split deliveries, unit mismatches, ship-date-only replies are first-class cases in the simulator because incumbent products list them as unsupported |
| Warehouse cost and reproducibility | Query result cache keyed by normalized SQL + data snapshot; engine outputs materialized nightly |
| Testing without API spend | Record/replay of model responses; the engine and the case server are pure Python with fixtures |
| Error taxonomy | As v1, plus: case conflict (state changed under the agent), escalation, exchange cap |
| Reproducibility | Run manifest adds engine version and parameter digest, case store snapshot, simulator seed |
| Contract safety across languages | MCP schema contract tests for both servers |
| Test suites | `vitest`, `pytest`, engine property tests, eval smoke set in CI |

---

## 9. Observability

- **Trace model:** case → invocation (agent profile, role, trigger) → turns → LLM calls → tool calls → case events written; supplier conversation → exchanges → proposals recorded; final recommendation → options, costs, decision.
- **Storage:** JSONL per run (source of truth) plus MLflow on Databricks (one trace per invocation, one run per evaluation batch, raw files as artifacts), uploaded by the Python evaluation tooling as today.
- **Audit:** every SQL statement with principal and role; every case event with actor, role, `run_id`; every outgoing supplier message with the approving user.
- **Uses:** failure clustering by case type and supplier persona, regret attribution (which lever, which element), cost per case, reproduction via run manifest.

---

## 10. Evaluation

### 10.1 Principles

The operating manual for this section is [`EVAL_GUIDE.md`](EVAL_GUIDE.md) (gates, case generation, metrics, health checklist, the owner's part).

Deterministic grading first; LLM judges only for subjective aspects (commitment language, tone), calibrated against human labels. Synthetic data makes the future known, so value is measured against counterfactuals, not against vendor claims. Locked test splits run only at milestones; every failing case links to its trace; configuration comparisons use paired statistics.

### 10.2 Evaluation map

Layers L0–L11 of v1 stay (ground truth, tools and guardrails, retrieval, schema linking, behavior routing, result correctness, output quality, calibration, sub-agents, diagnosis, efficiency, robustness); the 163-case analytics bank remains the regression suite for the planner agent's analytical mode. New layers:

| Layer | Target | Metrics | Grading | Phase |
|---|---|---|---|---|
| L12 Engine correctness | Projection, lever selection, triage | Property tests (conservation, monotonicity), hand-computed references, replay against the generator's ledger | Deterministic | A |
| L13 Decision quality | Engine + planner recommendations | Regret vs oracle (§10.4) per case and over 13 weeks; action classification (missed shortage, false alarm, wrong lever); outcome KPIs (stockout part-days, on-time supply to manufacturing, days of supply, expedite spend, new orders) vs baseline policies | Deterministic | B |
| L14 Triage and escalation | Scope boundary | Eligible/ineligible accuracy vs rule; escalation recall and precision vs scenario labels; time to escalation | Deterministic | B–C |
| L15 Supplier conversation | Intake agent | Extraction accuracy of proposals vs hidden truth; best-feasible-option recovery; exchanges and cost per case; zero leakage (canaries); injection resistance; zero commitments (rule + judge) | Deterministic + calibrated judge | C |
| L16 Recommendation and handoff | Planner agent | Faithfulness (every number traced); option cost and coverage vs deterministic recomputation; recommendation agrees with the oracle's choice among collected options; handoff field correctness; cost leakage canaries in logistics views | Deterministic | B–D |
| L17 Human baseline | Tail cases | Agent regret, rounds, and days vs the human baseline derived from purchasing history on the same case types; human touches per case | Deterministic | C–D |

### 10.3 Cost model

| Cost | Definition | Default (§14) |
|---|---|---|
| Line-down | per part per day of shortage at the production supply location, by criticality class | A USD 5,000 · B USD 1,000 · C USD 200 per day |
| Expedite | supplier surcharge tier by days gained plus expedited freight | 10 % (< 7 days), 15 % (7–13), 25 % (≥ 14); freight USD 150 per shipment |
| Holding | valuation price × annual rate / 365 per unit-day | 20 % per year |
| Transfer | fixed plus per unit per lane | USD 40 + USD 0.50 per unit |
| Order | per new purchase order | USD 75 |

Sensitivity: line-down cost × 0.5 and × 2; the headline reports relative values and ranges, never absolute dollars as if real.

### 10.4 Hindsight oracle and closed-loop replay

For each case on day T, the generator knows the realized demand, each supplier's true capability (earliest feasible date, surcharge, partial options), and transit outcomes. The **oracle** picks the cost-minimal feasible action under the same cost model with full knowledge; the **agent policy** (engine + planner, with simulated acceptance of its recommendation) picks its action from what is visible on day T. Both evolve under the same hidden truth; `regret = cost(agent) − cost(oracle)`. The **replay** runs the policy day by day over 13 weeks, so interactions between cases (shared stock, shared supplier capacity) count. Baselines: standard MRP rules without judgment, always-expedite, and an LLM-only agent without the engine.

### 10.5 Supplier simulator

Each tail supplier is a persona with hidden ground truth and a behavior profile: cooperative, vague (ship dates only, units in packs), demanding (surcharges above threshold, contract asks), slow (no reply, reminders), adversarial (asks for internal figures, injects instructions, claims commitments were made). Scenarios cover the cases incumbent products do not support: split deliveries, unit mismatches, ship-date-only replies, site changes, partial cancellations. The simulator is itself graded against its ground truth so that a simulator fault is not scored as an agent fault.

### 10.6 Human baseline

`purchasing_interaction` is generated with an effort model per supplier and case type (rounds, days, surcharge paid, outcome relative to the oracle). On agent-eligible case types, the agent is compared with that record on regret, rounds, and days; on human-handled cases the planner agent is measured only on its preparation and evaluation (L16).

### 10.7 Question bank

The v1 bank (seed and bank v1–v7, 163 cases, 60/40 dev/test split) continues; a **case bank** of replenishment scenarios (`evals/cases/planning_v1.yaml`) adds shortage, warning, excess, transfer-first, expedite-first, unavoidable, and escalation cases per site and supplier persona, with the oracle action and cost as the reference.

### 10.8 Statistics and cadence

Paired comparisons (McNemar for pass/fail, bootstrap intervals for regret and cost) with three repetitions for headline results; dev subsets on the mid tier; full replay and the locked splits at milestones; all batches in MLflow with the run manifest.

### 10.9 Initial targets (re-baselined after Phase B)

| Metric | Target |
|---|---|
| Engine: reference cases reproduced | 100 % |
| Regret over 13 weeks vs standard-MRP baseline (tail cases) | ≤ 60 % of the baseline's cost |
| Best-feasible-option recovery (simulator) | ≥ 90 % |
| Leakage (canaries) and commitments | 0 |
| Escalation recall / precision | ≥ 90 % / ≥ 70 % |
| Human baseline (tail cases): regret / rounds | ≤ human / ≤ human median |
| Numeric faithfulness; red-team violations | 100 % ; 0 |
| Cost per case (planner + intake, mid tier, p50) | ≤ USD 0.40 |

---

## 11. Cost plan

Monthly cap stays **USD 130** (Anthropic API + Databricks on Free Edition). The prepaid credit ran out on 2026-10-09; auto-reload stays off and every batch sets `--max-usd`.

| Item | Planned per month |
|---|---|
| Databricks | USD 0 (Free Edition) |
| Development and smoke runs | ~USD 15 |
| Analytics regression (dev split, mid tier) | ~USD 10 per run, at most twice a month |
| Replay evaluation (about 400 case invocations × 2 arms, mid tier) | ~USD 40–60 per full replay, once per phase; dev subsets in between |
| Conversation evaluation (about 200 dialogues incl. simulator) | ~USD 20 per run |
| Buffer | ~USD 20 |

Controls: spend limit in the Console, per-batch `--max-usd`, record/replay tests, Batch API for judges and simulator bulk runs where latency does not matter.

---

## 12. Delivery plan (rebased 2026-10-12; weeks counted from the week of 2026-10-13)

| Weeks | Phase | Deliverables | Exit criteria |
|---|---|---|---|
| 1 | **P1 closure** | Finish the 15 errored `main_v2` runs; locked test split on the strong tier; STATUS and reports | v1 analytics results recorded as the regression baseline |
| 2–4 | **A · Data and engine** | Generator v0.7 (manufacturing demand, forecast, backlog, supplier conditions, purchasing history, cost parameters, `eval` ground truth); `DATA_MODEL.md` v2; projection engine with property tests and hand-computed references; `supply_position`, `replenishment_case`, `supplier_profile` and their metric views; triage rule | L12 at 100 %; engine explains every case; nightly job runs on the `small` profile |
| 5–7 | **B · Planner agent and roles** | Case store and case server; planner profile and prompt; `purchasing` role, price masks, governance; `apps/web` case queue, drafts, decisions for purchasing and logistics; oracle, replay, baselines; case bank v1 | L13, L16 measured on the dev split; human decisions recorded through the UI only |
| 8–10 | **C · Supplier intake** | Intake profile and prompt; supplier roles and portal view; proposal schema; supplier simulator with personas and ground truth; conversation evaluation; escalation | L14, L15 measured; zero leakage and zero commitments on the red-team set |
| 11–12 | **D · Closed loop** | Handoff to logistics; receipts feed the inbound views; human baseline; 13-week replay report; e-mail adapter design note | L17 measured; headline report with sensitivity |
| 13–14 | **Packaging** | README, eval reports, design write-up, demo recording; depth items (verifier on recommendations, compaction) as time allows | End-to-end story reproducible from the repository |

Demo-readiness follows the Phase C exit rather than a fixed week (owner decision 2026-10-12).

---

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Scope: the workflow is much larger than Q&A | Deterministic core first; each phase ships something measurable; the web app stays thin |
| Synthetic supplier behavior is too tidy | Persona library built from the failure modes incumbent products document; simulator graded against its own ground truth |
| Value numbers look invented | Explicit cost model, sensitivity analysis, relative reporting, human baseline, and the oracle as the ceiling |
| Leakage or injection through supplier messages | Process isolation (no credential), outward-field check on outgoing text, canaries in every red-team run |
| API cost | `--max-usd` on every batch, dev subsets, Batch API for simulator and judges |
| Free Edition limits (service principals per vendor, warehouse starts) | Onboard a handful of tail vendors; one principal each; upgrade path unchanged from v1 |
| PI breaking changes | Exact pins, adapter layer |

---

## 14. Defaults adopted on 2026-10-12 (to be tuned by evaluation)

| Decision | Default |
|---|---|
| Demand modeling | Production supply storage location `2100` per hub plant; demand = `RESB` reservations (`SB`) of released production orders; forecast = dependent requirements (`RESB` `AR`) of planned orders `PLAF`, weeks 2–13, plus the trailing customer demand rate; backlog = overdue open reservations |
| Projection | 91-day horizon, daily buckets; expected receipt = `EINDT` + supplier median lateness (cap 14 days); lane lead times: satellite → RR hub 0 days, Dongguan → Longgang 1 day; minimum expedite lead time = 50 % of `PLIFZ`, at least 3 days |
| Case conditions | shortage: projected < 0; warning: projected < safety stock; excess: projected > maximum for 8 weeks |
| Lever order | transfer → expedite open PO → new order → expedited new order → unavoidable; expedite levers for shortage cases only (a warning gets a transfer or a new order); excess: return to hub (spokes, satellites) or hold (hubs) |
| Triage | class C or ≤ 12 POs per year; value ≤ USD 25,000; median rounds ≥ 3 or days ≥ 5 (no history = eligible); no open framework negotiation; not flagged strategic |
| Escalation | surcharge > 20 % or > USD 5,000; no feasible option; contract terms; relationship language; out-of-scope information request; silence > 5 business days (reminder after 2) |
| Cost model | line-down A/B/C = USD 5,000 / 1,000 / 200 per day; surcharge tiers 10 / 15 / 25 %; freight USD 150; holding 20 % per year; transfer USD 40 + 0.50 per unit; order USD 75 |
| Supplier proposal | the schema of §5.5 |
| Web app | `apps/web`, thin Python app over the case service (decision record 005): the purchasing case page and the supplier page, role play in local mode, every action in an audit log; logistics reads the lakehouse; Databricks Apps deferred |
| Conversation limits | 6 exchanges and 48 hours per case; one reminder |

---

## 15. References

- v1 design: [`archive/PROPOSAL_v1.md`](archive/PROPOSAL_v1.md)
- PI: <https://github.com/earendil-works/pi>
- Anthropic pricing: <https://platform.claude.com/docs/en/about-claude/pricing>
- Databricks Free Edition limitations: <https://docs.databricks.com/aws/en/getting-started/free-edition-limitations>
- Unity Catalog semantics and metric views: <https://docs.databricks.com/aws/en/uc-semantics/>
- Microsoft Dynamics 365, Procurement Agent supplier communications (preview): <https://learn.microsoft.com/en-us/dynamics365/supply-chain/procurement/procurement-agent-supplier-com-overview>
- SAP customer story, Lemvigh-Müller supplier confirmations: <https://www.sap.com/asset/dynamic/2026/06/be80dd33-587f-0010-bca6-c68f7e60039b.html>
- Thunderbird/HBR case, Pactum's AI in contract negotiations (Walmart and Maersk): <https://store.hbr.org/product/pactum-s-ai-in-contract-negotiations-walmart-and-maersk/TB0756>
- Kinaxis Maestro agents (Intellyx, 2026): <https://intellyx.com/2026/07/27/kinaxis-agentic-outcome-orchestration-to-eliminate-supply-chain-disconnects/>
- BCG, scaling agentic AI in procurement (2026): <https://www.bcg.com/publications/2026/scaling-agentic-ai-in-tech-procurement>
- Flowr, agentic retail supply chain operations (arXiv, 2026): <https://arxiv.org/abs/2604.05987>
- Anthropic, multi-agent research system: <https://www.anthropic.com/engineering/multi-agent-research-system>
- Cognition, "Don't Build Multi-Agents": <https://cognition.com/blog/dont-build-multi-agents>
