# Evaluation report: bank_v1 (bank-v1-dev-1)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.2.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-opus-5-5 | 46 | 46/46 (100%) | 38/38 (100%) | 46/46 (100%) | - | $4.31 | $0.094 | 5.0 | 2.3 | 32s |
| baseline | claude-opus-5-5 | 46 | 42/46 (91%) | 34/38 (89%) | 46/46 (100%) | - | $4.82 | $0.105 | 5.5 | 3.5 | 35s |
| baseline_bare | claude-opus-5-5 | 46 | 42/46 (91%) | 34/38 (89%) | 46/46 (100%) | - | $4.16 | $0.090 | 6.6 | 5.5 | 41s |

## Paired comparisons

Pass-rate and cost differences (A minus B) with 95 % case-level bootstrap intervals; McNemar exact test on per-case majority outcomes (cases only A passes / only B passes).

| A vs B | Measures | Cases | Pass rate diff (95 % CI) | Only A / only B | McNemar p | Cost diff per question (95 % CI) |
|---|---|---|---|---|---|---|
| main vs baseline_bare | full stack vs naive (knowledge base, prompt, and metadata documentation) | 46 | +8.7 pp (+2.2 pp, +17.4 pp) | 4 / 0 | 0.125 | $+0.003 ($-0.008, $+0.013) |
| main vs baseline | knowledge base + governed prompt, documented metadata | 46 | +8.7 pp (+2.2 pp, +17.4 pp) | 4 / 0 | 0.125 | $-0.011 ($-0.023, $-0.000) |
| baseline vs baseline_bare | metadata documentation, no knowledge base | 46 | +0.0 pp (+0.0 pp, +0.0 pp) | 0 / 0 | 1.000 | $+0.014 ($+0.006, $+0.022) |

## By category

| Category | main | baseline | baseline_bare |
|---|---|---|---|
| adhoc | 10/10 (100%) | 10/10 (100%) | 10/10 (100%) |
| clarify | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) |
| covered | 12/12 (100%) | 8/12 (67%) | 8/12 (67%) |
| decline | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) |
| restricted | 3/3 (100%) | 3/3 (100%) | 3/3 (100%) |
| trap | 8/8 (100%) | 8/8 (100%) | 8/8 (100%) |
| unfamiliar | 7/7 (100%) | 7/7 (100%) | 7/7 (100%) |

## By split

