# 007. Rank proposals by expected cost; a C part's late offer competes on it

- **Status:** accepted
- **Date proposed:** 2026-10-20
- **Date decided:** 2026-10-20
- **Decided by:** the owner
- **Scope:** case server (purchasing's ranking of proposals and the recommended proposal), case page, planner agent (through a prompt change); amends record 004 (answer 4, the recommended proposal) and closes record 002 phase 4

## Context

Purchasing's ranking of supplier proposals was rule (a) of 2026-10-19 (failure card `roleplay-20261019-C-US10-40001795-SH-20261122`): inside the mandate first, then by the number of mandate fields missed, then by total cost; the recommended proposal was the cheapest inside the mandate (record 004, answer 4). An offer that misses only the latest arrival date, like Blue Fen's cheaper split in the role play, ranked below every offer inside the mandate however little its lateness risked.

Record 002 phase 3 gave every option a calibrated stock-out risk and an expected line-down cost (engine 1.1.0); phase 4 replayed three rules against the strict rule on the 2,908 order decisions of the backtest's held-out half, priced on the demand that followed (`evals/reports/engine-risk-replay-small.md`). Record 002's bar: regret per case below the strict rule's, with no more A- or B-class line-down days.

## Options

1. **Keep the strict rule.** No change; the role play's cheaper split stays second however small its risk.
2. **Expected cost, late offers for C parts only.** Rank by expected total cost (option cost plus expected line-down); the proposals inside the mandate and, for C parts, those that miss only the arrival date compete on it; A and B parts keep the arrival check. Replay: regret 81 against 644 USD per case with the engine's levers, 406 against 1,462 with a 3 % cheaper late split on every case, 986 against 2,916 at 8 %, with the strict rule's A- and B-class line-down days in every variant (122 and 306) and more C-class days (+66 and +122 with the split). Passes the bar.
3. **Expected cost for every part.** The cheapest in the replay (regret 125 and 136 per case with the split) but two more B-class line-down days: misses the bar narrowly.

A probability threshold (the cheapest option within the service target) was also replayed and failed: to bring lumpy C parts within 10 % it buys expedited orders, at almost three times the strict rule's regret.

## Decision

Option 2, chosen by the owner on 2026-10-20 on the assistant's recommendation.

- The case server ranks a case's proposals by expected total cost where its demand model prices the risk: first the proposals inside the mandate and, for a C part, those whose only missed field is the arrival date, by expected total cost; then the others by the number of fields missed and expected total cost. The recommended proposal is the first when it is one of the former; without one, there is none.
- Where the risk is not priced (no qualified demand model: no outflow in the last 13 weeks), rule (a) applies unchanged.
- The mandate itself is unchanged: its fields are still checked and shown, and a C part's late offer is marked "arrival, priced by risk" rather than hidden. The service targets (A 1 %, B 3 %, C 10 %) stay reference values shown next to each risk; they are not a filter.
- The engine's options and doing nothing remain reference rows; purchasing may choose any option, and every decision is still a person's.
- The planner's prompt (the `rank_options` description and the evaluate instructions name the old rule) changes through `PROMPT_CHANGES.md`: failure card, the owner's diff review, a paired measurement. Until then the planner submits the proposals in the case server's order (the refusal of another order guarantees it) but may explain them with the old wording.

## Acceptance

- Case server: unit tests with hand-computed expected totals (a C part's one-day-late offer ranks first; the same case as a B part keeps the in-mandate offer first; cases without a priced risk keep rule (a)).
- The planner-evaluate suite still submits every proposal in the case server's order.
- Phase 4's replay stays the reference; a later replay with real supplier answers (the supplier simulator of Phase C) checks the rule again.

## Consequences

The case page's ranking, its "recommended" proposal, and the decision form's default follow the new order. A C part's offers late on arrival can now be recommended; A and B parts are unaffected. The cost of a C-part line-down day (200 USD) and the calibration curve now steer recommendations directly, so a change to either is a change to purchasing's ranking.

## Evidence

- `evals/reports/engine-risk-replay-small.md` (phase 4), `evals/reports/engine-risk-small.md` (phase 3), `evals/reports/forecast-accuracy-small.md` (calibration).
- Record 002 (accepted, amended 2026-10-19) and the role play of 2026-10-19.
