# Evaluation report: bank_v8 (bank-v8-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.7.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 106 | 104/106 (98%) | 90/92 (98%) | 106/106 (100%) | - | 495/495 (100%) | $4.90 | $0.046 | 4.3 | 2.8 | 25s |
| main_v2 | claude-sonnet-5-5 | 106 | 102/106 (96%) | 88/92 (96%) | 106/106 (100%) | - | 471/471 (100%) | $4.47 | $0.042 | 4.5 | 2.6 | 22s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs main_v2 | governed metric views and query_metric (prompt v4 vs v2) | 106 | +1.9 pp (+0.0 pp, +4.7 pp) | 2 / 0 | 0.500 | $+0.004 ($+0.001, $+0.007) |

## By category

| Category | main | main_v2 |
|---|---|---|
| adhoc | 22/22 (100%) | 22/22 (100%) |
| clarify | 4/4 (100%) | 4/4 (100%) |
| covered | 19/19 (100%) | 18/19 (95%) |
| decline | 4/4 (100%) | 4/4 (100%) |
| investigation | 6/7 (86%) | 6/7 (86%) |
| multi_step | 6/6 (100%) | 6/6 (100%) |
| restricted | 8/8 (100%) | 8/8 (100%) |
| trap | 22/23 (96%) | 21/23 (91%) |
| unfamiliar | 13/13 (100%) | 13/13 (100%) |

## By split

| Split | main | main_v2 |
|---|---|---|
| dev | 104/106 (98%) | 102/106 (96%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | main_v2 |
|---|---|---|---|
| seed_01 | covered | 1/1 ($0.04) | 1/1 ($0.07) |
| seed_02 | covered | 1/1 ($0.05) | 1/1 ($0.04) |
| seed_04 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| seed_06 | adhoc | 1/1 ($0.05) | 1/1 ($0.03) |
| seed_08 | adhoc | 1/1 ($0.05) | 1/1 ($0.06) |
| seed_10 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| seed_12 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.02) |
| seed_14 | trap | 1/1 ($0.05) | 1/1 ($0.04) |
| seed_16 | trap | 1/1 ($0.04) | 1/1 ($0.05) |
| seed_17 | restricted | 1/1 ($0.01) | 1/1 ($0.01) |
| seed_19 | clarify | 1/1 ($0.04) | 1/1 ($0.04) |
| c021 | covered | 1/1 ($0.04) | 1/1 ($0.05) |
| c023 | covered | 1/1 ($0.02) | 1/1 ($0.03) |
| c025 | covered | 1/1 ($0.03) | 1/1 ($0.05) |
| c026 | covered | 1/1 ($0.04) | 1/1 ($0.05) |
| c028 | covered | 1/1 ($0.04) | 1/1 ($0.05) |
| c029 | covered | 1/1 ($0.03) | 1/1 ($0.05) |
| c031 | covered | 1/1 ($0.03) | 1/1 ($0.02) |
| c033 | covered | 1/1 ($0.03) | 1/1 ($0.03) |
| c035 | covered | 1/1 ($0.03) | 1/1 ($0.07) |
| c036 | covered | 1/1 ($0.03) | 1/1 ($0.05) |
| c038 | covered | 1/1 ($0.05) | 1/1 ($0.04) |
| c039 | covered | 1/1 ($0.03) | 1/1 ($0.04) |
| c041 | adhoc | 1/1 ($0.05) | 1/1 ($0.03) |
| c043 | adhoc | 1/1 ($0.05) | 1/1 ($0.04) |
| c045 | adhoc | 1/1 ($0.04) | 1/1 ($0.03) |
| c046 | adhoc | 1/1 ($0.06) | 1/1 ($0.06) |
| c048 | adhoc | 1/1 ($0.04) | 1/1 ($0.02) |
| c050 | adhoc | 1/1 ($0.03) | 1/1 ($0.06) |
| c051 | adhoc | 1/1 ($0.08) | 1/1 ($0.05) |
| c053 | adhoc | 1/1 ($0.06) | 1/1 ($0.04) |
| c055 | adhoc | 1/1 ($0.04) | 1/1 ($0.03) |
| c056 | adhoc | 1/1 ($0.07) | 1/1 ($0.04) |
| c057 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c059 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c060 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| c062 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.02) |
| c064 | unfamiliar | 1/1 ($0.06) | 1/1 ($0.05) |
| c066 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| c067 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c069 | trap | 1/1 ($0.08) | 1/1 ($0.05) |
| c071 | trap | 1/1 ($0.03) | 1/1 ($0.02) |
| c073 | trap | 1/1 ($0.10) | 1/1 ($0.06) |
| c075 | trap | 1/1 ($0.06) | 0/1 ($0.05) — missing receipt_value{'werks': 'CN10', 'waers': 'CNY'}=440300753.79 |
| c077 | trap | 1/1 ($0.03) | 1/1 ($0.03) |
| c079 | trap | 1/1 ($0.07) | 1/1 ($0.04) |
| c081 | trap | 1/1 ($0.03) | 1/1 ($0.03) |
| c083 | trap | 1/1 ($0.06) | 1/1 ($0.05) |
| c085 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c087 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c089 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c090 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c092 | decline | 1/1 ($0.02) | 1/1 ($0.01) |
| c094 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c096 | restricted | 1/1 ($0.08) | 1/1 ($0.06) |
| c098 | restricted | 1/1 ($0.05) | 1/1 ($0.05) |
| c100 | restricted | 1/1 ($0.02) | 1/1 ($0.02) |
| c101 | trap | 1/1 ($0.03) | 1/1 ($0.05) |
| c103 | trap | 1/1 ($0.05) | 1/1 ($0.05) |
| c109 | trap | 1/1 ($0.09) | 1/1 ($0.09) |
| c110 | trap | 1/1 ($0.05) | 1/1 ($0.06) |
| c112 | trap | 1/1 ($0.02) | 1/1 ($0.04) |
| c114 | trap | 1/1 ($0.07) | 1/1 ($0.04) |
| c116 | trap | 1/1 ($0.03) | 1/1 ($0.05) |
| c105 | multi_step | 1/1 ($0.06) | 1/1 ($0.03) |
| c107 | multi_step | 1/1 ($0.03) | 1/1 ($0.04) |
| c117 | covered | 1/1 ($0.03) | 1/1 ($0.04) |
| c119 | adhoc | 1/1 ($0.05) | 1/1 ($0.05) |
| c121 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.04) |
| c123 | trap | 1/1 ($0.06) | 1/1 ($0.04) |
| c125 | multi_step | 1/1 ($0.05) | 1/1 ($0.05) |
| c127 | investigation | 1/1 ($0.12) | 1/1 ($0.10) |
| c128 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c130 | adhoc | 1/1 ($0.07) | 1/1 ($0.08) |
| c132 | unfamiliar | 1/1 ($0.05) | 1/1 ($0.03) |
| c133 | trap | 1/1 ($0.03) | 1/1 ($0.03) |
| c137 | multi_step | 1/1 ($0.06) | 1/1 ($0.04) |
| c136 | investigation | 1/1 ($0.11) | 1/1 ($0.05) |
| c138 | adhoc | 1/1 ($0.03) | 1/1 ($0.04) |
| c140 | trap | 1/1 ($0.09) | 1/1 ($0.06) |
| c144 | multi_step | 1/1 ($0.05) | 1/1 ($0.04) |
| c142 | investigation | 0/1 ($0.06) — missing mention RFID \| LTS, mention confirmed late \| late confirmation \| confirmed later \| delayed confirmation \| confirmation delay \| confirm late \| confirmations lag \| after the physical pick \| after the pick | 0/1 ($0.05) — missing mention confirmed late \| late confirmation \| confirmed later \| delayed confirmation \| confirmation delay \| confirm late \| confirmations lag \| after the physical pick \| after the pick |
| c146 | adhoc | 1/1 ($0.02) | 1/1 ($0.02) |
| c148 | adhoc | 1/1 ($0.07) | 1/1 ($0.05) |
| c150 | restricted | 1/1 ($0.04) | 1/1 ($0.04) |
| c152 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c154 | trap | 0/1 ($0.04) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice | 0/1 ($0.04) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice |
| c156 | trap | 1/1 ($0.06) | 1/1 ($0.04) |
| c158 | investigation | 1/1 ($0.05) | 1/1 ($0.04) |
| c160 | restricted | 1/1 ($0.01) | 1/1 ($0.01) |
| c162 | restricted | 1/1 ($0.01) | 1/1 ($0.01) |
| c163 | covered | 1/1 ($0.05) | 1/1 ($0.05) |
| c164 | adhoc | 1/1 ($0.09) | 1/1 ($0.08) |
| c165 | investigation | 1/1 ($0.13) | 1/1 ($0.17) |
| c167 | adhoc | 1/1 ($0.06) | 1/1 ($0.04) |
| c168 | investigation | 1/1 ($0.11) | 1/1 ($0.09) |
| c170 | adhoc | 1/1 ($0.03) | 1/1 ($0.03) |
| c171 | investigation | 1/1 ($0.06) | 1/1 ($0.09) |
| c173 | adhoc | 1/1 ($0.02) | 1/1 ($0.02) |
| c174 | unfamiliar | 1/1 ($0.05) | 1/1 ($0.04) |
| c176 | unfamiliar | 1/1 ($0.08) | 1/1 ($0.10) |
| c177 | trap | 1/1 ($0.07) | 1/1 ($0.05) |
| c179 | multi_step | 1/1 ($0.05) | 1/1 ($0.04) |
| c180 | covered | 1/1 ($0.04) | 0/1 ($0.02) — missing cases{'site': 'Longgang', 'condition': 'excess'}=14, cases{'site': 'Longgang', 'condition': 'shortage'}=107 |
| c182 | restricted | 1/1 ($0.03) | 1/1 ($0.04) |
| c184 | adhoc | 1/1 ($0.07) | 1/1 ($0.06) |

