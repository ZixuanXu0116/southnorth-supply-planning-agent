# 003. Model the warehouses as main warehouses that supply manufacturing, with overflow hubs

- **Status:** accepted
- **Date proposed:** 2026-10-16
- **Date decided:** 2026-10-16
- **Decided by:** the owner
- **Scope:** data model and generator, engine, planning tables, knowledge base, evaluation bank, design documents (`PROPOSAL.md` §1, §4; `DATA_MODEL.md` §1)

## Context

On 2026-10-16 the owner stated that the system's picture of the business is wrong. The owner's picture, in their words translated:

> The hub is a cheaper, larger warehouse where we keep what is not needed right away. The spoke is our real main warehouse: when the company's own manufacturing department tells us what it needs, we scan and ship from the main warehouse. If the main warehouse is short, it is replenished from the hub; if the hub does not have it either, we place a new order with the supplier or expedite an order that is placed but not yet delivered. There are no external customers. Our relationship with suppliers is to get more, or to get it sooner.

The owner's answers to the first clarifying questions (2026-10-16):

1. External customers: none at all; every goods issue goes to the manufacturing department.
2. Supplier deliveries arrive at the hub or at the main warehouse; both happen.
3. Longgang and Round Rock are the main warehouses; the other locations (Dongguan, the Round Rock satellites) are hubs.
4. Manufacturing demand reaches the warehouse as the SAP **CO27** data (the picking list of component requirements for production orders), replicated to Databricks every 30 minutes; for this project that is as good as real time.
5. A shortage is judged on the projection over the next three months: the stock of every location plus the orders that are placed and will arrive, against the backlog of unmet past demand and the demand of the next three months.
6. A hub has no shortage of its own: when the main warehouse is short it is replenished from the hub; when the two together are not enough, the orders are looked at.
7. This decision has the highest priority.

What the system assumes today (`DATA_MODEL.md` §1.1, unchanged since v1 and carried into PROPOSAL v2):

| Topic | Today | The owner's model |
|---|---|---|
| Role of `1000` at Longgang and Round Rock | the hub: supplier receipts, EWM, AutoStore and RFID, shipments to external customers, staging to the assembly building, replenishment of the spoke and the satellites | the **main warehouse**: the issue point for manufacturing |
| Role of Dongguan `3000` and the Round Rock satellites `2001`–`2003` | spokes and satellites with their own customers, replenished from the hub by storage-location MRP (reorder point `LMINB`, replenishment quantity `LBSTF`) | **overflow hubs**: cheaper storage with no demand of their own, pulled from when the main warehouse is short |
| Demand | external customers (sales orders `VBAK`/`VBAP`, outbound deliveries `LIKP`/`LIPS`, waves, picks, goods issue `601`, proof of delivery) plus, since generator 0.7.0, the manufacturing department (production orders `AFKO`/`AFPO`, component reservations `RESB`, staging `311` to the production supply area `2100`, consumption `261`) | the manufacturing department only (CO27 component requirements) |
| Transfers | hub pushes to spoke and satellites when their reorder points fire; the engine's transfer lever pulls surplus from a spoke or satellite back to the hub, leaving the location its reorder point plus 14 days of its own demand | overflow hub to main warehouse, on demand, with nothing held back |
| Supplier receipts | at the hub (`101` into `1000`, EWM inbound) | at the main warehouse or at an overflow hub |
| Export control | shipments to foreign customers need licenses (scenarios A5, A6; `trade_compliance` role; 14 evaluation cases) | no external shipments; meaningful only if a cross-border flow between the two sites exists |
| The engine | projects the hub's stock alone against customer and manufacturing demand; shortage below zero, warning below safety stock; levers in the order transfer-to-hub, expedite, new order, expedited new order | projects the plant's pooled stock (main plus overflow) against manufacturing backlog and demand over three months; a main-warehouse shortfall with hub stock is a transfer; a pooled shortfall is an order or an expedite |

How much depends on the external-customer concept, measured on the repository (2026-10-16):

- Evaluation bank (186 cases): 13 mention customers, 11 countries or destinations, 14 export control or licenses (overlapping, about 25–30 cases in all); 8 use "hub" and 8 "spoke" or "satellite" in a sense that flips; 63 concern deliveries, goods issue, or on-time delivery and 36 waves, picks, or warehouse tasks, which survive if deliveries become internal deliveries to manufacturing.
- Knowledge base (43 pages): 12 mention customers, 5 export control.
- Generator: `outbound.py` (1,280 lines: sales orders, deliveries, waves, picks, goods issue, proof of delivery), `trade.py` (256), the customer master in `master.py`.
- Contracts: `kna1`, `knvv`, `vbak`, `vbap`, `vbuk`, `vbup`, `likp`, `lips`, the `export_*` and `trade_*` extracts, `curated.fct_outbound_delivery`, the outbound metric views.
- Engine and planning tables: inputs, projection, lever rule, `supply_position`, `replenishment_case`, the oracle's realized outflow, the replay, bank v8's planning cases, the lakehouse case store.

