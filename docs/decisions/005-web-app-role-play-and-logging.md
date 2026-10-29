# 005. Build the purchasing and supplier pages as a thin Python web app with role play, a simulation driver, and full logging

- **Status:** accepted
- **Date proposed:** 2026-10-17
- **Date decided:** 2026-10-17
- **Decided by:** the owner
- **Scope:** application (`apps/web`), case store (attachments, mandate fields), evaluation (flow suite), configuration

## Context

Phase B needs the two pages that `PROPOSAL.md` §3.1 and §12 list for `apps/web`: the case queue with drafts and decisions for purchasing and logistics, and the supplier view. The owner wants to play both roles end to end (approve a request, set a mandate, answer as a supplier with an Excel sheet, decide, hand off, confirm receipt), wants the assistant to run the same flow automatically for validation, and wants every final decision, every message between the agents and the people or suppliers, and every Excel attachment kept in logs, traces, or Databricks tables.

What exists: the case server (`case_server/`, Python) with the state machine, the 18 event types, the actors and roles allowed per event, the payload schemas, a local store and the lakehouse store (`cases.case_event`, `cases.supplier_message`, `cases.supplier_proposal`), `explain_case` and `evaluate_option`, the outgoing-text check, and a command-line stand-in for the buttons (`snm-cases act`); the planner agent in the TS harness, run headless (`npm run case`), writing `runs/<run>/trace.jsonl` and `result.json`, uploaded to MLflow by `snm-evals mlflow-upload`; record 004 (accepted) with the mandate the purchasing page must show. What does not exist: any HTTP server or page, a supplier login, an attachment store, the mandate fields, the `within_mandate` flags, an automated driver of the human and supplier sides.

## Options

1. **Change nothing: keep the command line.** `snm-cases act` already records every human event; the owner could play the roles from a terminal. No pages, no Excel, no supplier view; the flow cannot be shown to anyone and the supplier side has no isolation to test.

2. **TS `apps/web` as written in the proposal.** A Node server with server-rendered pages, calling the case server over MCP stdio and the harness in process. Consistent with the proposal's language choice; costs a second client of the case server (the harness already has one for the planner's tools, the pages need the human events), a TS Excel parser, and a TS simulation driver that duplicates what the Phase C supplier simulator (Python, per §12) will do.

3. **Thin Python web app** (recommended): `apps/web` as a FastAPI application with server-rendered Jinja2 pages and small HTMX interactions, importing `snm_cases.service` directly (the pages are one more caller of the single write path; nothing bypasses the state machine), launching the agents through the headless harness CLI exactly as the planner evaluation does, parsing Excel with `openpyxl`, and sharing its driver code with the Phase C supplier simulator. Two languages in the product, but the app stays thin and the Python side already owns the case store, the evaluation, and the simulation. Deviates from the proposal's "TS `apps/web`" line, hence this record.

4. **Databricks App.** Deferred by the proposal (§12 packaging row); the Free Edition and the role principals are not ready for it, and the role play must work offline against the local store.

## Design under option 3

**Pages.**