## Notes (2026-10-13)

- First run on generator 0.7.0 and bank v8 (106 dev cases, 14 of them new): `main` 104/106 (98 %), `main_v2` 102/106 (96 %); $9.37 for 212 runs, no errored runs. This batch replaces the unfinished P1 comparison `semantic-v4-dev-sonnet` (recorded on 0.6.1): both arms ran on the same snapshot.
- Re-scored once after two evaluation fixes, both found by reading the failed runs: (1) fiscal period keys written "P10", "P08", "FP10", or "period 10" now match a `fiscal_period` key (c164, c167 reported every reference value but labeled the periods "P8" and "P11"); (2) c184 asked which suppliers the agent-eligible cases involve but its answer spec demanded per-supplier counts; it now checks the total and the five supplier names. Before the fixes: `main` 101/106, `main_v2` 100/106.
- New cases: both arms answer the A3, A4, and A5 investigations (c165, c168, c171) with the expected cause and timing, keep the staging and consumption double count out (c177), read the production supply area (c176) and planned-order requirements (c179), and answer the value-at-risk question (c182) with `policy_restricted`. c180 fails in `main_v2` only (raw-table query grouped Dongguan under Longgang differently).
- Remaining failures in `main`: c142 and c154, which have failed in every arm since they were added (late RFID confirmations and the duplicated replication day are not documented).
- The planning-case answers of this batch refer to engine 0.1.0; engine 0.2.0 (hub demand includes network replenishment) was published after the batch and the reference answers were refreshed.
