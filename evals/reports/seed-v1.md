# Evaluation report: seed (seed-v1)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.1.2', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|
| main | 60 | 60/60 (100%) | 51/51 (100%) | 60/60 (100%) | 20/20 | $5.72 | $0.095 | 5.2 | 2.7 | 32s |
| baseline | 20 | 20/20 (100%) | 17/17 (100%) | 20/20 (100%) | - | $2.07 | $0.104 | 5.7 | 3.5 | 34s |
| main_bare | 20 | 20/20 (100%) | 17/17 (100%) | 20/20 (100%) | - | $1.97 | $0.098 | 5.5 | 3.1 | 34s |
| baseline_bare | 60 | 57/60 (95%) | 48/51 (94%) | 60/60 (100%) | 20/20 | $5.87 | $0.098 | 6.9 | 6.3 | 41s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs baseline_bare | full stack vs naive (knowledge base, prompt, and metadata documentation) | 20 | +5.0 pp (+0.0 pp, +15.0 pp) | 1 / 0 | 1.000 | $-0.002 ($-0.019, $+0.012) |
| main vs baseline | knowledge base + governed prompt, documented metadata | 20 | +0.0 pp (+0.0 pp, +0.0 pp) | 0 / 0 | 1.000 | $-0.008 ($-0.025, $+0.008) |
| main_bare vs baseline_bare | knowledge base + governed prompt, bare metadata | 20 | +5.0 pp (+0.0 pp, +15.0 pp) | 1 / 0 | 1.000 | $+0.001 ($-0.013, $+0.013) |
| baseline vs baseline_bare | metadata documentation, no knowledge base | 20 | +5.0 pp (+0.0 pp, +15.0 pp) | 1 / 0 | 1.000 | $+0.006 ($-0.004, $+0.015) |
| main vs main_bare | metadata documentation, with knowledge base | 20 | +0.0 pp (+0.0 pp, +0.0 pp) | 0 / 0 | 1.000 | $-0.003 ($-0.009, $+0.003) |

## By category

| Category | main | baseline | main_bare | baseline_bare |
|---|---|---|---|---|
| adhoc | 12/12 (100%) | 4/4 (100%) | 4/4 (100%) | 9/12 (75%) |
| clarify | 3/3 (100%) | 1/1 (100%) | 1/1 (100%) | 3/3 (100%) |
| covered | 15/15 (100%) | 5/5 (100%) | 5/5 (100%) | 15/15 (100%) |
| decline | 3/3 (100%) | 1/1 (100%) | 1/1 (100%) | 3/3 (100%) |
| restricted | 6/6 (100%) | 2/2 (100%) | 2/2 (100%) | 6/6 (100%) |
| trap | 12/12 (100%) | 4/4 (100%) | 4/4 (100%) | 12/12 (100%) |
| unfamiliar | 9/9 (100%) | 3/3 (100%) | 3/3 (100%) | 9/9 (100%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | baseline | main_bare | baseline_bare |
|---|---|---|---|---|---|
| seed_01 | covered | 3/3 ($0.11) | 1/1 ($0.18) | 1/1 ($0.13) | 3/3 ($0.13) |
| seed_02 | covered | 3/3 ($0.07) | 1/1 ($0.13) | 1/1 ($0.10) | 3/3 ($0.11) |
| seed_03 | covered | 3/3 ($0.11) | 1/1 ($0.15) | 1/1 ($0.14) | 3/3 ($0.14) |
| seed_04 | covered | 3/3 ($0.15) | 1/1 ($0.23) | 1/1 ($0.17) | 3/3 ($0.26) |
| seed_05 | covered | 3/3 ($0.12) | 1/1 ($0.13) | 1/1 ($0.12) | 3/3 ($0.12) |
| seed_06 | adhoc | 3/3 ($0.09) | 1/1 ($0.08) | 1/1 ($0.09) | 3/3 ($0.08) |
| seed_07 | adhoc | 3/3 ($0.11) | 1/1 ($0.13) | 1/1 ($0.10) | 3/3 ($0.13) |
| seed_08 | adhoc | 3/3 ($0.10) | 1/1 ($0.13) | 1/1 ($0.11) | 3/3 ($0.13) |
| seed_09 | adhoc | 3/3 ($0.12) | 1/1 ($0.12) | 1/1 ($0.13) | 0/3 ($0.09) — missing reversed_deliveries{'vstel': 'C100'}=123, reversed_deliveries{'vstel': 'C300'}=51 |
| seed_10 | unfamiliar | 3/3 ($0.07) | 1/1 ($0.05) | 1/1 ($0.06) | 3/3 ($0.04) |
| seed_11 | unfamiliar | 3/3 ($0.11) | 1/1 ($0.11) | 1/1 ($0.11) | 3/3 ($0.09) |
| seed_12 | unfamiliar | 3/3 ($0.03) | 1/1 ($0.02) | 1/1 ($0.03) | 3/3 ($0.02) |
| seed_13 | trap | 3/3 ($0.15) | 1/1 ($0.11) | 1/1 ($0.13) | 3/3 ($0.10) |
| seed_14 | trap | 3/3 ($0.08) | 1/1 ($0.06) | 1/1 ($0.07) | 3/3 ($0.09) |
| seed_15 | trap | 3/3 ($0.11) | 1/1 ($0.12) | 1/1 ($0.11) | 3/3 ($0.11) |
| seed_16 | trap | 3/3 ($0.12) | 1/1 ($0.06) | 1/1 ($0.13) | 3/3 ($0.07) |
| seed_17 | restricted | 3/3 ($0.02) | 1/1 ($0.06) | 1/1 ($0.02) | 3/3 ($0.03) |
| seed_18 | decline | 3/3 ($0.02) | 1/1 ($0.05) | 1/1 ($0.02) | 3/3 ($0.04) |
| seed_19 | clarify | 3/3 ($0.03) | 1/1 ($0.04) | 1/1 ($0.03) | 3/3 ($0.03) |
| seed_20 | restricted | 3/3 ($0.17) | 1/1 ($0.11) | 1/1 ($0.18) | 3/3 ($0.16) |

## Notes (2026-09-26)

- Batch: `main` and `baseline_bare` three repetitions each; `baseline` and `main_bare` one repetition. Spend for the
  ablation and repetitions: $11.69 (the batch total above includes the earlier `main`/`baseline` run).
- **Stability:** `main` passed all 60 runs and every case gave the same outcome in all three repetitions.
- **Metadata documentation matters for correctness in one place:** without table and column documentation and
  without the knowledge base (`baseline_bare`), the agent failed seed_09 in 3/3 runs: it treated plant `CN10` as one
  site and merged Longgang and Dongguan (the shared-plant trap). Both the knowledge base alone (`main_bare`) and the
  documentation alone (`baseline`) fix it.
- **Metadata documentation matters most for efficiency:** `baseline_bare` needs 6.3 SQL statements per question vs
  2.7 for `main` (+130 %), 6.9 vs 5.2 turns, and 41 s vs 32 s. Cost per question is about the same (~$0.10)
  because undocumented `describe_table` outputs are shorter.
- **No difference is statistically significant:** one discriminating case out of 20 gives McNemar p = 1.0 and
  bootstrap intervals that include zero. The seed suite is too small and too easy to measure the knowledge base.
- Scorer fixes found by this run: durations are converted between time units (a correct answer in hours was
  rejected); earlier, keys are matched in the value unit as well as the label.
