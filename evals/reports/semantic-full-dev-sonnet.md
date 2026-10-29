# Evaluation report: bank_v7 (semantic-full-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.6.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 92 | 84/92 (91%) | 72/79 (91%) | 91/92 (99%) | - | 393/393 (100%) | $4.13 | $0.045 | 3.8 | 2.2 | 21s |
| main_v2 | claude-sonnet-5-5 | 92 | 89/92 (97%) | 76/79 (96%) | 92/92 (100%) | - | 356/356 (100%) | $3.55 | $0.039 | 4.3 | 2.6 | 20s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs main_v2 | governed metric views and query_metric (prompt v3 vs v2) | 92 | -5.4 pp (-10.9 pp, +0.0 pp) | 1 / 6 | 0.125 | $+0.006 ($+0.004, $+0.009) |

## By category

| Category | main | main_v2 |
|---|---|---|
| adhoc | 16/17 (94%) | 17/17 (100%) |
| clarify | 4/4 (100%) | 4/4 (100%) |
| covered | 17/18 (94%) | 18/18 (100%) |
| decline | 4/4 (100%) | 4/4 (100%) |
| investigation | 3/4 (75%) | 3/4 (75%) |
| multi_step | 5/5 (100%) | 4/5 (80%) |
| restricted | 5/7 (71%) | 7/7 (100%) |
| trap | 19/22 (86%) | 21/22 (95%) |
| unfamiliar | 11/11 (100%) | 11/11 (100%) |

## By split

