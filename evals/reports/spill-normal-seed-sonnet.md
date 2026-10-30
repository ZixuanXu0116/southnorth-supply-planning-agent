# Evaluation report: seed (spill-normal-seed-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.8.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Cache read | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 18 | 16/18 (89%) | 14/16 (88%) | 18/18 (100%) | - | 97/97 (100%) | $0.98 | $0.054 | 86% | 4.8 | 3.4 | 28s |
| main_spill | claude-sonnet-5-5 | 18 | 17/18 (94%) | 15/16 (94%) | 18/18 (100%) | - | 98/98 (100%) | $0.87 | $0.048 | 87% | 4.4 | 3.1 | 23s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main_spill vs main | spill files for oversized tool outputs (candidate) | 18 | +5.6 pp (+0.0 pp, +16.7 pp) | 1 / 0 | 1.000 | $-0.006 ($-0.010, $-0.002) |

## By category

| Category | main | main_spill |
|---|---|---|
| adhoc | 2/3 (67%) | 2/3 (67%) |
| clarify | 1/1 (100%) | 1/1 (100%) |
| covered | 4/5 (80%) | 5/5 (100%) |
| restricted | 2/2 (100%) | 2/2 (100%) |
| trap | 4/4 (100%) | 4/4 (100%) |
| unfamiliar | 3/3 (100%) | 3/3 (100%) |

## By split

| Split | main | main_spill |
|---|---|---|
| dev | 10/10 (100%) | 10/10 (100%) |
| test | 6/8 (75%) | 7/8 (88%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | main_spill |
|---|---|---|---|
| seed_01 | covered | 1/1 ($0.05) | 1/1 ($0.05) |
| seed_02 | covered | 1/1 ($0.06) | 1/1 ($0.04) |
| seed_03 | covered | 0/1 ($0.08) — missing median_minutes{'lgnum': 'DG01'}=18.225, median_minutes{'lgnum': 'SZ01'}=11.083333 | 1/1 ($0.05) |
| seed_04 | covered | 1/1 ($0.03) | 1/1 ($0.03) |
| seed_05 | covered | 1/1 ($0.03) | 1/1 ($0.03) |
| seed_07 | adhoc | 1/1 ($0.09) | 1/1 ($0.08) |
| seed_08 | adhoc | 1/1 ($0.06) | 1/1 ($0.05) |
| seed_09 | adhoc | 0/1 ($0.08) — missing reversed_deliveries{'vstel': 'C300'}=33 | 0/1 ($0.06) — missing reversed_deliveries{'vstel': 'C300'}=33 |
| seed_10 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.04) |
| seed_11 | unfamiliar | 1/1 ($0.06) | 1/1 ($0.06) |
| seed_12 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.02) |
| seed_13 | trap | 1/1 ($0.09) | 1/1 ($0.09) |
| seed_14 | trap | 1/1 ($0.04) | 1/1 ($0.04) |
| seed_15 | trap | 1/1 ($0.07) | 1/1 ($0.07) |
| seed_16 | trap | 1/1 ($0.04) | 1/1 ($0.04) |
| seed_17 | restricted | 1/1 ($0.01) | 1/1 ($0.01) |
| seed_19 | clarify | 1/1 ($0.05) | 1/1 ($0.04) |
| seed_20 | restricted | 1/1 ($0.06) | 1/1 ($0.08) |
