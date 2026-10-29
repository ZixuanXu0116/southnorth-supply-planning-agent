# Evaluation report: bank_v6 (v2-vs-v1-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.6.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 89 | 87/89 (98%) | 76/78 (97%) | 89/89 (100%) | - | 388/388 (100%) | $3.88 | $0.044 | 4.5 | 2.6 | 21s |
| main_v1 | claude-sonnet-5-5 | 89 | 86/89 (97%) | 75/78 (96%) | 89/89 (100%) | - | 353/398 (89%) | $3.49 | $0.039 | 4.3 | 2.6 | 19s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs main_v1 | task card + numeric faithfulness gate (prompt v2 vs v1) | 89 | +1.1 pp (+0.0 pp, +3.4 pp) | 1 / 0 | 1.000 | $+0.004 ($+0.003, $+0.006) |

## By category

| Category | main | main_v1 |
|---|---|---|
| adhoc | 17/17 (100%) | 17/17 (100%) |
| clarify | 4/4 (100%) | 4/4 (100%) |
| covered | 17/17 (100%) | 16/17 (94%) |
| decline | 4/4 (100%) | 4/4 (100%) |
| investigation | 3/4 (75%) | 3/4 (75%) |
| multi_step | 5/5 (100%) | 5/5 (100%) |
| restricted | 5/5 (100%) | 5/5 (100%) |
| trap | 21/22 (95%) | 21/22 (95%) |
| unfamiliar | 11/11 (100%) | 11/11 (100%) |

## By split

| Split | main | main_v1 |
|---|---|---|
| dev | 87/89 (98%) | 86/89 (97%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | main_v1 |
|---|---|---|---|
| seed_01 | covered | 1/1 ($0.06) | 1/1 ($0.06) |
| seed_02 | covered | 1/1 ($0.04) | 1/1 ($0.05) |
| seed_04 | covered | 1/1 ($0.05) | 1/1 ($0.06) |
| seed_06 | adhoc | 1/1 ($0.03) | 1/1 ($0.03) |
| seed_08 | adhoc | 1/1 ($0.07) | 1/1 ($0.05) |
| seed_10 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| seed_12 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.02) |
| seed_14 | trap | 1/1 ($0.06) | 1/1 ($0.03) |
| seed_16 | trap | 1/1 ($0.05) | 1/1 ($0.05) |
| seed_17 | restricted | 1/1 ($0.01) | 1/1 ($0.01) |
| seed_19 | clarify | 1/1 ($0.05) | 1/1 ($0.05) |
| c021 | covered | 1/1 ($0.06) | 1/1 ($0.04) |
| c023 | covered | 1/1 ($0.03) | 1/1 ($0.03) |
| c025 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c026 | covered | 1/1 ($0.05) | 1/1 ($0.05) |
| c028 | covered | 1/1 ($0.09) | 0/1 ($0.06) — missing median_minutes{'lgnum': 'RR01'}=188.20833349999998 |
| c029 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c031 | covered | 1/1 ($0.03) | 1/1 ($0.02) |
| c033 | covered | 1/1 ($0.03) | 1/1 ($0.04) |
| c035 | covered | 1/1 ($0.07) | 1/1 ($0.04) |
| c036 | covered | 1/1 ($0.05) | 1/1 ($0.05) |
| c038 | covered | 1/1 ($0.05) | 1/1 ($0.04) |
| c039 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c041 | adhoc | 1/1 ($0.03) | 1/1 ($0.02) |
| c043 | adhoc | 1/1 ($0.05) | 1/1 ($0.05) |
| c045 | adhoc | 1/1 ($0.03) | 1/1 ($0.02) |
| c046 | adhoc | 1/1 ($0.08) | 1/1 ($0.05) |
| c048 | adhoc | 1/1 ($0.02) | 1/1 ($0.02) |
| c050 | adhoc | 1/1 ($0.07) | 1/1 ($0.06) |
| c051 | adhoc | 1/1 ($0.05) | 1/1 ($0.05) |
| c053 | adhoc | 1/1 ($0.03) | 1/1 ($0.03) |
| c055 | adhoc | 1/1 ($0.03) | 1/1 ($0.03) |
| c056 | adhoc | 1/1 ($0.05) | 1/1 ($0.05) |
| c057 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c059 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c060 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.01) |
| c062 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.03) |
| c064 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.05) |
| c066 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| c067 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.04) |
| c069 | trap | 1/1 ($0.05) | 1/1 ($0.04) |
| c071 | trap | 1/1 ($0.02) | 1/1 ($0.04) |
| c073 | trap | 1/1 ($0.06) | 1/1 ($0.06) |
| c075 | trap | 1/1 ($0.08) | 1/1 ($0.06) |
| c077 | trap | 1/1 ($0.05) | 1/1 ($0.03) |
| c079 | trap | 1/1 ($0.05) | 1/1 ($0.05) |
| c081 | trap | 1/1 ($0.02) | 1/1 ($0.03) |
| c083 | trap | 1/1 ($0.05) | 1/1 ($0.04) |
| c085 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c087 | clarify | 1/1 ($0.02) | 1/1 ($0.01) |
| c089 | clarify | 1/1 ($0.01) | 1/1 ($0.01) |
| c090 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c092 | decline | 1/1 ($0.02) | 1/1 ($0.03) |
| c094 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c096 | restricted | 1/1 ($0.07) | 1/1 ($0.08) |
| c098 | restricted | 1/1 ($0.06) | 1/1 ($0.05) |
| c100 | restricted | 1/1 ($0.02) | 1/1 ($0.02) |
| c101 | trap | 1/1 ($0.05) | 1/1 ($0.04) |
| c103 | trap | 1/1 ($0.04) | 1/1 ($0.03) |
| c109 | trap | 1/1 ($0.07) | 1/1 ($0.06) |
| c110 | trap | 1/1 ($0.05) | 1/1 ($0.05) |
| c112 | trap | 1/1 ($0.04) | 1/1 ($0.04) |
| c114 | trap | 1/1 ($0.04) | 1/1 ($0.05) |
| c116 | trap | 1/1 ($0.04) | 1/1 ($0.05) |
| c105 | multi_step | 1/1 ($0.07) | 1/1 ($0.05) |
| c107 | multi_step | 1/1 ($0.04) | 1/1 ($0.05) |
| c117 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c119 | adhoc | 1/1 ($0.05) | 1/1 ($0.05) |
| c121 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.04) |
| c123 | trap | 1/1 ($0.06) | 1/1 ($0.05) |
| c125 | multi_step | 1/1 ($0.06) | 1/1 ($0.04) |
| c127 | investigation | 1/1 ($0.12) | 1/1 ($0.11) |
| c128 | covered | 1/1 ($0.03) | 1/1 ($0.03) |
| c130 | adhoc | 1/1 ($0.06) | 1/1 ($0.06) |
| c132 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c133 | trap | 1/1 ($0.03) | 1/1 ($0.02) |
| c137 | multi_step | 1/1 ($0.07) | 1/1 ($0.04) |
| c136 | investigation | 1/1 ($0.05) | 1/1 ($0.06) |
| c138 | adhoc | 1/1 ($0.03) | 1/1 ($0.04) |
| c140 | trap | 1/1 ($0.07) | 1/1 ($0.06) |
| c144 | multi_step | 1/1 ($0.04) | 1/1 ($0.04) |
| c142 | investigation | 0/1 ($0.06) — missing mention RFID \| LTS, mention confirmed late \| late confirmation \| confirmed later \| delayed confirmation \| confirmation delay \| confirm late \| confirmations lag \| after the physical pick \| after the pick | 0/1 ($0.06) — missing mention confirmed late \| late confirmation \| confirmed later \| delayed confirmation \| confirmation delay \| confirm late \| confirmations lag \| after the physical pick \| after the pick |
| c146 | adhoc | 1/1 ($0.03) | 1/1 ($0.02) |
| c148 | adhoc | 1/1 ($0.05) | 1/1 ($0.05) |
| c150 | restricted | 1/1 ($0.04) | 1/1 ($0.04) |
| c152 | decline | 1/1 ($0.01) | 1/1 ($0.01) |
| c154 | trap | 0/1 ($0.04) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice | 0/1 ($0.02) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice |
| c156 | trap | 1/1 ($0.04) | 1/1 ($0.04) |
| c158 | investigation | 1/1 ($0.06) | 1/1 ($0.04) |