| Split | main | main_v2 |
|---|---|---|
| dev | 84/92 (91%) | 89/92 (97%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | main_v2 |
|---|---|---|---|
| seed_01 | covered | 1/1 ($0.07) | 1/1 ($0.07) |
| seed_02 | covered | 1/1 ($0.07) | 1/1 ($0.04) |
| seed_04 | covered | 1/1 ($0.06) | 1/1 ($0.05) |
| seed_06 | adhoc | 1/1 ($0.05) | 1/1 ($0.03) |
| seed_08 | adhoc | 1/1 ($0.06) | 1/1 ($0.06) |
| seed_10 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| seed_12 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| seed_14 | trap | 1/1 ($0.07) | 1/1 ($0.03) |
| seed_16 | trap | 0/1 ($0.04) — missing deliveries_shipped{'vstel': 'U100'}=0 | 1/1 ($0.04) |
| seed_17 | restricted | 1/1 ($0.01) | 1/1 ($0.01) |
| seed_19 | clarify | 1/1 ($0.06) | 1/1 ($0.04) |
| c021 | covered | 1/1 ($0.04) | 1/1 ($0.05) |
| c023 | covered | 1/1 ($0.04) | 1/1 ($0.03) |
| c025 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c026 | covered | 1/1 ($0.05) | 1/1 ($0.05) |
| c028 | covered | 1/1 ($0.04) | 1/1 ($0.07) |
| c029 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c031 | covered | 1/1 ($0.04) | 1/1 ($0.02) |
| c033 | covered | 1/1 ($0.04) | 1/1 ($0.03) |
| c035 | covered | 1/1 ($0.04) | 1/1 ($0.05) |
| c036 | covered | 0/1 ($0.05) — missing on_time_rate{'bukrs': '1000'}=0.7852, on_time_rate{'bukrs': '2000'}=0.8016 | 1/1 ($0.05) |
| c038 | covered | 1/1 ($0.07) | 1/1 ($0.06) |
| c039 | covered | 1/1 ($0.05) | 1/1 ($0.04) |
| c041 | adhoc | 1/1 ($0.03) | 1/1 ($0.02) |
| c043 | adhoc | 1/1 ($0.05) | 1/1 ($0.04) |
| c045 | adhoc | 1/1 ($0.02) | 1/1 ($0.02) |
| c046 | adhoc | 1/1 ($0.07) | 1/1 ($0.05) |
| c048 | adhoc | 1/1 ($0.02) | 1/1 ($0.02) |
| c050 | adhoc | 1/1 ($0.05) | 1/1 ($0.06) |
| c051 | adhoc | 1/1 ($0.04) | 1/1 ($0.06) |
| c053 | adhoc | 1/1 ($0.05) | 1/1 ($0.03) |
| c055 | adhoc | 1/1 ($0.04) | 1/1 ($0.03) |
| c056 | adhoc | 1/1 ($0.07) | 1/1 ($0.06) |
| c057 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.03) |
| c059 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c060 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.01) |
| c062 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| c064 | unfamiliar | 1/1 ($0.07) | 1/1 ($0.05) |
| c066 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| c067 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c069 | trap | 1/1 ($0.07) | 1/1 ($0.06) |
| c071 | trap | 1/1 ($0.02) | 1/1 ($0.03) |
| c073 | trap | 1/1 ($0.09) | 1/1 ($0.06) |
| c075 | trap | 1/1 ($0.07) | 1/1 ($0.05) |
| c077 | trap | 1/1 ($0.04) | 1/1 ($0.05) |
| c079 | trap | 1/1 ($0.08) | 1/1 ($0.05) |
| c081 | trap | 1/1 ($0.06) | 1/1 ($0.04) |
| c083 | trap | 1/1 ($0.05) | 1/1 ($0.05) |
| c085 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c087 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c089 | clarify | 1/1 ($0.01) | 1/1 ($0.00) |
| c090 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c092 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c094 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c096 | restricted | 0/1 ($0.09) — missing pick_tasks{'confirmed_by': 'RRU0001'}=36, pick_tasks{'confirmed_by': 'RRU0002'}=42 | 1/1 ($0.07) |
| c098 | restricted | 0/1 ($0.05) — behavior=decline | 1/1 ($0.05) |
| c100 | restricted | 1/1 ($0.02) | 1/1 ($0.02) |
| c101 | trap | 1/1 ($0.05) | 1/1 ($0.04) |
| c103 | trap | 1/1 ($0.05) | 1/1 ($0.04) |
| c109 | trap | 1/1 ($0.08) | 1/1 ($0.07) |
| c110 | trap | 1/1 ($0.05) | 1/1 ($0.05) |
| c112 | trap | 1/1 ($0.05) | 1/1 ($0.04) |
| c114 | trap | 1/1 ($0.04) | 1/1 ($0.04) |
| c116 | trap | 1/1 ($0.04) | 1/1 ($0.05) |
| c105 | multi_step | 1/1 ($0.06) | 0/1 ($0.05) — missing open_lines{'name1': 'Hoshino RF Devices'}=29, overdue_lines{'name1': 'Hoshino RF Devices'}=0 |
| c107 | multi_step | 1/1 ($0.05) | 1/1 ($0.04) |
| c117 | covered | 1/1 ($0.04) | 1/1 ($0.05) |
| c119 | adhoc | 1/1 ($0.05) | 1/1 ($0.05) |
| c121 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.03) |
| c123 | trap | 0/1 ($0.04) — missing pick_tasks=271 | 1/1 ($0.04) |
| c125 | multi_step | 1/1 ($0.04) | 1/1 ($0.05) |
| c127 | investigation | 1/1 ($0.17) | 1/1 ($0.11) |
| c128 | covered | 1/1 ($0.04) | 1/1 ($0.03) |
| c130 | adhoc | 0/1 ($0.07) — missing autostore_picks=1716 | 1/1 ($0.06) |
| c132 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c133 | trap | 1/1 ($0.02) | 1/1 ($0.03) |
| c137 | multi_step | 1/1 ($0.05) | 1/1 ($0.05) |
| c136 | investigation | 1/1 ($0.10) | 1/1 ($0.04) |
| c138 | adhoc | 1/1 ($0.04) | 1/1 ($0.04) |
| c140 | trap | 1/1 ($0.05) | 1/1 ($0.05) |
| c144 | multi_step | 1/1 ($0.04) | 1/1 ($0.03) |
| c142 | investigation | 0/1 ($0.06) — missing mention RFID \| LTS, mention confirmed late \| late confirmation \| confirmed later \| delayed confirmation \| confirmation delay \| confirm late \| confirmations lag \| after the physical pick \| after the pick | 0/1 ($0.05) — missing mention RFID \| LTS, mention confirmed late \| late confirmation \| confirmed later \| delayed confirmation \| confirmation delay \| confirm late \| confirmations lag \| after the physical pick \| after the pick |
| c146 | adhoc | 1/1 ($0.02) | 1/1 ($0.02) |
| c148 | adhoc | 1/1 ($0.05) | 1/1 ($0.05) |
| c150 | restricted | 1/1 ($0.04) | 1/1 ($0.04) |
| c152 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c154 | trap | 0/1 ($0.06) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice | 0/1 ($0.04) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice |
| c156 | trap | 1/1 ($0.07) | 1/1 ($0.04) |
| c158 | investigation | 1/1 ($0.07) | 1/1 ($0.05) |
| c160 | restricted | 1/1 ($0.01) | 1/1 ($0.01) |
| c162 | restricted | 1/1 ($0.01) | 1/1 ($0.01) |
| c163 | covered | 1/1 ($0.04) | 1/1 ($0.04) |

