# Failure card: bank-v8-g080-dev-sonnet / c109

- **Status:** adopted (2026-10-19)
- **Batch, arm, repetition:** `bank-v8-g080-dev-sonnet`, `main`, rep 1
- **Prompt version and digest:** `main/v4`, `36b457a0d591e623`
- **Model and effort:** `claude-sonnet-5-5`, effort `medium` (run manifest)
- **Trace:** `runs/evals/bank-v8-g080-dev-sonnet/runs/c109-main-r1/trace.jsonl`
- **Suite and category:** `bank_v2` (via `bank_v8`), `trap`, traps: `transfers_not_demand`, `transfer_double_count`

## Expected

`answer` or `answer_with_assumption` with the quantity issued by Longgang `CN10/1000` in FY2026 P11: issues to manufacturing (`311` to `2100` net of `312`, 118,136) plus the transfers out to Dongguan (`313` issuing items only, 23,587), `hub_demand_qty` = 141,723 (rel 0.2 %).

## Observed

`answer_with_assumption` with delivery lines, not quantities (Longgang C100 4,235 lines on 215 deliveries) from `mv_outbound_shipped`, and the Dongguan replenishment explicitly left out although the question asks to include it; the scorer reports `hub_demand_qty=141723` missing. The same case passed on generator 0.7.0 (`bank-v8-dev-sonnet`).

## Decisive part of the trace

- Turn 1: `kb_read` `sites/longgang.md`, `concepts/internal_transfers.md`, `concepts/production_supply.md`; `list_metrics`.
- Turn 2 thinking: "I should exclude Dongguan replenishment transfers from demand, since demand really means lines shipped from C100".
- Turn 3 thinking: "'Demand' is ambiguous between lines and quantity, and quantity would need a separate query, so I'll report lines shipped"; `query_metric` `mv_outbound_shipped` (`lines_shipped`, `deliveries_shipped` by site).
- Turn 4: submits, stating "Under `concept.internal_transfers` they are not demand, so the 4,235 lines are only Longgang's issues to manufacturing".

## Root cause

`interpretation`. The agent read the right pages and the right data, but let a general rule override the explicit scope of the question, and answered "how much" with a line count. The rule it followed changed with generator 0.8: `kb/concepts/internal_transfers.md` on 0.7.0 said "Hub demand for replenishment planning = customer demand + transfers to spokes/satellites"; the 0.8 rewrite (commit `f888392`, decision record 003) says "transfers between the main warehouse and the overflow hub are not demand", and nothing tells the agent how to answer a question that asks for them anyway. Not `retrieval` (the page was read) and not `prompt instruction` (the prompt does not define demand; the definition belongs in the KB).

The case itself is now partly at odds with record 003: its notes call the transfers to Dongguan part of "main-warehouse demand", while the KB (owner-approved through record 003) says they are not demand.

## Fix hypothesis

Two candidate layers; the owner picks one (or both, measured separately):

1. **KB page** (`kb/concepts/internal_transfers.md`, rules for analysis): add one rule: when a question asks for an amount that explicitly includes the replenishment of an overflow hub, add the `313` issuing items (`SHKZG = 'H'`) to the issues to manufacturing, never the `313` receiving item or the `315` placement, and report the two parts separately. This restores what the 0.7 page covered without calling the transfers demand.
2. **Case spec** (`evals/cases/bank_v2.yaml`, c109): reword the question in the 0.8 vocabulary, for example "How many units did the Longgang main warehouse issue in fiscal period 11 of FY2026, counting both the issues to manufacturing and the replenishment of the Dongguan overflow hub?", and align the notes with record 003. The trap (`313` issuing items only, not `315`) stays.

Recommended: 1, because the agent already received an explicit inclusion and still dropped it; a question worded in 0.8 terms would hide that. Not the prompt (`PROMPT_CHANGES.md` §3: a definition belongs in the KB).

## Affected categories

The 22 `trap` cases of the dev split plus every other dev case that read or requires `concept.internal_transfers` in this batch (c164, c165): 24 cases, paired against this batch, one repetition to screen.

## Owner review

- Hypothesis review (2026-10-19): confirmed, fix in the KB page (option 1).
- Diff review (2026-10-19): approved as written.

The diff (`kb/concepts/internal_transfers.md`, rules for analysis; `last_reviewed` moves to 2026-10-19), one added rule, no other line changed:

> When a question explicitly includes the replenishment of an overflow hub, answer with both parts and their sum, in quantities: the issues to manufacturing as above, plus the transfers from the main warehouse to that hub counted once on the issuing side (Longgang: the `313` issuing items at `1000`, never the `313` receiving item or the `315` placement; Round Rock: the `311` issuing items from `1000` to `2001`). Say that the transfers are internal movements, not demand by definition.

It addresses the two gaps of the trace: the explicit inclusion is honored instead of being overridden by the "not demand" rule, and the amount is a quantity, not a line count. It states a rule, names no case, number, or answer, and leaves the two existing rules unchanged.

## Measurement

Screening batch [`bank-v8-g080-c109kb-dev-sonnet`](../reports/bank-v8-g080-c109kb-dev-sonnet.md), one repetition, paired with `bank-v8-g080-dev-sonnet` on the same 24 cases (same model, effort, prompt, and snapshot): 22/24 before, 23/24 after; discordant pairs 1 (c109 now passes) / 0. No regression in the other 23 cases (c154 fails in both, as a discovery task). Cost per question $0.058 before, $0.062 after (c109 itself $0.06 vs $0.09); cache read 88 % vs 86 %. `PROMPT_CHANGES.md` §5 asks for three repetitions before adopting; the owner chose to repeat c109 only (the rule applies only when a question explicitly includes a hub's replenishment): c109 passes 3/3 in the same batch, $0.10 per run.

## Decision

Adopted by the owner on 2026-10-19 after the screening run and two more repetitions of c109. Regression case: `bank_v2/c109` (already in the suite; its notes still describe the transfers as part of demand, which the answer spec does not depend on).
