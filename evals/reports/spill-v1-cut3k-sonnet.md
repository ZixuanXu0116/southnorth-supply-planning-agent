# Evaluation report: spill_v1 (spill-v1-cut3k-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.8.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Cache read | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 12 | 12/12 (100%) | 12/12 (100%) | 12/12 (100%) | 4/4 | 16/16 (100%) | $0.30 | $0.025 | 91% | 3.5 | 1.3 | 15s |
| main_nospill | claude-sonnet-5-5 | 12 | 12/12 (100%) | 12/12 (100%) | 12/12 (100%) | 4/4 | 20/20 (100%) | $0.34 | $0.028 | 93% | 4.4 | 2.8 | 17s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs main_nospill | spill files against the plain cut of oversized outputs | 4 | +0.0 pp (+0.0 pp, +0.0 pp) | 0 / 0 | 1.000 | $-0.003 ($-0.007, $+0.001) |

## By category

| Category | main | main_nospill |
|---|---|---|
| unfamiliar | 12/12 (100%) | 12/12 (100%) |

## By split

| Split | main | main_nospill |
|---|---|---|
| dev | 12/12 (100%) | 12/12 (100%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | main_nospill |
|---|---|---|---|
| s01 | unfamiliar | 3/3 ($0.03) | 3/3 ($0.04) |
| s02 | unfamiliar | 3/3 ($0.03) | 3/3 ($0.03) |
| s03 | unfamiliar | 3/3 ($0.02) | 3/3 ($0.03) |
| s04 | unfamiliar | 3/3 ($0.02) | 3/3 ($0.02) |
