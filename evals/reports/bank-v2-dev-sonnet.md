# Evaluation report: bank_v2 (bank-v2-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.2.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 9 | 9/9 (100%) | 9/9 (100%) | 9/9 (100%) | - | $0.46 | $0.052 | 5.0 | 3.2 | 22s |
| baseline | claude-sonnet-5-5 | 9 | 5/9 (56%) | 5/9 (56%) | 9/9 (100%) | - | $0.58 | $0.064 | 6.2 | 4.9 | 26s |
| baseline_bare | claude-sonnet-5-5 | 9 | 4/9 (44%) | 4/9 (44%) | 8/9 (89%) | - | $0.51 | $0.056 | 7.7 | 6.6 | 32s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs baseline_bare | full stack vs naive (knowledge base, prompt, and metadata documentation) | 9 | +55.6 pp (+22.2 pp, +88.9 pp) | 5 / 0 | 0.062 | $-0.005 ($-0.016, $+0.006) |
| main vs baseline | knowledge base + governed prompt, documented metadata | 9 | +44.4 pp (+11.1 pp, +77.8 pp) | 4 / 0 | 0.125 | $-0.012 ($-0.019, $-0.004) |
| baseline vs baseline_bare | metadata documentation, no knowledge base | 9 | +11.1 pp (-22.2 pp, +44.4 pp) | 2 / 1 | 1.000 | $+0.007 ($-0.003, $+0.016) |

## By category

| Category | main | baseline | baseline_bare |
|---|---|---|---|
| multi_step | 2/2 (100%) | 2/2 (100%) | 1/2 (50%) |
| trap | 7/7 (100%) | 3/7 (43%) | 3/7 (43%) |

## By split

| Split | main | baseline | baseline_bare |
|---|---|---|---|
| dev | 9/9 (100%) | 5/9 (56%) | 4/9 (44%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | baseline | baseline_bare |
|---|---|---|---|---|
| c101 | trap | 1/1 ($0.06) | 0/1 ($0.05) — missing on_time_pgi_rate{'vstel': 'C300'}=0.9507, on_time_pgi_rate{'vstel': 'U100'}=0.9635 | 1/1 ($0.04) |
| c103 | trap | 1/1 ($0.05) | 0/1 ($0.06) — missing materials_below_14_days=7 | 0/1 ($0.05) — missing materials_below_14_days=7 |
| c109 | trap | 1/1 ($0.07) | 1/1 ($0.10) | 1/1 ($0.08) |
| c110 | trap | 1/1 ($0.07) | 0/1 ($0.08) — missing median_minutes{'shift': 'A'}=18.466667, median_minutes{'shift': 'B'}=20.0 | 0/1 ($0.10) — behavior=clarify; missing median_minutes{'shift': 'A'}=18.466667, median_minutes{'shift': 'B'}=20.0 |
| c112 | trap | 1/1 ($0.03) | 1/1 ($0.04) | 0/1 ($0.02) — missing on_time_pgi_rate{'vstel': 'C300'}=0.9419 |
| c114 | trap | 1/1 ($0.04) | 1/1 ($0.06) | 1/1 ($0.04) |
| c116 | trap | 1/1 ($0.05) | 0/1 ($0.06) — missing median_days_of_supply=83.99996640001343 | 0/1 ($0.08) — missing median_days_of_supply=83.99996640001343 |
| c105 | multi_step | 1/1 ($0.06) | 1/1 ($0.07) | 0/1 ($0.06) — missing open_lines{'name1': 'Hoshino RF Devices'}=29, overdue_lines{'name1': 'Hoshino RF Devices'}=0 |
| c107 | multi_step | 1/1 ($0.04) | 1/1 ($0.05) | 1/1 ($0.04) |

## Notes (2026-09-28)

- Batch: the nine new dev-split cases of `bank_v2` (c101-c116), which depend on rules stated only in the
  knowledge base, and multi-step questions. Main model Sonnet 5.5 (effort medium), three arms. Spend $1.55.
- **`main` 9/9, `baseline` 5/9, `baseline_bare` 4/9.** On these cases the knowledge base changes the answer far
  more often than on bank v1 (+44 pp vs `baseline`); nine cases are too few for significance (4/0, p = 0.125).
- `baseline` failures: the current fiscal quarter taken as 2026-08-24 onward and only shipped deliveries
  counted (c101); plant-level stock and a 90-day demand window for days of supply (c103, c116); no knowledge of
  the site's shift times (c110). `baseline_bare` also used calendar weeks for "last week" (c112) and identified
  the supplier only by number with a wrong fiscal-year start (c105).
- Scorer and case corrections from this run: for single-row references, the key (for example the supplier
  name) may be named once in the answer text instead of in every value label (c105 was rejected for all arms
  although the numbers were right); c109 now uses a 0.2 % tolerance, because customer demand from delivery
  quantities differs from goods-issue postings by 0.06 % while the case targets the transfer rules. Before these
  corrections: `main` 8/9, `baseline` 3/9, `baseline_bare` 3/9. The bank v1 batches score unchanged.