- `/cases` (role `purchasing` only; decided on the owner's answer 4): the queue of order cases (shortage and warning) with filters by condition, triage, and state; transfer cases (pull, excess) appear read-only as what was published to logistics. `/cases/<id>`: the engine's explanation (`explain_case`), the options with their costs (`evaluate_option`, commercial columns only for roles with cost access), the planner's draft, the **mandate form** at request approval (engine defaults prefilled: latest arrival, quantity range, surcharge and freight ceilings, validity, allowed kinds; editable; record 004), the conversation (messages and proposals with their per-field `within_mandate` flags, the recommended proposal first), attachments, and the buttons that are exactly purchasing's human events of the state machine: send request (with the mandate), record decision (accept a proposal, choose another option, decline), cancel, override triage, add note, and "run the planner" / "run the intake agent" (the agents never run unasked in the app). Hand-off and receipt are not buttons (answer 4): the system appends `handed_off` when a decision is recorded or a transfer is prepared, and `receipt_confirmed` when the data shows the receipt.
- `/supplier` (role `supplier_<lifnr>`): the vendor's open requests with the **outward fields only** (part, description, quantity, needed date, purchase-order line, request type), the request's Excel sheet to download, a reply form (free text, structured fields per option, or the filled sheet to upload), and the vendor's own message history. No internal figure reaches this page; the server renders it from `outward_fields`, not from the case.
- **Role play.** In local mode the app runs without authentication and shows a role switch (purchasing and every onboarded supplier); every action is appended with that actor's role, so the state machine, not the page, decides what is allowed. In lakehouse mode the role comes from the login (later: Databricks principals).

**Excel exchange.** The request to a supplier carries an `.xlsx` generated from the outward fields (one template: header block with the request, one row per option for the supplier to fill: quantity, ship date, arrival date, unit price, surcharge, freight, valid until, split deliveries, conditions). The supplier uploads the filled sheet; the app validates it against the template (`openpyxl`), stores the file, records one `supplier_proposal` row per filled option with `within_mandate` flags, keeps free text verbatim, and shows parse errors to the supplier instead of guessing. The intake agent (Phase C) reads the parsed rows and the message text, never the file.

**Logging, tracing, tables.** Every action is already an append-only event in `cases.case_event` (actor kind and role, state after, facts, payload, run id, time); messages in `cases.supplier_message`; proposals in `cases.supplier_proposal`. Added: `cases.supplier_attachment` (attachment id, case, message, vendor, file name, content type, size, SHA-256, the volume path, the parsed rows as JSON, who uploaded it, when), with the file itself in a Unity Catalog volume (`snm.cases.attachments/<case>/<attachment>.xlsx`; a local directory in local mode), and the mandate as a `mandate_set` event with its own payload schema. Agent runs keep `runs/<run>/trace.jsonl` and `result.json` and go to MLflow as today; the app writes one audit line per HTTP action (`runs/web/access.jsonl`: time, actor, route, case, event appended or refusal). The final decision is the `decision_recorded` event; nothing else is the record.

**Simulation driver.** `snm-sim flow` (Python, in `evals/`) plays the engine, the planner, purchasing, the system's hand-off and receipt, and scripted suppliers through the same service calls the pages use: open cases from a planning run, run the planner, approve with the engine's mandate, answer as a supplier (inside the mandate, outside on one field, decline, with or without a sheet), decide, then the system hand-off and the receipt from data. It checks the invariants after every step (append-only, legal transition, actor allowed, every decision by a human actor, no internal figure in outbound text, every attachment stored and parsed) and writes a report. The Phase C supplier simulator replaces its scripted suppliers with personas; the conversation evaluation of record 004 runs on the same driver.

## Owner questions

1. Stack: option 3 (Python FastAPI, Jinja2, HTMX, `openpyxl`), accepting the deviation from the proposal's TS line?
2. Role play first in local mode without authentication, lakehouse mode later behind the role principals?
3. The Excel template: one sheet, header block plus one row per option with the columns listed above; conditions as free text, 500 characters?
4. The logistics endpoint (site managers) uses the same case page filtered by role, with the commercial columns masked, rather than a separate page? (Answered: no page at all.)

Answers (owner, 2026-10-17):

1. Yes, option 3.
2. Yes, local role play first.
3. Yes, that template.
4. **No page for logistics at all.** Logistics learns the outcome and plans its work from the Databricks tables and metric views (`curated.replenishment_case`, `mv_replenishment_case`, the `cases` tables); the pages are for purchasing and for suppliers only. Consequently the hand-off and the receipt are not buttons: a recorded decision is published to the lakehouse (the `handed_off` event is appended by the system right after `decision_recorded`), a prepared transfer case is handed off the same way, and the case closes when the data shows the goods receipt or the transfer (a system `receipt_confirmed`, from the engine sync; until that sync exists the simulation driver plays it).

## Decision

Option 3 with the owner's answers: a thin Python web app (`apps/web`, FastAPI, Jinja2, plain forms, `openpyxl`) with two surfaces, the purchasing case page and the supplier page, played by the owner in local mode without authentication and later behind the role principals; logistics has no page and reads the lakehouse; hand-off and receipt are system events driven by the decision and by the data; one Excel template for the supplier exchange; every action an event, every message, proposal, and attachment a row, the file in a volume; a Python driver plays all sides for validation.

## Acceptance

Draft, to be fixed with the decision:

- Role play: the owner runs one case end to end in local mode (planner prepares, purchasing approves with a mandate, the supplier answers with an uploaded sheet, the proposal is recorded with `within_mandate`, purchasing decides, the system hands off and, once the receipt is in the data, closes the case) and every step is visible as an event, message, proposal, or attachment row.
- Driver: `snm-sim flow` on 20 cases of the `small` planning run with zero invariant violations; the report lists every decision with its human actor.
- Lakehouse: the same flow against the lakehouse store writes to the four `cases` tables and the volume; `snm-cases list` and the metric views show the cases.
- Evaluation: a `flow_v1` suite (scripted scenarios run by the driver) scored on: every decision by a human actor (100 %), zero leaks in outbound text, every attachment stored and parsed, the planner's draft right per `evals/src/snm_evals/planner.py`.
- Tests: app routes (role gating, outward-only supplier page, Excel parse errors), attachment contract, mandate payload schema; `PROPOSAL.md` §3.1 and §5.5, `DATA_MODEL.md` §7a updated.

## Consequences

A second Python package (`apps/web`, `snm-web`) with FastAPI, Jinja2, and `openpyxl` as dependencies; the case model gains `mandate_set` and the attachment table; the proposal schema gains `within_mandate`; the harness gains nothing (the app calls its headless CLI). The supplier page is the first external-facing surface, so its template must never receive a case object, only `outward_fields`, and a test asserts it. Databricks Apps stay deferred. Open: the login for lakehouse mode (role principals are not provisioned yet, `STATUS.md` pending actions).

## Evidence

- `PROPOSAL.md` §3.1 (components), §5.5 (intake agent), §7.2 (policies), §12 (phase plan).
- Records 003 and 004.
- `case_server/src/snm_cases/model.py` (states, transitions, payloads), `evals/src/snm_evals/planner.py` (how the planner is driven headless today).
