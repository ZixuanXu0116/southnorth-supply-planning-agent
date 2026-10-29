# Change card: decision record 007 / planner wording of purchasing's ranking

- **Status:** adopted 2026-10-20 (`planner/v5`, under the delegated class of change)
- **Trigger:** decision record 007 (`PROMPT_CHANGES.md` §3, item 2), not a failed run: the case server now ranks proposals by expected cost and lets a C part's late offer compete; the planner's tool description and evaluate instructions still name rule (a)
- **Batch, arm, repetition:** `planner-evaluate-v4-rule007`, `planner/v4`, rep 1 (the baseline under the new rule)
- **Prompt version and digest:** `planner/v4`
- **Model and effort:** the configured main model (`SNM_MODEL_MAIN`), effort medium
- **Trace:** `runs/evals/planner-evaluate-v4-rule007/runs-v4/` (runs are not committed)
- **Suite and category:** planner-evaluate (`snm-evals planner-evaluate`), all four mixes

## Expected

The planner explains purchasing's order with the rule in force: by expected total cost, the in-mandate offers and, for C parts, the offers late on arrival only first; it cites the stock-out risk and the expected total when it explains a late offer ranked first.

## Observed

Batch `planner-evaluate-v4-rule007` (12 cases, USD 0.76): the proposals in the case server's new order in 12/12 runs, the rule's first recommended in 9/12 (the three others, all in the `outside_only` mix, recommended an engine option or the second proposal with a stated reason). In 2 of the 3 `cheaper_late_split` cases the C part's late split ranked first and was recommended; both rationales cited its stock-out risk (3.30%, 3.00%, the same as the in-mandate offer's) and expected total cost, one named the rule ("ranked first by expected cost"), the other only that the arrival miss is purchasing's to accept. So the stale description has not produced a wrong explanation yet; it still states rule (a), which a run may repeat.

## Root cause

`prompt instruction`. The order itself is a mechanism and lives in the case server (it refuses another order); what is stale is the text that describes it: the `rank_options` description ("those inside the mandate by total cost, then the others by the number of mandate fields they miss and by total cost") and the evaluate instructions' list of figures, which names neither the stock-out risk nor the expected total. Not `tool error`: the tool returns the right order and the right fields.

## Fix hypothesis

Layer: the tool description (a prompt surface, `PROMPT_CHANGES.md` §1) and the evaluate instructions; a new version `planner/v5`.

1. **`rank_options` description** (`case_server/src/snm_cases/server.py`). Now: "The proposals of the case in purchasing's order: those inside the mandate by total cost, then the others by the number of mandate fields they miss and by total cost. Every row carries its evaluation (evaluation_id, coverage date, days below zero and below safety, costs when your role sees them) and the mandate fields it misses. The engine's options and doing nothing come as reference rows, not ranked." New: "The proposals of the case in purchasing's order (decision record 007). Where the case's stock-out risk is priced: by expected total cost (the option's cost plus its expected line-down), first the proposals inside the mandate and, for a C part, those that miss only the arrival date (late_priced_by_risk), then the others by the number of mandate fields missed. Where it is not priced: inside the mandate first, by total cost. Every row carries its evaluation (evaluation_id, coverage date, days below zero and below safety, stock-out risk, costs and expected_total_cost when your role sees them) and the mandate fields it misses. The engine's options and doing nothing come as reference rows, not ranked."
2. **Evaluate instructions** (`harness/src/agent/planner.ts`, `TASK_INSTRUCTIONS_V5.evaluate`). Changed only in the list of figures. Now: "… Explain the trade-off with the evaluated figures only (coverage date, days below zero and below safety, the mandate fields missed, cost when your role sees costs)." New: "… Explain the trade-off with the evaluated figures only (coverage date, days below zero and below safety, stock-out risk, the mandate fields missed, cost and expected total cost when your role sees costs)."
3. **Version**: `PLANNER_PROMPT_VERSION` moves to `v5` on adoption; `prompts/planner/v5.md` is `v4.md` unchanged (the change is in the task instructions and the tool description).

Checklist (§6): a rule stated once (in the tool description, where the mechanism lives; the instructions only add two figures to cite); no duplication of the rule in the instructions; testable (the suite's rule-order check, and whether the rationale of a C part's late offer ranked first names its risk and expected total); no non-negotiable touched (the agent still decides nothing); the frozen prefix changes only in the task text and the tool description.

The tool description is shared by every version that has `rank_options` (v4 and v5), so the paired measurement compares `planner-evaluate-v4-rule007` (old description, v4 instructions, today) with a v5 batch on the same sampled cases after the change.

## Affected categories

The planner-evaluate suite (four mixes; the `cheaper_late_split` mix on C parts is where the new order differs) and, unchanged, the prepare suite (`snm-evals planner`).

## Owner review

- Hypothesis review (2026-10-20): confirmed by the owner, layer: tool description and evaluate instructions.
- Diff review (2026-10-20): approved by the owner as written. The owner delegated this class of change (wording aligned with a decided rule) to the assistant from now on (`STATUS.md`, working agreements).

## Measurement

2026-10-20, the same 12 sampled cases as the baseline (planner-evaluate suite, four mixes), three repetitions of v5 against the v4 baseline under the new rule; the tool description is shared, so the baseline is the batch run before the change (`planner-evaluate-v4-rule007`).

| Batch | Proposals in the server's order | Recommended the rule's first | Rationale cites the stock-out risk (two decimals) | Cites the expected total | Late split ranked first, explained with risk and expected cost | Cost |
|---|---|---|---|---|---|---|
| v4 baseline (`planner-evaluate-v4-rule007`) | 12/12 | 9/12 | 12/12 | 9/12 | 2/2 | USD 0.76 |
| v5 (`planner-evaluate-v5-rep1` to `-rep3`) | 36/36 | 33/36 | 36/36 | 36/36 | 6/6 | USD 2.02 |

No rationale states rule (a). The prepare suite with v5 (`planner-prepare-v5`) passes 9/9 (USD 0.32). No regression.

## Decision

Adopted 2026-10-20 by the assistant under the owner's delegation for wording aligned with a decided rule (`STATUS.md`, working agreements): `PLANNER_PROMPT_VERSION` is `v5`, the default of both planner suites; v4 stays selectable for comparisons.