## Notes (2026-10-09)

- Second semantic-layer slice: curated views for every knowledge-base metric (`fct_outbound_delivery` gains warehouse, POD, and wave-to-PGI fields; new `fct_warehouse_task`, `fct_inbound_putaway`, `fct_autostore_status_daily`, `fct_stock_position_daily`, `fct_purchase_schedule_line`) and eight metric views covering all eight KB metrics. Every run result now lists the `queries` behind the cited values (`result_id`, `sql`, `metric_view`). This is the first full dev run (92 cases) of `main` (prompt v3, `list_metrics` and `query_metric`) against `main_v2` (the previous main configuration).
- Result: `main` 84/92 against `main_v2` 89/92 (-5.4 pp; the 95 % interval touches zero, McNemar p = 0.125). `main` needs fewer turns and queries (3.8 and 2.2 against 4.3 and 2.6) but costs $0.006 more per question: `list_metrics` returns about 17.6k characters (about 4.4k tokens) for the ops analyst, read once per run.
- Tool use in `main`: 33 runs used `query_metric` only, 9 both tools, 39 `run_sql` only, 11 neither (clarify, decline, restricted). 80 runs attached queries (104 in total, 49 from metric views).
- Six cases failed in `main` only. None is a wrong number from a metric view:
  - seed_16: `mv_outbound_shipped` returns no row for a site without shipped deliveries, and the agent reported Longgang and Dongguan without stating Round Rock = 0 (the trap of the question).
  - c036: the values are correct (78.5 % and 80.2 %, within tolerance of 0.7852 and 0.8016) but labelled `CC1000` and `CC2000`; the scorer's key matching requires a non-alphanumeric boundary around the key `1000`, so the labels did not match. Scoring strictness, not an agent error.
  - c096 (site manager, picks per operator): `mv_warehouse_tasks` has no operator dimension (the column is masked for most roles), so the agent fell back to `run_sql` and took the wrong fiscal period boundaries (period 10 instead of 11) instead of reading them from T009B.
  - c123 and c130: the question asks for a total; the agent reported only a breakdown (by task type, by site). The faithfulness gate does not accept a sum that is not a cell of a result, and the agent did not run the total as its own query.
  - c098 (executive): `list_metrics` returns an empty menu for the executive role, and the agent answered `decline` instead of `policy_restricted`.
- c105 failed in `main_v2` only (the open lines of one supplier were missed); c142 and c154 fail in both arms as in earlier runs.
- Follow-ups proposed to the owner: a compact `list_metrics` (names and one-line descriptions, details on demand); an operator dimension for the people-level roles; prompt guidance (report the asked total as its own query, a group absent from a metric result is zero, derive fiscal dates from T009B when falling back to SQL); scorer key boundaries for digit keys preceded by letters; executive access to the metric views. Then re-run the dev split.
