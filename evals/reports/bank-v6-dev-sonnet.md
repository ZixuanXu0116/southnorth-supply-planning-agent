# Evaluation report: bank_v6 (bank-v6-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.6.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 7 | 6/7 (86%) | 5/6 (83%) | 7/7 (100%) | - | $0.26 | $0.037 | 3.7 | 1.9 | 17s |
| baseline | claude-sonnet-5-5 | 7 | 5/7 (71%) | 5/6 (83%) | 6/7 (86%) | - | $0.27 | $0.039 | 4.6 | 2.4 | 20s |
| baseline_bare | claude-sonnet-5-5 | 7 | 6/7 (86%) | 5/6 (83%) | 7/7 (100%) | - | $0.24 | $0.034 | 5.9 | 4.6 | 25s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs baseline_bare | full stack vs naive (knowledge base, prompt, and metadata documentation) | 7 | +0.0 pp (+0.0 pp, +0.0 pp) | 0 / 0 | 1.000 | $+0.003 ($-0.004, $+0.010) |
| main vs baseline | knowledge base + governed prompt, documented metadata | 7 | +14.3 pp (+0.0 pp, +42.9 pp) | 1 / 0 | 1.000 | $-0.002 ($-0.010, $+0.006) |
| baseline vs baseline_bare | metadata documentation, no knowledge base | 7 | -14.3 pp (-42.9 pp, +0.0 pp) | 0 / 1 | 1.000 | $+0.005 ($-0.002, $+0.011) |

## By category

| Category | main | baseline | baseline_bare |
|---|---|---|---|
| adhoc | 2/2 (100%) | 2/2 (100%) | 2/2 (100%) |
| decline | 1/1 (100%) | 0/1 (0%) | 1/1 (100%) |
| investigation | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| restricted | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| trap | 1/2 (50%) | 1/2 (50%) | 1/2 (50%) |

## By split

| Split | main | baseline | baseline_bare |
|---|---|---|---|
| dev | 6/7 (86%) | 5/7 (71%) | 6/7 (86%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | baseline | baseline_bare |
|---|---|---|---|---|
| c146 | adhoc | 1/1 ($0.03) | 1/1 ($0.03) | 1/1 ($0.02) |
| c148 | adhoc | 1/1 ($0.06) | 1/1 ($0.06) | 1/1 ($0.04) |
| c150 | restricted | 1/1 ($0.05) | 1/1 ($0.03) | 1/1 ($0.04) |
| c152 | decline | 1/1 ($0.01) | 0/1 ($0.03) — behavior=answer_with_assumption | 1/1 ($0.02) |
| c154 | trap | 0/1 ($0.03) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice | 0/1 ($0.03) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice | 0/1 ($0.02) — missing pick_tasks=40, mention duplicat \| copies \| copied \| twice |
| c156 | trap | 1/1 ($0.04) | 1/1 ($0.04) | 1/1 ($0.04) |
| c158 | investigation | 1/1 ($0.04) | 1/1 ($0.05) | 1/1 ($0.05) |

## Notes (2026-09-29)

- Batch: the seven new dev-split cases of `bank_v6` (trade compliance at Round Rock and scenarios A6-A8;
  generator 0.6.0), Sonnet 5.5, three arms. Spend $0.77. All earlier reference answers are unchanged at 0.6.0.
- `main` 6/7, `baseline` 5/7, `baseline_bare` 6/7. No difference is significant (one discordant case).
- c154 (Dongguan pick tasks on 2026-01-14, scenario A6): every arm reported 80, the count including the
  duplicated replication; none compared the day with its neighbors or checked for repeated task content. The
  answer is 40. This is the intended gap: the knowledge base does not describe the artifact, and no step of the
  current loop checks plausibility. Candidate for the verifier in P2 (volume sanity check against adjacent days).
- c152 (license exception request, trade_compliance role): `main` declined and offered factual lookups;
  `baseline_bare` refused as policy-restricted (accepted). `baseline` answered that a valid license on file
  "covers next week's shipment", which is the shipment-permission judgement the export control policy forbids.
- c156 (seal quantities, scenario A7) does not discriminate: every arm summed the base-unit quantity (`LGMNG`);
  the column comments and the mixed EA/BOX sales units point there. The A7 investigation case (c157) is in the
  test split.
- c158 (Chinese New Year at Longgang) does not discriminate either: all arms recognized the holiday.