Not affected: the tool server and SQL guard, governance (roles, row filters, masks; site keys stay plant and storage location), the case server and case store design, the planner agent and harness, the evaluation infrastructure, the inbound, putaway, inventory, AutoStore, RFID, and workforce parts of the data.

## Options

1. **Change nothing.** The engine answers a question the owner does not ask (will the hub run out, with customers that do not exist). Rejected by the owner's statement.

2. **Re-describe without regenerating.** Rename the roles in the documents (main warehouse, overflow hub), keep the data. Rejected: the data would still contain foreign customers, export holds, and hub-to-spoke replenishment; every document would contradict every table.

3. **Re-target the flows and the engine; keep the structures.** SAP's structures already express the owner's model, so the change is to what moves where, not to the tables:
   - *Demand*: the manufacturing department only. Outbound deliveries are created from production orders' component requirements (what CO27 lists: `RESB` reservations of released orders) and go to the production supply area; waves, picks, tasks, AutoStore and RFID stay as they are, because the warehouse still picks and ships, now internally. Sales orders, the customer master, and proof of delivery by carrier are removed.
   - *Network*: `1000` is the main warehouse; `3000` and `2001`–`2003` are overflow hubs with no demand of their own. Supplier receipts land at the main warehouse or at an overflow hub. The main warehouse pulls from its overflow hubs when short (`311`/`313` toward the main warehouse); stock above the main warehouse's maximum is moved out to the overflow hub (the former excess case becomes "move to overflow").
   - *Engine 1.0*: one projection per material and plant over 91 days on the pooled stock of the main warehouse and its overflow hubs, in-transit, and open orders at their expected dates, against the backlog of unmet component requirements and the next three months of demand (released orders' requirements as firm, planned orders as forecast, and a rate for what neither covers). Shortage and warning keep their definitions on the pooled line. Levers: when the main warehouse alone falls short but the pool does not, a transfer from the overflow hub (logistics); when the pool falls short, expedite, new order, expedited new order (purchasing); expedites only against a projected stock-out (record 001 still holds). Supplier receipts at an overflow hub reach the main warehouse by transfer.
   - *Scenarios*: A3 becomes "the main warehouse's pull parameters too low"; A4 (wave policy) and A1, A2, A6–A8 stay where they concern warehouse operations; A5 and the export tables are dropped unless a cross-border inter-site flow is added (owner question 1).
   - *Documents, KB, bank*: `PROPOSAL.md` §1 and §4 and `DATA_MODEL.md` §1 rewritten to this model; KB site and concept pages rewritten; the customer and export cases of the bank dropped, the hub and spoke wording rewritten, manufacturing-delivery cases added (bank v9); every reference answer refreshed because the data regenerates.
   Estimated effort at the pace of the previous increments: three to four weeks, in the increments listed under Consequences.

4. **Rebuild the domain from scratch** with new tables. Rejected: the SAP structures fit the owner's model already, and a rebuild discards the measured v1 work (guard, governance, metric views, 186 cases, agent arms) for nothing the data model needs.

Recommendation: option 3, in increments, documents first.

## Owner questions

1. With no external customers, does any cross-border flow remain that keeps export control meaningful, for example parts moving between Round Rock (US) and Longgang (CN)? If yes, trade compliance stays on that flow; if no, scenarios A5 and A6, the export tables, the `trade_compliance` role, and 14 evaluation cases are dropped.
2. Terminology: "main warehouse" for Longgang `1000` and Round Rock `1000`, "overflow hub" for Dongguan `3000` and the three Round Rock satellites, in every document, KB page, and question; storage-location codes unchanged. Agreed?
3. The warehouse still runs waves, picks, and goods issue for the internal deliveries to manufacturing (so pick productivity, wave lead time, AutoStore, and RFID keep their meaning), and the picking list (CO27) is what starts them. Correct?
4. The overflow hubs have no reorder logic of their own: the main warehouse pulls when it is short, and stock above the main warehouse's maximum is moved out to the overflow hub. Correct? Who decides the move out: logistics, as a case, or automatically?
5. Dongguan serves Longgang only; the three satellites serve Round Rock only. Correct?
6. The evaluation bank: drop the customer and export cases and add manufacturing-delivery cases in a new suite (bank v9), rather than rewriting each old case. Agreed?
7. The timeline: three to four weeks for this change, after which Phase B continues (the 13-week plan of `PROPOSAL.md` §12 slips by that much). Agreed?

Answers (owner, 2026-10-16):

1. No cross-border flow. Simplify: the agentic workflow covers Round Rock and its satellites only; Longgang and Dongguan are not done for now.
2. Agreed.
3. Correct.
4. Correct: pull when the main warehouse is short, move out when it is above its maximum, a min-max system. The move to the hub is a recommendation generated by our code, reviewed by a person (human in the loop), and then triggered.
5. Correct, and for this project only the Round Rock main warehouse and its satellite 1 (`2001`); the other satellites are out of scope.
6. Agreed.
7. The time must be short: every change for this decision is to be implemented and finished today.

## Decision

Option 3, scoped and sequenced by the owner's answers. The business model is: each site has a main warehouse that issues to the company's manufacturing department and overflow hubs that hold what the main warehouse does not need right away; there are no external customers; the main warehouse pulls from its overflow hub when it is short, surplus above its maximum is moved to the hub after a person's review, and purchasing expedites or orders from suppliers when the main warehouse and the hub together are not enough; supplier deliveries arrive at either. The workflow covers Round Rock's main warehouse `1000` and overflow hub `2001` only. Terminology everywhere: main warehouse, overflow hub. The customer and export cases of the evaluation bank are dropped and manufacturing cases are added in a new suite. The engine, the planning tables, the case server, the planner, the knowledge base, the design documents, and the bank are changed on 2026-10-17; the generator's replacement of the legacy customer flows (generator 0.8) follows as the next increment because it cannot be finished in a day.

## Acceptance

Done on 2026-10-17 (engine 1.0, commit `a728e59` and the following):

- `PROPOSAL.md` §1 and §4, `DATA_MODEL.md` §1, the KB (`metric.projected_stock`, `guide.replenishment_planning`, `concept.internal_transfers`, `site.round_rock`, the glossary, the index), and the planning contracts describe the owner's model; the legacy customer flows of the generated data are named as such wherever they still appear.
- Engine 1.0: plants in scope `US10`; demand from reservations, planned orders, and the staging rate; two projected lines (pooled, main warehouse alone); conditions shortage, warning, pull, excess; moves as recommendations for logistics; expedites only against a stock-out. Hand-computed references for the pooled projection, the pull, the excess move, and the demand rate; 370 tests pass.
- Oracle and replay rerun on the new model with their reports rewritten; planner evaluation on the new run: 6 of 6 cases (new orders and moves to the overflow hub), USD 0.24.
- Evaluation bank: 23 cases retired (customers, export control, the Longgang planning case), 4 questions reworded to the new terminology; 163 cases remain; reference-answer files pruned, to be refreshed on the republished tables.
- Lakehouse: publication of the engine 1.0 tables, metric views, and governance check attempted; Databricks refused every connection on 2026-10-17 ("cannot create the resource"); the publish is retried automatically and completed in the next session if the day ends first, together with the reference-answer refresh and the case store sync.

Still open (next increments): generator 0.8 (customer flows removed, outbound deliveries created from the CO27 picking list, supplier receipts at both locations, pull and move flows, manufacturing demand sized to the main warehouse); bank v9 with manufacturing-delivery cases; the trade-compliance pages and role retired with the data; record 002 recomputed on the new projection.

## Consequences

- On the `small` profile the engine now opens 161 cases for Round Rock (10 shortage, 148 warning, 3 excess, no pull): generator 0.7 holds little stock at the overflow hub and sized manufacturing demand small against the main warehouse's stock, so the pull and move flows of the business model barely appear until generator 0.8.
- The replay's ground truth (emergency orders placed for customer demand) and the oracle's realized demand no longer measure what the engine does; both reports say so and wait for generator 0.8.
- Longgang's open cases in the lakehouse case store are cleared by the next sync (the engine no longer plans `CN10`); the v1 analytics agent keeps answering questions about Longgang and Dongguan data.
- The v1 analytics bank shrinks from 186 to 163 cases; the surviving cases that touch deliveries, waves, picks, and inventory remain valid on the legacy data and are refreshed, not rewritten.
- The `trade_compliance` role and its pages stay deployed until generator 0.8 removes the export data; no agent case exercises them.
- Record 002 stays on hold; its options survive, its evidence must be recomputed.

## Evidence

- The owner's statements of 2026-10-16 (chat; quoted above in translation).
- Repository counts of 2026-10-16 (this record, Context).
- `DATA_MODEL.md` §1.1 (the model being replaced).
