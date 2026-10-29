# Evaluation report: bank_v5 (bank-v5-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.5.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 4 | 3/4 (75%) | 3/4 (75%) | 4/4 (100%) | - | $0.26 | $0.065 | 5.2 | 3.5 | 26s |
| baseline | claude-sonnet-5-5 | 4 | 2/4 (50%) | 2/4 (50%) | 4/4 (100%) | - | $0.24 | $0.059 | 6.2 | 4.2 | 23s |
| baseline_bare | claude-sonnet-5-5 | 4 | 1/4 (25%) | 1/4 (25%) | 4/4 (100%) | - | $0.20 | $0.049 | 7.2 | 6.8 | 30s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs baseline_bare | full stack vs naive (knowledge base, prompt, and metadata documentation) | 4 | +50.0 pp (+0.0 pp, +100.0 pp) | 2 / 0 | 0.500 | $+0.016 ($+0.009, $+0.023) |
| main vs baseline | knowledge base + governed prompt, documented metadata | 4 | +25.0 pp (+0.0 pp, +75.0 pp) | 1 / 0 | 1.000 | $+0.006 ($-0.013, $+0.031) |
| baseline vs baseline_bare | metadata documentation, no knowledge base | 4 | +25.0 pp (+0.0 pp, +75.0 pp) | 1 / 0 | 1.000 | $+0.010 ($-0.009, $+0.025) |

## By category

| Category | main | baseline | baseline_bare |
|---|---|---|---|
| adhoc | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| investigation | 0/1 (0%) | 0/1 (0%) | 0/1 (0%) |
| multi_step | 1/1 (100%) | 1/1 (100%) | 0/1 (0%) |
| trap | 1/1 (100%) | 0/1 (0%) | 0/1 (0%) |

## By split

| Split | main | baseline | baseline_bare |
|---|---|---|---|
| dev | 3/4 (75%) | 2/4 (50%) | 1/4 (25%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | baseline | baseline_bare |
|---|---|---|---|---|
| c138 | adhoc | 1/1 ($0.06) | 1/1 ($0.05) | 1/1 ($0.03) |
| c140 | trap | 1/1 ($0.10) | 0/1 ($0.06) — missing median_minutes=15.516667 | 0/1 ($0.07) — missing median_minutes=15.516667 |
| c144 | multi_step | 1/1 ($0.05) | 1/1 ($0.05) | 0/1 ($0.04) — missing loaded=12, left_storage=9 |
| c142 | investigation | 0/1 ($0.06) — missing mention RFID | LTS, mention confirmed late | late confirmation | confirmed later | delayed confirmation | confirmation delay | confirm late | confirmations lag | after the physical pick | after the pick | 0/1 ($0.08) — missing mention RFID | LTS, mention confirmed late | late confirmation | confirmed later | delayed confirmation | confirmation delay | confirm late | confirmations lag | after the physical pick | after the pick | 0/1 ($0.05) — missing mention RFID | LTS, mention confirmed late | late confirmation | confirmed later | delayed confirmation | confirmation delay | confirm late | confirmations lag | after the physical pick | after the pick |

## Notes (2026-09-29)

- Batch: the four new dev-split cases of `bank_v5` (RFID tote reads at Round Rock, trap 10; generator 0.5.0),
  Sonnet 5.5, three arms. Spend $0.70.
- `main` 3/4, `baseline` 2/4, `baseline_bare` 1/4 (after the reference fix below).
- c140 (physical emergency pick time): only `main` used the exit reads (15.5 min); the other arms reported the
  EWM confirmation-based time, which is about twice as long.
- c142 (is the emergency pick speed in EWM real?): every arm failed. None looked at the RFID reads; all
  compared EWM confirmation times only. This is the intended exploration gap (the knowledge base documents the
  RFID table but not the late confirmations) and a candidate for the explorer sub-agent in P2.
- Reference fix: the first c144 reference SQL used a correlated scalar subquery over a CTE with ORDER BY/LIMIT,
  and Databricks evaluated the CTE again and picked another day (2026-09-04, 5 totes instead of 2026-09-02, 12).
  The agents' answers were right. The reference now aggregates per day and takes the top row; c105, c106, and
  c137 (same CTE pattern with joins) were verified with independent queries. Before the fix all three arms
  failed c144.
