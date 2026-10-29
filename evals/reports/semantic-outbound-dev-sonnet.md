# Evaluation report: bank_v7 (semantic-outbound-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.6.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 15 | 15/15 (100%) | 13/13 (100%) | 15/15 (100%) | - | 86/86 (100%) | $0.57 | $0.038 | 3.4 | 2.2 | 27s |
| main_v2 | claude-sonnet-5-5 | 15 | 15/15 (100%) | 13/13 (100%) | 15/15 (100%) | - | 85/85 (100%) | $0.67 | $0.045 | 4.8 | 3.2 | 24s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs main_v2 | governed metric views and query_metric (prompt v3 vs v2) | 15 | +0.0 pp (+0.0 pp, +0.0 pp) | 0 / 0 | 1.000 | $-0.007 ($-0.012, $-0.001) |

## By category

| Category | main | main_v2 |
|---|---|---|
| adhoc | 2/2 (100%) | 2/2 (100%) |
| clarify | 2/2 (100%) | 2/2 (100%) |
| covered | 7/7 (100%) | 7/7 (100%) |
| multi_step | 1/1 (100%) | 1/1 (100%) |
| trap | 3/3 (100%) | 3/3 (100%) |

## By split

| Split | main | main_v2 |
|---|---|---|
| dev | 15/15 (100%) | 15/15 (100%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | main_v2 |
|---|---|---|---|
| seed_01 | covered | 1/1 ($0.05) | 1/1 ($0.06) |
| seed_02 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| seed_19 | clarify | 1/1 ($0.05) | 1/1 ($0.05) |
| c021 | covered | 1/1 ($0.05) | 1/1 ($0.05) |
| c023 | covered | 1/1 ($0.02) | 1/1 ($0.03) |
| c025 | covered | 1/1 ($0.02) | 1/1 ($0.04) |
| c039 | covered | 1/1 ($0.02) | 1/1 ($0.04) |
| c050 | adhoc | 1/1 ($0.03) | 1/1 ($0.06) |
| c056 | adhoc | 1/1 ($0.07) | 1/1 ($0.05) |
| c069 | trap | 1/1 ($0.05) | 1/1 ($0.06) |
| c087 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c101 | trap | 1/1 ($0.03) | 1/1 ($0.05) |
| c112 | trap | 1/1 ($0.03) | 1/1 ($0.04) |
| c107 | multi_step | 1/1 ($0.04) | 1/1 ($0.05) |
| c163 | covered | 1/1 ($0.04) | 1/1 ($0.05) |

## Notes (2026-10-08)

- First semantic-layer slice: curated views `curated.dim_date` and `curated.fct_outbound_delivery`, metric views `semantic.mv_outbound_due` (on-time PGI rate) and `semantic.mv_outbound_shipped` (shipped deliveries and lines), and the tools `list_metrics` and `query_metric`. `main` (prompt v3) has the metric tools; `main_v2` is the previous main configuration. Cases: the fifteen dev cases whose definitions are the on-time PGI rate or lines shipped, including two clarify cases and two the metric views do not cover (c056 material quantities, c069 units shipped).
- Accuracy is at the ceiling for both arms (15/15). The difference is efficiency: `main` answered every covered case with one or two `query_metric` calls and no hand-written SQL, at 3.4 turns and 2.2 queries per question against 4.8 and 3.2, and $0.007 (16 %) less per question (95 % CI excludes zero).
- For the uncovered c056, `main` tried `query_metric` (the shipped view has no material dimension) and then wrote SQL; for c069 it used `run_sql` for the quantities. Both answers were correct.
- Before the run, a live test (`tool_server/tests/test_live_metrics.py`) reproduced the reference answers of ten covered cases and c107 through the metric views, as the ops analyst's service principal; site managers see only their own site through the metric views.