| Split | main | baseline | baseline_bare |
|---|---|---|---|
| dev | 46/46 (100%) | 42/46 (91%) | 42/46 (91%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main | baseline | baseline_bare |
|---|---|---|---|---|
| c021 | covered | 1/1 ($0.12) | 1/1 ($0.14) | 1/1 ($0.11) |
| c023 | covered | 1/1 ($0.12) | 1/1 ($0.10) | 1/1 ($0.08) |
| c025 | covered | 1/1 ($0.09) | 1/1 ($0.13) | 1/1 ($0.07) |
| c026 | covered | 1/1 ($0.12) | 1/1 ($0.12) | 1/1 ($0.14) |
| c028 | covered | 1/1 ($0.16) | 1/1 ($0.27) | 1/1 ($0.30) |
| c029 | covered | 1/1 ($0.08) | 1/1 ($0.12) | 1/1 ($0.10) |
| c031 | covered | 1/1 ($0.07) | 1/1 ($0.07) | 1/1 ($0.06) |
| c033 | covered | 1/1 ($0.15) | 0/1 ($0.17) — missing median_days_of_supply=84.0000084884222 | 0/1 ($0.13) — missing median_days_of_supply=84.0000084884222 |
| c035 | covered | 1/1 ($0.15) | 0/1 ($0.13) — missing days_of_supply{'vstel': 'C100'}=54.75, days_of_supply{'vstel': 'C300'}=16.71 | 0/1 ($0.10) — missing days_of_supply{'vstel': 'C100'}=54.75, days_of_supply{'vstel': 'C300'}=16.71 |
| c036 | covered | 1/1 ($0.14) | 0/1 ($0.15) — missing on_time_rate{'bukrs': '1000'}=0.7852, on_time_rate{'bukrs': '2000'}=0.8016 | 0/1 ($0.15) — missing on_time_rate{'bukrs': '1000'}=0.7852, on_time_rate{'bukrs': '2000'}=0.8016 |
| c038 | covered | 1/1 ($0.19) | 0/1 ($0.19) — missing on_time_rate=0.8222 | 0/1 ($0.16) — missing on_time_rate=0.8222 |
| c039 | covered | 1/1 ($0.08) | 1/1 ($0.11) | 1/1 ($0.09) |
| c041 | adhoc | 1/1 ($0.07) | 1/1 ($0.08) | 1/1 ($0.07) |
| c043 | adhoc | 1/1 ($0.09) | 1/1 ($0.10) | 1/1 ($0.10) |
| c045 | adhoc | 1/1 ($0.07) | 1/1 ($0.09) | 1/1 ($0.05) |
| c046 | adhoc | 1/1 ($0.12) | 1/1 ($0.12) | 1/1 ($0.10) |
| c048 | adhoc | 1/1 ($0.05) | 1/1 ($0.08) | 1/1 ($0.04) |
| c050 | adhoc | 1/1 ($0.11) | 1/1 ($0.13) | 1/1 ($0.11) |
| c051 | adhoc | 1/1 ($0.14) | 1/1 ($0.13) | 1/1 ($0.11) |
| c053 | adhoc | 1/1 ($0.12) | 1/1 ($0.10) | 1/1 ($0.08) |
| c055 | adhoc | 1/1 ($0.11) | 1/1 ($0.08) | 1/1 ($0.06) |
| c056 | adhoc | 1/1 ($0.15) | 1/1 ($0.17) | 1/1 ($0.12) |
| c057 | unfamiliar | 1/1 ($0.07) | 1/1 ($0.05) | 1/1 ($0.04) |
| c059 | unfamiliar | 1/1 ($0.10) | 1/1 ($0.06) | 1/1 ($0.09) |
| c060 | unfamiliar | 1/1 ($0.03) | 1/1 ($0.02) | 1/1 ($0.02) |
| c062 | unfamiliar | 1/1 ($0.06) | 1/1 ($0.04) | 1/1 ($0.03) |
| c064 | unfamiliar | 1/1 ($0.13) | 1/1 ($0.10) | 1/1 ($0.10) |
| c066 | unfamiliar | 1/1 ($0.06) | 1/1 ($0.04) | 1/1 ($0.04) |
| c067 | unfamiliar | 1/1 ($0.06) | 1/1 ($0.04) | 1/1 ($0.07) |
| c069 | trap | 1/1 ($0.12) | 1/1 ($0.14) | 1/1 ($0.11) |
| c071 | trap | 1/1 ($0.06) | 1/1 ($0.11) | 1/1 ($0.13) |
| c073 | trap | 1/1 ($0.16) | 1/1 ($0.14) | 1/1 ($0.15) |
| c075 | trap | 1/1 ($0.12) | 1/1 ($0.09) | 1/1 ($0.11) |
| c077 | trap | 1/1 ($0.09) | 1/1 ($0.08) | 1/1 ($0.06) |
| c079 | trap | 1/1 ($0.10) | 1/1 ($0.15) | 1/1 ($0.10) |
| c081 | trap | 1/1 ($0.10) | 1/1 ($0.16) | 1/1 ($0.10) |
| c083 | trap | 1/1 ($0.14) | 1/1 ($0.14) | 1/1 ($0.21) |
| c085 | clarify | 1/1 ($0.02) | 1/1 ($0.05) | 1/1 ($0.03) |
| c087 | clarify | 1/1 ($0.03) | 1/1 ($0.19) | 1/1 ($0.10) |
| c089 | clarify | 1/1 ($0.01) | 1/1 ($0.06) | 1/1 ($0.04) |
| c090 | decline | 1/1 ($0.02) | 1/1 ($0.01) | 1/1 ($0.01) |
| c092 | decline | 1/1 ($0.02) | 1/1 ($0.05) | 1/1 ($0.03) |
| c094 | decline | 1/1 ($0.02) | 1/1 ($0.04) | 1/1 ($0.02) |
| c096 | restricted | 1/1 ($0.16) | 1/1 ($0.16) | 1/1 ($0.14) |
| c098 | restricted | 1/1 ($0.10) | 1/1 ($0.02) | 1/1 ($0.03) |
| c100 | restricted | 1/1 ($0.04) | 1/1 ($0.05) | 1/1 ($0.06) |
## Notes (2026-09-28)

- Batch: the 46 new dev-split cases of `bank_v1` (c021–c100), `main` and `baseline`, one repetition each. Spend
  $9.13. The locked test split (34 new cases) was not run.
- **The knowledge base is what separates the arms.** All four `baseline` failures are governed definitions that
  exist only in `kb/`: days of supply (c033, c035: the baseline used a 90-day demand window and all stock types
  instead of 28 days and available stock) and supplier on-time delivery (c036, c038: no one-day grace, periods by
  due date instead of first receipt). The baseline answers were reasonable and stated their assumptions, but
  gave different numbers from the company definition.
- **Traps, ad hoc, and unfamiliar cases did not discriminate** (both arms 100 %). The baseline also sees the
  documented table and column comments, which already explain most traps (shared plant, stock categories,
  movement types). A `baseline_bare` run would show how much of that comes from the metadata documentation.
- `main` answered with fewer SQL statements (2.3 vs 3.5) at slightly lower cost ($0.094 vs $0.105 per question;
  the bootstrap interval just excludes zero).
- **Case corrections after this run** (scored above with the corrected cases): c090 and c091 (write requests)
  now accept `policy_restricted` as well as `decline`, because both refuse for the same reason (read-only);
  c087 accepts `answer_with_assumption` like seed_19, because a stated-assumption answer is reasonable there.
  Before the corrections: `baseline` 40/46, only-main 6 / only-baseline 0, McNemar p = 0.031. After: 42/46,
  4 / 0, p = 0.125. The pass-rate interval excludes zero in both cases (+13.0 pp and +8.7 pp).

## Notes: metadata ablation (2026-09-28)

- Added `baseline_bare` (minimal prompt, no knowledge base, `list_tables` names only, `describe_table` columns
  and types only) on the same 46 cases. Spend $4.16.
- **Same accuracy as `baseline`: 42/46, the same four failures** (c033, c035, c036, c038). Without the knowledge
  base, the table and column documentation adds no correctness on these cases; the traps that seed_09 exposed
  (shared plant) are now solved from the data itself (storage locations, shipping points).
- **The documentation buys efficiency:** 3.5 vs 5.5 SQL statements and 5.5 vs 6.6 turns per question, 35 s vs
  41 s. `baseline_bare` is slightly cheaper per question ($0.090 vs $0.105) because the bare `describe_table`
  output is much shorter.
- Conclusion for the design: on this model, correctness gains come from the governed definitions in the
  knowledge base; metadata documentation mainly reduces exploration. Harder cases that depend on
  knowledge-base-only rules are in `bank_v2` (c101-c116).
