# Evaluation report: bank_v8 (bank-v8-g080-c109kb-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.8.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Cache read | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 26 | 25/26 (96%) | 25/26 (96%) | 26/26 (100%) | 1/1 | 113/113 (100%) | $1.70 | $0.065 | 85% | 5.1 | 3.1 | 30s |

## By category

| Category | main |
|---|---|
| adhoc | 1/1 (100%) |
| investigation | 1/1 (100%) |
| trap | 23/24 (96%) |

## By split

| Split | main |
|---|---|
| dev | 25/26 (96%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main |
|---|---|---|
| seed_14 | trap | 1/1 ($0.06) |
| seed_16 | trap | 1/1 ($0.06) |
| c071 | trap | 1/1 ($0.05) |
| c073 | trap | 1/1 ($0.11) |
| c075 | trap | 1/1 ($0.07) |
| c077 | trap | 1/1 ($0.03) |
| c079 | trap | 1/1 ($0.07) |
| c081 | trap | 1/1 ($0.03) |
| c083 | trap | 1/1 ($0.06) |
| c101 | trap | 1/1 ($0.04) |
| c103 | trap | 1/1 ($0.05) |
| c109 | trap | 3/3 ($0.10) |
| c110 | trap | 1/1 ($0.06) |
| c112 | trap | 1/1 ($0.02) |
| c114 | trap | 1/1 ($0.06) |
| c116 | trap | 1/1 ($0.04) |
| c123 | trap | 1/1 ($0.04) |
| c133 | trap | 1/1 ($0.03) |
| c140 | trap | 1/1 ($0.07) |
| c154 | trap | 0/1 ($0.05) — missing pick_tasks=33, mention duplicat \| copies \| copied \| twice |
| c156 | trap | 1/1 ($0.05) |
| c164 | adhoc | 1/1 ($0.09) |
| c165 | investigation | 1/1 ($0.17) |
| c177 | trap | 1/1 ($0.07) |
## Notes (2026-10-19)

- Screening run (one repetition) of the KB rule added to `concept.internal_transfers` for failure card [bank-v8-g080-dev-sonnet-c109](../failures/bank-v8-g080-dev-sonnet-c109.md), approved by the owner on 2026-10-19. Cases: the 22 dev `trap` cases plus c164 and c165 (the other dev cases that read the page in the baseline batch). Same model, effort, prompt (`main/v4`), and data snapshot as the baseline `bank-v8-g080-dev-sonnet`; the reference answers recomputed before the run were identical.
- Paired against the baseline on the same 24 cases: 22/24 before, **23/24 after**; one discordant pair, c109 now passes (0 cases pass only before). c109 reports the issues to manufacturing (118,136), the `313` transfers to Dongguan counted once (23,587), and the sum (141,723), and says that only the first part is demand. c154 fails as before (discovery task, outside the headline).
- Cost on the same 24 cases: $1.38 before, $1.49 after ($0.058 vs $0.062 per question; c109 itself $0.06 vs $0.09); cache read 88 % vs 86 %; turns 5.1 vs 5.0; SQL statements 3.3 vs 3.0. The difference is within the run-to-run noise of one repetition.
- Two more repetitions of c109 (2026-10-19, the owner's choice: c109 only, the other 23 cases keep the screening result): **3/3 runs pass**, each reporting 118,136 + 23,587 = 141,723 with the transfers counted once; $0.30 for the three runs ($0.10 per run). The table above now counts 26 runs (c109 three times).
