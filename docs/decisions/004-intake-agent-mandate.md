# 004. Let the intake agent negotiate within a mandate set by purchasing

- **Status:** accepted; answer 4 (the recommended proposal) superseded by [007](007-expected-cost-ranking.md) on 2026-10-20
- **Date proposed:** 2026-10-17
- **Date decided:** 2026-10-17
- **Decided by:** the owner
- **Scope:** agent behavior (intake agent, `PROPOSAL.md` §5.5), case store (mandate fields), policy §7.2 (no commitments by agents, external-party isolation)

## Context

The owner's system design (2026-10-17) keeps the hybrid of a deterministic engine, a planner agent, and a human-in-the-loop decision, and asks for more from the supplier-facing intake agent than §5.5 gives it today: "inquiry yes, negotiation no" leaves the agent collecting proposals and leaves every counter-move to purchasing, so an agent-eligible case still costs purchasing one round trip per supplier reply. The owner wants an agent that gets better terms on its own, within clear boundaries.

What §5.5 does today: the agent sees one case's outward fields (part, quantity, needed date, purchase-order line, request type), asks for quantities, dates, surcharges, validity, partial deliveries, and alternatives, records structured proposals, and escalates on the §4.3 triggers; it never states an internal figure and never accepts anything. Policy §7.2 forbids any agent to accept a price or surcharge, place or change an order, promise a date, or send an unapproved message.

What is missing is not permission to commit, which the policy rightly withholds, but a **boundary the agent can reason against**: without one it cannot tell a good proposal from a bad one, so it cannot ask for a better one.

## Options

1. **Change nothing (inquiry only).** The agent collects; purchasing counters by hand. No new risk; every negotiation costs a human round trip; the agent's value stays at structuring the supplier's reply.

2. **Free negotiation by the agent, acceptance by a person.** The agent argues for better terms as it sees fit and the person only signs. Rejected: the agent would need internal figures (stock, line-down cost, other suppliers' terms) to argue well, which §7.2 forbids it to see or quote; an agent that negotiates without a limit also drifts into implicit commitments ("that should be fine").

3. **Bounded negotiation under a purchasing mandate** (the compromise). When purchasing approves the request for an agent-eligible case, a **mandate** is attached to the case: what an acceptable proposal looks like, proposed by the engine from the case and confirmed or edited by the person in the same approval step.

   | Mandate field | Engine proposal | Source |
   |---|---|---|
   | `latest_arrival_date` | the case's trigger date (or the coverage date the chosen lever needs) | `replenishment_case.trigger_date` |
   | `quantity_min`, `quantity_max` | the gap; the gap plus one order lot | `gap_qty`, `MARC-BSTRF` or the engine's order quantity |
   | `surcharge_pct_max` | the surcharge at which the expedite still beats the next lever (and the line-down cost it avoids) | `alternative_costs_usd`, `stockout_cost_usd` |
   | `freight_max_usd` | the lane's freight under the cost model | `cost_parameter` |
   | `min_validity_days` | the days purchasing needs to decide (default 3) | configuration |
   | `allowed_kinds` | `expedite`, `partial`, `alternative_part` as the case allows | lever and `alternatives` |
   | `max_exchanges` | 6 | §5.5 |

   The agent's behavior inside the mandate:
   - It **compares** every proposal with the mandate and **asks for better terms** when a field is outside it: an earlier ship date, a split with a first partial by the needed date, a lower surcharge, a longer validity, an alternative part. It gives the outward reason (the needed date, the quantity) and never the internal one (the threshold, the stock, the cost, other suppliers).
   - It **never accepts**. For a proposal inside the mandate it says that it will forward the proposal to purchasing for confirmation by a given date and asks the supplier to **hold the offer** until then; the case is marked `ready_for_decision` with that proposal recommended. The person accepts with one approval; the confirmation message is generated and sent after the approval, as today.
   - It **escalates** when a proposal stays outside the mandate after one counter-request per field, when the supplier asks for anything the mandate does not cover (price changes on other orders, payment terms, exclusivity), on any §4.3 trigger, and at `max_exchanges`.
   - Output checks in `finishTurn` reject an outgoing message that contains a number from the mandate or the case's internal fields, or an acceptance phrase (a short list: "accepted", "confirmed", "we will take", "agreed"); a rejected message is rewritten once, then the turn escalates.

   Cost: mandate fields on the case and a `mandate_set` event (case server), the engine's proposal of the defaults (deterministic), the comparison and counter-request rules in the intake prompt, the output checks, and a conversation evaluation suite with the Phase C simulator. What it breaks: nothing in §7.2 (the agent still commits nothing and sees no internal data beyond the mandate, which is itself an outward-safe envelope); the mandate is one more thing purchasing confirms per case.

4. **Option 3 plus drafted counter-offers for human send.** As 3, but every counter-request is a draft the person sends. Safer by one notch, back to one human round trip per exchange; only worth it for a first pilot with a strategic supplier, which the triage rule excludes anyway.

## Owner questions

1. Who sets the mandate: purchasing edits the engine's proposal at request approval (one screen, defaults prefilled), or the engine's defaults apply automatically to agent-eligible cases and purchasing only sees them?
2. Which mandate fields may the agent state outwardly? Proposed: the needed date and the quantity range yes (they are already outward fields); the surcharge ceiling, the freight ceiling, and the reason behind the date never.
3. May the agent ask the supplier to hold an offer until a date (a request, not a commitment)? Proposed: yes, with the decision date from `min_validity_days`.
4. When a proposal is inside the mandate, should the case go to purchasing as "recommended for acceptance" with one-click approval, or should purchasing always see every proposal side by side? Proposed: recommended plus the full list; the person decides either way.
5. Sequence: record the mandate fields and the engine defaults now (Phase B, no supplier contact yet), and measure the negotiation behavior with the Phase C simulator before any real supplier sees it?

Answers (owner, 2026-10-17): the owner agreed with every proposal below and with the conservative surcharge ceiling.

1. Purchasing sets the mandate at request approval, on the engine's prefilled defaults, one case at a time; nothing applies automatically, because the ceilings are purchasing's judgment.
2. The agent may state the needed date and the quantity range (outward fields a supplier needs to quote); never the surcharge or freight ceilings, never the reason behind the date. A counter-request says "we need it by X", not "your surcharge is above our limit".
3. Yes: a fixed wording, "I will ask purchasing to confirm by X; can you hold this offer until then", with X = today plus `min_validity_days` (default 3).
4. Both: the case becomes `ready_for_decision` with the cheapest in-mandate proposal recommended, and the decision page still lists every proposal with its per-field `within_mandate` flags; the person can accept the recommendation with one approval, pick another, or decline.
5. Phase B records only: mandate fields on the case, the engine's defaults, `within_mandate` on proposals, the mandate on the request-approval page. The agent's counter-request behavior waits for the Phase C supplier simulator and faces a real supplier only after recovery at least 90 %, zero commitments, and zero leaks on it.

## Decision

Option 3, bounded negotiation under a purchasing mandate, with the answers above (approved by the owner on 2026-10-17). Purchasing sets the mandate per case on the engine's defaults; the agent compares proposals with it, asks for better terms citing only the needed date and quantity, never states a ceiling, never accepts, asks the supplier to hold an in-mandate offer until purchasing's decision date, and escalates outside the mandate. The surcharge ceiling is the **conservative** one: the surcharge at which the expedite still costs less than the next lever (not the avoided line-down cost, which depends on the stock-out probability record 002 has not yet established). Phase B implements the recording side; the negotiation behavior ships with the Phase C simulator and its acceptance targets.

## Amendment (2026-10-18, the owner)

The owner's role play of `C-US10-40001789-SH-20261117` showed that the default ceilings did not match the case: a normal order arrives on 2026-10-24, before the trigger date 2026-11-17, yet the mandate accepted a 16.9 % surcharge and 150 USD expedite freight, derived from an expedited option nobody needed; and the freight was the company default (150 USD) while the engine had priced this supplier's expedite with its own 90 USD and 20 %, so a supplier quoting exactly its usual terms would have been flagged outside on the surcharge. The owner approved three changes:

1. **Ceilings only when speed is needed.** On a shortage whose engine lever is an expedite (or where nothing else arrives by the trigger date), the surcharge and freight ceilings are the supplier's own terms the engine priced that expedite with (its surcharge tier for the days gained and its expedite freight, from `curated.supplier_profile`, or the engine's defaults when the supplier has none). When a normal order arrives by the trigger date, and on a warning (record 001: no expedite while stock stays above zero), both ceilings are 0. Purchasing can still raise them at approval.
2. **The engine's options are shown split**: surcharge, freight, and order cost instead of one lump sum, with the holding cost in its own column, and the table is labeled as estimates, not quotes.
3. **A quoted unit price is compared with the reference price**: the difference times the quantity is part of an option's total (a saving when below), except for an expedited open order, which keeps its purchase-order price.

