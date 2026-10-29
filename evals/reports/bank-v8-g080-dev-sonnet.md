# Evaluation report: bank_v8 (bank-v8-g080-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.8.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Cache read | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 92 | 89/92 (97%) | 78/81 (96%) | 92/92 (100%) | - | 446/446 (100%) | $4.35 | $0.047 | 87% | 4.3 | 2.7 | 25s |

## By category

| Category | main |
|---|---|
| adhoc | 14/14 (100%) |
| clarify | 3/3 (100%) |
| covered | 19/19 (100%) |
| decline | 2/2 (100%) |
| investigation | 5/6 (83%) |
| multi_step | 6/6 (100%) |
| restricted | 7/7 (100%) |
| trap | 20/22 (91%) |
| unfamiliar | 13/13 (100%) |

## By split

| Split | main |
|---|---|
| dev | 89/92 (97%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main |
|---|---|---|
| seed_01 | covered | 1/1 ($0.05) |
| seed_02 | covered | 1/1 ($0.06) |
| seed_04 | covered | 1/1 ($0.05) |
| seed_08 | adhoc | 1/1 ($0.06) |
| seed_10 | unfamiliar | 1/1 ($0.04) |
| seed_12 | unfamiliar | 1/1 ($0.02) |
| seed_14 | trap | 1/1 ($0.04) |
| seed_16 | trap | 1/1 ($0.04) |
| seed_17 | restricted | 1/1 ($0.01) |
| seed_19 | clarify | 1/1 ($0.06) |
| c021 | covered | 1/1 ($0.03) |
| c023 | covered | 1/1 ($0.02) |
| c025 | covered | 1/1 ($0.03) |
| c026 | covered | 1/1 ($0.04) |
| c028 | covered | 1/1 ($0.03) |
| c029 | covered | 1/1 ($0.03) |
| c031 | covered | 1/1 ($0.03) |
| c033 | covered | 1/1 ($0.03) |
| c035 | covered | 1/1 ($0.03) |
| c036 | covered | 1/1 ($0.03) |
| c038 | covered | 1/1 ($0.05) |
| c039 | covered | 1/1 ($0.03) |
| c041 | adhoc | 1/1 ($0.04) |
| c043 | adhoc | 1/1 ($0.06) |
| c045 | adhoc | 1/1 ($0.04) |
| c048 | adhoc | 1/1 ($0.04) |
| c050 | adhoc | 1/1 ($0.03) |
| c055 | adhoc | 1/1 ($0.04) |
| c056 | adhoc | 1/1 ($0.09) |
| c057 | unfamiliar | 1/1 ($0.04) |
| c059 | unfamiliar | 1/1 ($0.04) |
| c060 | unfamiliar | 1/1 ($0.02) |
| c062 | unfamiliar | 1/1 ($0.05) |
| c064 | unfamiliar | 1/1 ($0.08) |
| c066 | unfamiliar | 1/1 ($0.02) |
| c067 | unfamiliar | 1/1 ($0.03) |
| c071 | trap | 1/1 ($0.02) |
| c073 | trap | 1/1 ($0.09) |
| c075 | trap | 1/1 ($0.07) |
| c077 | trap | 1/1 ($0.03) |
| c079 | trap | 1/1 ($0.07) |
| c081 | trap | 1/1 ($0.04) |
| c083 | trap | 1/1 ($0.05) |
| c085 | clarify | 1/1 ($0.01) |
| c089 | clarify | 1/1 ($0.01) |
| c090 | decline | 1/1 ($0.01) |
| c094 | decline | 1/1 ($0.01) |
| c096 | restricted | 1/1 ($0.06) |
| c098 | restricted | 1/1 ($0.04) |
| c100 | restricted | 1/1 ($0.02) |
| c101 | trap | 1/1 ($0.03) |
| c103 | trap | 1/1 ($0.06) |
| c109 | trap | 0/1 ($0.06) — missing hub_demand_qty=141723.000 |
| c110 | trap | 1/1 ($0.05) |
| c112 | trap | 1/1 ($0.03) |
| c114 | trap | 1/1 ($0.06) |
| c116 | trap | 1/1 ($0.04) |
| c105 | multi_step | 1/1 ($0.04) |
| c107 | multi_step | 1/1 ($0.03) |
| c117 | covered | 1/1 ($0.03) |
| c119 | adhoc | 1/1 ($0.04) |
| c121 | unfamiliar | 1/1 ($0.04) |
| c123 | trap | 1/1 ($0.05) |
| c125 | multi_step | 1/1 ($0.03) |
| c127 | investigation | 1/1 ($0.13) |
| c128 | covered | 1/1 ($0.04) |
| c130 | adhoc | 1/1 ($0.06) |
| c132 | unfamiliar | 1/1 ($0.07) |
| c133 | trap | 1/1 ($0.03) |
| c137 | multi_step | 1/1 ($0.05) |
| c136 | investigation | 1/1 ($0.12) |
| c138 | adhoc | 1/1 ($0.05) |
| c140 | trap | 1/1 ($0.10) |
| c144 | multi_step | 1/1 ($0.05) |
| c142 | investigation | 0/1 ($0.07) — missing mention RFID \| LTS, mention confirmed late \| late confirmation \| confirmed later \| delayed confirmation \| confirmation delay \| confirm late \| confirmations lag \| after the physical pick \| after the pick |
| c154 | trap | 0/1 ($0.04) — missing pick_tasks=33, mention duplicat \| copies \| copied \| twice |
| c156 | trap | 1/1 ($0.07) |
| c158 | investigation | 1/1 ($0.05) |
| c160 | restricted | 1/1 ($0.01) |
| c162 | restricted | 1/1 ($0.02) |
| c163 | covered | 1/1 ($0.05) |
| c164 | adhoc | 1/1 ($0.10) |
| c165 | investigation | 1/1 ($0.14) |
| c167 | adhoc | 1/1 ($0.07) |
| c168 | investigation | 1/1 ($0.12) |
| c174 | unfamiliar | 1/1 ($0.06) |
| c176 | unfamiliar | 1/1 ($0.05) |
| c177 | trap | 1/1 ($0.07) |
| c179 | multi_step | 1/1 ($0.07) |
| c180 | covered | 1/1 ($0.05) |
| c182 | restricted | 1/1 ($0.04) |
| c184 | adhoc | 1/1 ($0.06) |

## Notes (2026-10-19)

- First run of the data agent on generator 0.8.0 (92 dev cases after the retirements of decision record 003; `main` only, prompt `main/v4`, Sonnet 5.5): 87/92 as run, **89/92 (97 %)** after two reference corrections; $4.35 for 92 runs ($0.047 per question, 87 % of prompt tokens read from the cache), no errored runs. Same model and prompt as `bank-v8-dev-sonnet` on 0.7.0 (`main` 104/106 there); the case sets differ, so the two rates are not paired.
- Adjudication of the five failures (`EVAL_GUIDE.md` §8):
  - **c176, reference wrong.** The agent read the daily snapshot of 2026-09-20 (17,250 units on 335 materials) and is right; the reference read `MARD`, which on 0.8 already holds Longgang's staging and consumption of the morning of 2026-09-21 (the extraction runs at 13:00 local). Reference SQL moved to the snapshot ([card](../failures/bank-v8-g080-dev-sonnet-c176.md)).
  - **c184, reference wrong.** The agent named the four suppliers of the 0.8 engine run; `must_mention` still listed the five of 0.7 because `refresh` does not recompute it ([card](../failures/bank-v8-g080-dev-sonnet-c184.md)).
  - **c109, agent wrong (interpretation), new on 0.8.** Asked for Longgang's outbound demand "including the replenishment of Dongguan", the agent reported delivery lines and left the transfers out, following the rewritten `concept.internal_transfers` rule that transfers to the overflow hub are not demand ([card](../failures/bank-v8-g080-dev-sonnet-c109.md), waits for the owner's hypothesis review).
  - **c142 and c154, adjudicated by the owner on 2026-10-19.** c142 moves to a documented-cause version (the KB will state that emergency picks are often confirmed late); c154 stays a discovery task and leaves the headline pass rate. Both fail as before; c154's reference moved from 40 to 33 tasks on 0.8, and the agent's 66 is again the duplicated count. **Headline without c154: 89/91 (98 %).**
- Found on the way, not part of this batch: test-split case c186 asks on which day the projection of material 40001720 at Round Rock first goes negative, but on generator 0.8 with engine 1.0 it never does (`first_short_day` is NULL, projected stock 38 on 2026-10-05) while `must_mention` still expects 2026-09-22. It needs a new material or a new question before the P1 milestone run on the test split.
