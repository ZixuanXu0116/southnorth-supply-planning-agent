# Evaluation report: bank_v4 (bank-v4-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.4.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 6 | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | - | $0.31 | $0.052 | 4.3 | 3.0 | 20s |
| baseline | claude-sonnet-5-5 | 6 | 5/6 (83%) | 5/6 (83%) | 6/6 (100%) | - | $0.32 | $0.054 | 5.5 | 3.0 | 21s |
| baseline_bare | claude-sonnet-5-5 | 6 | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | - | $0.27 | $0.044 | 6.7 | 6.3 | 27s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs baseline_bare | full stack vs naive (knowledge base, prompt, and metadata documentation) | 6 | +0.0 pp (+0.0 pp, +0.0 pp) | 0 / 0 | 1.000 | $+0.008 ($-0.011, $+0.023) |
| main vs baseline | knowledge base + governed prompt, documented metadata | 6 | +16.7 pp (+0.0 pp, +50.0 pp) | 1 / 0 | 1.000 | $-0.002 ($-0.015, $+0.010) |
| baseline vs baseline_bare | metadata documentation, no knowledge base | 6 | -16.7 pp (-50.0 pp, +0.0 pp) | 0 / 1 | 1.000 | $+0.009 ($-0.009, $+0.026) |

## By category

| Category | main | baseline | baseline_bare |
|---|---|---|---|
| adhoc | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| covered | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| investigation | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| multi_step | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| trap | 1/1 (100%) | 0/1 (0%) | 1/1 (100%) |
| unfamiliar | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |

## By split

| Split | main | baseline | baseline_bare |
|---|---|---|---|
| dev | 6/6 (100%) | 5/6 (83%) | 6/6 (100%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | baseline | baseline_bare |
|---|---|---|---|---|
| c128 | covered | 1/1 ($0.04) | 1/1 ($0.05) | 1/1 ($0.04) |
| c130 | adhoc | 1/1 ($0.08) | 1/1 ($0.06) | 1/1 ($0.04) |
| c132 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.03) | 1/1 ($0.03) |
| c133 | trap | 1/1 ($0.04) | 0/1 ($0.03) — missing presentations=66 | 1/1 ($0.02) |
| c137 | multi_step | 1/1 ($0.05) | 1/1 ($0.05) | 1/1 ($0.08) |
| c136 | investigation | 1/1 ($0.06) | 1/1 ($0.09) | 1/1 ($0.05) |

## Notes (2026-09-29)

- Batch: the six new dev-split cases of `bank_v4` (AutoStore controller logs, scenario A1; generator 0.4.0),
  Sonnet 5.5, three arms. Spend $0.90.
- `main` 6/6, `baseline` 5/6, `baseline_bare` 6/6. The one miss: on c133 `baseline` counted AutoStore pick tasks
  as presentations instead of reading the presentation log (62 vs 66). The scenario A1 investigation with the
  window given (c136) passed on every arm (all named both closed ports); the unguided version (c135) is in the
  test split.
- As with bank v3, questions on new raw tables are mostly answerable from the table documentation; the
  knowledge base matters most where a definition or a cross-source rule is needed.