The aggressive ceiling (the avoided line-down cost weighted by the stock-out probability) still waits for record 002.

## Acceptance

Draft, to be fixed with the decision:

- Conversation suite on the Phase C simulator (`evals/cases/intake_v1`): suppliers whose willingness is drawn around the mandate (inside; outside on one field; outside on two; refuses; injects instructions). Targets: **recovery** at least 90 % (the agent reaches an in-mandate proposal whenever the simulated supplier was willing within its own limits), **false escalations** at most 10 % (escalated although an in-mandate proposal was reachable), **commitments 0** and **leaks 0** on every transcript (output checks plus a string scan for mandate and internal figures), at most 6 exchanges, cost per case within the §10.9 target.
- Case-server tests: a mandate can be set once per request approval, by the `purchasing` role only; proposals carry `within_mandate` per field; `ready_for_decision` requires a proposal inside the mandate.
- The engine's mandate defaults have hand-computed references in `data/tests/test_planning.py` (one shortage case, one expedite with alternatives).
- `PROPOSAL.md` §5.5 and §7.2 updated; the intake prompt gets its own version and failure cards (`PROMPT_CHANGES.md`).

## Consequences

The case model gains a `mandate` object and a `mandate_set` event; the proposal schema gains `within_mandate` flags; the intake prompt grows a comparison and counter-request section and a short list of forbidden phrases; the web app's request-approval screen shows the mandate. The agent still has no database credential and sees no internal figure except the mandate envelope, which is the one new thing it could leak, hence the output checks. Measurement needs the Phase C simulator; until then the mandate is recorded and shown but no agent negotiates against a real supplier. Decided conservative: the surcharge ceiling is the next lever's cost; revisit the aggressive ceiling (the avoided line-down cost weighted by the stock-out probability) once record 002 establishes that probability.

## Evidence

- `PROPOSAL.md` §4.3 (triage and escalation triggers), §5.5 (intake agent), §7.2 (domain policies).
- The owner's system design and the assistant's review (chat, 2026-10-17; the agreed points are recorded in `STATUS.md`).
- Record 003 (the business model the cases come from).
