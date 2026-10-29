# Evaluation report: bank_v3 (bank-v3-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.3.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 6 | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | - | $0.33 | $0.054 | 4.8 | 2.8 | 22s |
| baseline | claude-sonnet-5-5 | 6 | 5/6 (83%) | 5/6 (83%) | 6/6 (100%) | - | $0.40 | $0.066 | 6.0 | 4.3 | 25s |
| baseline_bare | claude-sonnet-5-5 | 6 | 6/6 (100%) | 6/6 (100%) | 6/6 (100%) | - | $0.37 | $0.061 | 7.5 | 9.3 | 34s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs baseline_bare | full stack vs naive (knowledge base, prompt, and metadata documentation) | 6 | +0.0 pp (+0.0 pp, +0.0 pp) | 0 / 0 | 1.000 | $-0.007 ($-0.022, $+0.008) |
| main vs baseline | knowledge base + governed prompt, documented metadata | 6 | +16.7 pp (+0.0 pp, +50.0 pp) | 1 / 0 | 1.000 | $-0.012 ($-0.037, $+0.009) |
| baseline vs baseline_bare | metadata documentation, no knowledge base | 6 | -16.7 pp (-50.0 pp, +0.0 pp) | 0 / 1 | 1.000 | $+0.005 ($-0.018, $+0.023) |

## By category

| Category | main | baseline | baseline_bare |
|---|---|---|---|
| adhoc | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| covered | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| investigation | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| multi_step | 1/1 (100%) | 0/1 (0%) | 1/1 (100%) |
| trap | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |
| unfamiliar | 1/1 (100%) | 1/1 (100%) | 1/1 (100%) |

## By split

| Split | main | baseline | baseline_bare |
|---|---|---|---|
| dev | 6/6 (100%) | 5/6 (83%) | 6/6 (100%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | baseline | baseline_bare |
|---|---|---|---|---|
| c117 | covered | 1/1 ($0.06) | 1/1 ($0.12) | 1/1 ($0.08) |
| c119 | adhoc | 1/1 ($0.06) | 1/1 ($0.05) | 1/1 ($0.05) |
| c121 | unfamiliar | 1/1 ($0.04) | 1/1 ($0.03) | 1/1 ($0.02) |
| c123 | trap | 1/1 ($0.05) | 1/1 ($0.05) | 1/1 ($0.05) |
| c125 | multi_step | 1/1 ($0.04) | 0/1 ($0.07) — missing median_minutes{'nltyp': '0010'}=169.916667 | 1/1 ($0.05) |
| c127 | investigation | 1/1 ($0.08) | 1/1 ($0.07) | 1/1 ($0.11) |

## Notes (2026-09-28)

- Batch: the six new dev-split cases of `bank_v3` (EWM inbound and putaway, generator 0.3.0), Sonnet 5.5,
  three arms. Spend $1.10.
- `main` 6/6, `baseline` 5/6, `baseline_bare` 6/6. Most inbound questions are answerable from the table
  documentation and the data itself; the one discriminating case is c125, where `baseline` took the
  inbound delivery creation (the supplier's shipping notification) as the dock time and reported days
  instead of minutes. Both investigation-style runs (c127, scenario A2 with the material group named)
  passed on every arm; the unguided version (c126) is in the test split.
- Case correction from this run: c117 and c118 accept dock-to-stock measured from the ECC goods receipt
  posting (about two minutes earlier than the putaway task creation) with a 6 % tolerance, as c124 already
  did. Before the correction `baseline` scored 4/6 (c117 was 5.0 % off on Round Rock).
