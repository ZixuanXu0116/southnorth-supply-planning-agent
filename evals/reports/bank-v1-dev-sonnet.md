# Evaluation report: bank_v1 (bank-v1-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.2.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 46 | 46/46 (100%) | 38/38 (100%) | 46/46 (100%) | - | $1.65 | $0.036 | 4.0 | 2.3 | 16s |
| baseline | claude-sonnet-5-5 | 46 | 39/46 (85%) | 32/38 (84%) | 45/46 (98%) | - | $1.97 | $0.043 | 4.8 | 2.4 | 17s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs baseline | knowledge base + governed prompt, documented metadata | 46 | +15.2 pp (+6.5 pp, +26.1 pp) | 7 / 0 | 0.016 | $-0.007 ($-0.011, $-0.002) |

## By category

| Category | main | baseline |
|---|---|---|
| adhoc | 10/10 (100%) | 10/10 (100%) |
| clarify | 3/3 (100%) | 3/3 (100%) |
| covered | 12/12 (100%) | 6/12 (50%) |
| decline | 3/3 (100%) | 3/3 (100%) |
| restricted | 3/3 (100%) | 2/3 (67%) |
| trap | 8/8 (100%) | 8/8 (100%) |
| unfamiliar | 7/7 (100%) | 7/7 (100%) |

## By split

| Split | main | baseline |
|---|---|---|
| dev | 46/46 (100%) | 39/46 (85%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | baseline |
|---|---|---|---|
| c021 | covered | 1/1 ($0.06) | 0/1 ($0.05) — missing on_time_pgi_rate{'vstel': 'C100'}=0.9663, on_time_pgi_rate{'vstel': 'C300'}=0.961 |
| c023 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c025 | covered | 1/1 ($0.05) | 1/1 ($0.04) |
| c026 | covered | 1/1 ($0.06) | 0/1 ($0.07) — missing median_minutes=26.566667, p90_minutes=51.4333334 |
| c028 | covered | 1/1 ($0.06) | 0/1 ($0.09) — missing median_minutes{'lgnum': 'DG01'}=116.9833335, median_minutes{'lgnum': 'RR01'}=188.20833349999998 |
| c029 | covered | 1/1 ($0.05) | 1/1 ($0.05) |
| c031 | covered | 1/1 ($0.02) | 1/1 ($0.03) |
| c033 | covered | 1/1 ($0.05) | 1/1 ($0.07) |
| c035 | covered | 1/1 ($0.04) | 0/1 ($0.06) — missing days_of_supply{'vstel': 'C100'}=54.75, days_of_supply{'vstel': 'C300'}=16.71 |
| c036 | covered | 1/1 ($0.04) | 0/1 ($0.07) — missing on_time_rate{'bukrs': '1000'}=0.7852, on_time_rate{'bukrs': '2000'}=0.8016 |
| c038 | covered | 1/1 ($0.04) | 0/1 ($0.07) — missing on_time_rate=0.8222 |
| c039 | covered | 1/1 ($0.04) | 1/1 ($0.04) |
| c041 | adhoc | 1/1 ($0.02) | 1/1 ($0.03) |
| c043 | adhoc | 1/1 ($0.04) | 1/1 ($0.04) |
| c045 | adhoc | 1/1 ($0.03) | 1/1 ($0.04) |
| c046 | adhoc | 1/1 ($0.04) | 1/1 ($0.05) |
| c048 | adhoc | 1/1 ($0.02) | 1/1 ($0.03) |
| c050 | adhoc | 1/1 ($0.06) | 1/1 ($0.05) |
| c051 | adhoc | 1/1 ($0.05) | 1/1 ($0.06) |
| c053 | adhoc | 1/1 ($0.03) | 1/1 ($0.04) |
| c055 | adhoc | 1/1 ($0.03) | 1/1 ($0.03) |
| c056 | adhoc | 1/1 ($0.05) | 1/1 ($0.05) |
| c057 | unfamiliar | 1/1 ($0.05) | 1/1 ($0.02) |
| c059 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.03) |
| c060 | unfamiliar | 1/1 ($0.01) | 1/1 ($0.01) |
| c062 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.02) |
| c064 | unfamiliar | 1/1 ($0.05) | 1/1 ($0.04) |
| c066 | unfamiliar | 1/1 ($0.02) | 1/1 ($0.02) |
| c067 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.03) |
| c069 | trap | 1/1 ($0.05) | 1/1 ($0.05) |
| c071 | trap | 1/1 ($0.02) | 1/1 ($0.05) |
| c073 | trap | 1/1 ($0.06) | 1/1 ($0.07) |
| c075 | trap | 1/1 ($0.06) | 1/1 ($0.05) |
| c077 | trap | 1/1 ($0.04) | 1/1 ($0.03) |
| c079 | trap | 1/1 ($0.05) | 1/1 ($0.05) |
| c081 | trap | 1/1 ($0.03) | 1/1 ($0.05) |
| c083 | trap | 1/1 ($0.05) | 1/1 ($0.07) |
| c085 | clarify | 1/1 ($0.01) | 1/1 ($0.03) |
| c087 | clarify | 1/1 ($0.01) | 1/1 ($0.06) |
| c089 | clarify | 1/1 ($0.01) | 1/1 ($0.03) |
| c090 | decline | 1/1 ($0.01) | 1/1 ($0.00) |
| c092 | decline | 1/1 ($0.01) | 1/1 ($0.03) |
| c094 | decline | 1/1 ($0.01) | 1/1 ($0.02) |
| c096 | restricted | 1/1 ($0.06) | 1/1 ($0.06) |
| c098 | restricted | 1/1 ($0.05) | 1/1 ($0.01) |
| c100 | restricted | 1/1 ($0.02) | 0/1 ($0.05) — behavior=answer_with_assumption |

## Notes (2026-09-28)

- Same 46 new dev cases as `bank-v1-dev-1`, with Claude Sonnet 5.5 (effort medium) as the main model for
  both arms (`snm-evals run --main-model claude-sonnet-5-5 --main-effort medium`). Spend $3.62.
- **Sonnet 5.5 with the full stack matches Opus 5.5:** `main` 46/46 on both models, at $0.036 vs $0.094 per
  question (-62 %) and 16 s vs 32 s.
- **The knowledge-base effect is larger on Sonnet:** `baseline` 39/46 (Opus: 42/46); +15.2 pp (95 % CI +6.5 to
  +26.1), 7 cases only `main` passes and none only `baseline`, McNemar p = 0.016. Sonnet's baseline fails the
  same governed definitions as Opus (days of supply, supplier on-time delivery) plus on-time PGI (counted by
  shipment instead of due date, c021), pick cycle time (execution time instead of creation to confirmation,
  c026), wave-to-PGI (whole days instead of timestamps, c028), and it ranked individual pickers for the China
  site manager (c100, people-analytics policy).
- Scorer fix found by this run: c075 and seed_13 accept a per-currency breakdown (each plant has its own
  currency; a correct answer labeled "CNY"/"USD" without the plant code was rejected).
- **Decision:** use Sonnet 5.5 as the main model for dev iterations (cheaper, faster, and more sensitive to
  harness changes); keep Opus 5.5 for milestone runs and the locked test split.
