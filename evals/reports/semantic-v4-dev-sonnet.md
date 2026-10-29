# Evaluation report: bank_v7 (semantic-v4-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.6.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 92 | 90/92 (98%) | 77/79 (97%) | 92/92 (100%) | - | 402/402 (100%) | $3.71 | $0.040 | 4.1 | 2.5 | 21s |
| main_v2 | claude-sonnet-5-5 | 92 | 75/92 (82%) | 65/79 (82%) | 77/92 (84%) | - | 295/295 (100%) | $2.97 | $0.032 | 3.8 | 2.1 | 17s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs main_v2 | governed metric views and query_metric (prompt v4 vs v2) | 92 | +16.3 pp (+8.7 pp, +23.9 pp) | 15 / 0 | 0.000 | $+0.008 ($+0.004, $+0.013) |

## By category

| Category | main | main_v2 |
|---|---|---|
| adhoc | 17/17 (100%) | 14/17 (82%) |
| clarify | 4/4 (100%) | 4/4 (100%) |
| covered | 18/18 (100%) | 16/18 (89%) |
| decline | 4/4 (100%) | 3/4 (75%) |
| investigation | 3/4 (75%) | 1/4 (25%) |
| multi_step | 5/5 (100%) | 4/5 (80%) |
| restricted | 7/7 (100%) | 4/7 (57%) |
| trap | 21/22 (95%) | 18/22 (82%) |
| unfamiliar | 11/11 (100%) | 11/11 (100%) |

## By split