## Notes (2026-10-06)

- Batch: every dev-split case of `bank_v6` (89 cases), Sonnet 5.5, two arms: `main` (prompt v2 with the task
  card and the numeric faithfulness gate) and `main_v1` (the previous main). Spend $7.37.
- **Accuracy is unchanged**: `main` 87/89, `main_v1` 86/89 (one discordant case, p = 1.0). The discordant case
  (c028) is run-to-run variance, not the gate: `main_v1` reported a `percentile_approx` median for RR01 (171.35)
  instead of the exact one (188.21); `main` reported the exact median in its first submission.
- **Faithfulness**: every value of the accepted `main` answers is traced to its result (388/388). Without the
  gate, 89 % of values are traced (353/398); 19 of 89 `main_v1` runs report at least one untraced value: 18
  values without a `result_id` and 27 derived outside SQL (sums across plants, ratios, conversions). Only one
  of those 19 runs failed (c154, for another reason), so on this dev set untraced numbers were mostly correct
  arithmetic done by the model; the gate buys auditability, not accuracy.
- **Cost of the gate**: 14 of 89 `main` runs were rejected once (66 of the 70 untraced values had no
  `result_id`; 4 were derived). All 14 resubmitted traced values; 13 passed (c154 failed as before). Rejected
  runs cost $0.063 on average versus $0.045 for the same cases in `main_v1`; overall +$0.004 per question
  (+10 %).
- **The task card is almost never used**: 1 of 89 `main` runs called `update_task_card` although prompt v2
  asks for it. A prompt instruction alone does not make Sonnet 5.5 (effort medium) keep a task card in
  single-question runs.
- Failures shared by both arms: c142 (RFID reads not used) and c154 (A6 duplicates not detected).
- Case correction from this run: c092 (license exception for a shipment to Russia) now also accepts
  `policy_restricted`, as c090, c091, and c152 do; both arms refused with that behavior. Before the
  correction: `main` 86/89, `main_v1` 85/89.