| Split | main | main_v2 |
|---|---|---|
| dev | 90/92 (98%) | 75/92 (82%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | main_v2 |
|---|---|---|---|
| seed_01 | covered | 1/1 ($0.05) | 1/1 ($0.06) |
| seed_02 | covered | 1/1 ($0.06) | 1/1 ($0.04) |
| seed_04 | covered | 1/1 ($0.05) | 1/1 ($0.05) |
| seed_06 | adhoc | 1/1 ($0.03) | 1/1 ($0.03) |
| seed_08 | adhoc | 1/1 ($0.07) | 1/1 ($0.06) |
| seed_10 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| seed_12 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| seed_14 | trap | 1/1 ($0.04) | 1/1 ($0.03) |
| seed_16 | trap | 1/1 ($0.03) | 0/1 ($0.04) — missing deliveries_shipped{'vstel': 'U100'}=0 |
| seed_17 | restricted | 1/1 ($0.01) | 1/1 ($0.01) |
| seed_19 | clarify | 1/1 ($0.05) | 1/1 ($0.04) |
| c021 | covered | 1/1 ($0.03) | 1/1 ($0.04) |
| c023 | covered | 1/1 ($0.02) | 1/1 ($0.03) |
| c025 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c026 | covered | 1/1 ($0.05) | 1/1 ($0.05) |
| c028 | covered | 1/1 ($0.04) | 0/1 ($0.07) — missing median_minutes{'lgnum': 'RR01'}=188.20833349999998 |
| c029 | covered | 1/1 ($0.03) | 1/1 ($0.04) |
| c031 | covered | 1/1 ($0.03) | 1/1 ($0.02) |
| c033 | covered | 1/1 ($0.03) | 1/1 ($0.03) |
| c035 | covered | 1/1 ($0.03) | 1/1 ($0.05) |
| c036 | covered | 1/1 ($0.03) | 1/1 ($0.05) |
| c038 | covered | 1/1 ($0.05) | 1/1 ($0.04) |
| c039 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c041 | adhoc | 1/1 ($0.03) | 1/1 ($0.03) |
| c043 | adhoc | 1/1 ($0.06) | 1/1 ($0.03) |
| c045 | adhoc | 1/1 ($0.04) | 1/1 ($0.03) |
| c046 | adhoc | 1/1 ($0.08) | 1/1 ($0.05) |
| c048 | adhoc | 1/1 ($0.03) | 1/1 ($0.02) |
| c050 | adhoc | 1/1 ($0.04) | 1/1 ($0.06) |
| c051 | adhoc | 1/1 ($0.06) | 1/1 ($0.05) |
| c053 | adhoc | 1/1 ($0.05) | 1/1 ($0.04) |
| c055 | adhoc | 1/1 ($0.04) | 1/1 ($0.03) |
| c056 | adhoc | 1/1 ($0.06) | 1/1 ($0.06) |
| c057 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c059 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c060 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| c062 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| c064 | unfamiliar | 1/1 ($0.05) | 1/1 ($0.04) |
| c066 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| c067 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c069 | trap | 1/1 ($0.08) | 1/1 ($0.06) |
| c071 | trap | 1/1 ($0.02) | 1/1 ($0.02) |
| c073 | trap | 1/1 ($0.07) | 1/1 ($0.05) |
| c075 | trap | 1/1 ($0.07) | 1/1 ($0.05) |
| c077 | trap | 1/1 ($0.03) | 1/1 ($0.03) |
| c079 | trap | 1/1 ($0.07) | 1/1 ($0.05) |
| c081 | trap | 1/1 ($0.03) | 1/1 ($0.03) |
| c083 | trap | 1/1 ($0.06) | 1/1 ($0.05) |
| c085 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c087 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c089 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c090 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c092 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c094 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c096 | restricted | 1/1 ($0.06) | 1/1 ($0.07) |
| c098 | restricted | 1/1 ($0.03) | 1/1 ($0.05) |
| c100 | restricted | 1/1 ($0.02) | 1/1 ($0.02) |
| c101 | trap | 1/1 ($0.03) | 1/1 ($0.05) |
| c103 | trap | 1/1 ($0.04) | 1/1 ($0.04) |
| c109 | trap | 1/1 ($0.11) | 1/1 ($0.08) |
| c110 | trap | 1/1 ($0.04) | 1/1 ($0.05) |
| c112 | trap | 1/1 ($0.03) | 1/1 ($0.03) |
| c114 | trap | 1/1 ($0.06) | 1/1 ($0.05) |
| c116 | trap | 1/1 ($0.05) | 1/1 ($0.04) |
| c105 | multi_step | 1/1 ($0.06) | 1/1 ($0.05) |
| c107 | multi_step | 1/1 ($0.03) | 1/1 ($0.04) |
| c117 | covered | 1/1 ($0.03) | 1/1 ($0.05) |
| c119 | adhoc | 1/1 ($0.04) | 1/1 ($0.05) |
| c121 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.04) |
| c123 | trap | 1/1 ($0.04) | 1/1 ($0.05) |
| c125 | multi_step | 1/1 ($0.04) | 1/1 ($0.04) |
| c127 | investigation | 1/1 ($0.11) | 1/1 ($0.08) |
| c128 | covered | 1/1 ($0.02) | 1/1 ($0.03) |
| c130 | adhoc | 1/1 ($0.06) | 1/1 ($0.06) |
| c132 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.03) |
| c133 | trap | 1/1 ($0.03) | 1/1 ($0.03) |
| c137 | multi_step | 1/1 ($0.05) | 1/1 ($0.05) |
| c136 | investigation | 1/1 ($0.10) | 0/1 ($0.02) — behavior=None; missing mention AS-PORT-03 \| port 03 \| port 3, mention AS-PORT-07 \| port 07 \| port 7; error |
| c138 | adhoc | 1/1 ($0.04) | 0/1 ($0.00) — behavior=None; missing cage_exits=11; error |
| c140 | trap | 1/1 ($0.08) | 0/1 ($0.00) — behavior=None; missing median_minutes=15.516667; error |
| c144 | multi_step | 1/1 ($0.05) | 0/1 ($0.00) — behavior=None; missing loaded=12, left_storage=9; error |
| c142 | investigation | 0/1 ($0.08) — missing mention confirmed late \| late confirmation \| confirmed later \| delayed confirmation \| confirmation delay \| confirm late \| confirmations lag \| after the physical pick \| after the pick | 0/1 ($0.00) — behavior=None; missing mention RFID \| LTS, mention confirmed late \| late confirmation \| confirmed later \| delayed confirmation \| confirmation delay \| confirm late \| confirmations lag \| after the physical pick \| after the pick; error |
| c146 | adhoc | 1/1 ($0.02) | 0/1 ($0.00) — behavior=None; missing materials{'eccn': '3A001'}=9, materials{'eccn': '3A991'}=39; error |
| c148 | adhoc | 1/1 ($0.05) | 0/1 ($0.00) — behavior=None; missing deliveries{'land1': 'IL'}=70, deliveries{'land1': 'KR'}=8; error |
| c150 | restricted | 1/1 ($0.04) | 0/1 ($0.00) — behavior=None; missing held_deliveries=3, avg_hold_hours=38.88; error |
| c152 | decline | 1/1 ($0.01) | 0/1 ($0.00) — behavior=None; error |
| c154 | trap | 0/1 ($0.04) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice | 0/1 ($0.00) — behavior=None; missing pick_tasks=40, mention duplicat \| copies \| copied \| twice; error |
| c156 | trap | 1/1 ($0.05) | 0/1 ($0.00) — behavior=None; missing april_qty=2638.000, june_qty=2789.000; error |
| c158 | investigation | 1/1 ($0.04) | 0/1 ($0.00) — behavior=None; missing mention Chinese New Year \| Spring Festival \| Lunar New Year \| CNY holiday \| CNY shutdown \| CNY period; error |
| c160 | restricted | 1/1 ($0.01) | 0/1 ($0.00) — behavior=None; error |
| c162 | restricted | 1/1 ($0.01) | 0/1 ($0.00) — behavior=None; error |
| c163 | covered | 1/1 ($0.02) | 0/1 ($0.00) — behavior=None; missing deliveries_shipped=126, lines_shipped=267; error |

## Notes (2026-10-09, interim)

- Follow-ups to the previous full run ([`semantic-full-dev-sonnet.md`](semantic-full-dev-sonnet.md)): compact `list_metrics` plus `describe_metric`, the restricted `operator` dimension of `mv_warehouse_tasks`, main prompt v4, and the scorer's letter-prefix rule for numeric codes. `main` runs prompt v4 with the metric tools; `main_v2` is unchanged.
- `main` is complete: 90/92, with the same two failures as every arm since the cases were added (c142 late-confirmation investigation, c154 duplicated replication day). All six cases that regressed in the previous run pass. `describe_metric` was called in 28 runs; 31 runs used `query_metric` only, 18 both tools, 32 `run_sql` only, 11 neither.
- **`main_v2` is incomplete.** The Anthropic credit balance ran out after 169 of 184 runs (`400 credit balance too low`), so its last 15 runs (c136 to c163) are errors, not agent failures; the arm's pass rate, the paired comparison, and the cost means above are not valid. On the 77 cases both arms completed: `main` 77/77 against `main_v2` 75/77, $0.040 against $0.038 per question, 4.1 against 4.3 turns, 2.6 against 2.4 queries. The batch will be finished with `snm-evals run --retry-errors` once the credit is topped up, and these notes replaced.
