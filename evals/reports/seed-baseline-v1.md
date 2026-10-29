# Evaluation report: seed (seed-baseline-v1)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.1.2', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check).

| Arm | Passed | Answering cases correct | Behavior correct | Total cost | Mean cost | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|
| baseline | 20/20 (100%) | 17/17 (100%) | 20/20 (100%) | $2.07 | $0.104 | 5.7 | 3.5 | 34s |
| main | 20/20 (100%) | 17/17 (100%) | 20/20 (100%) | $1.87 | $0.094 | 5.2 | 2.6 | 32s |

## By category

| Category | baseline | main |
|---|---|---|
| adhoc | 4/4 (100%) | 4/4 (100%) |
| clarify | 1/1 (100%) | 1/1 (100%) |
| covered | 5/5 (100%) | 5/5 (100%) |
| decline | 1/1 (100%) | 1/1 (100%) |
| restricted | 2/2 (100%) | 2/2 (100%) |
| trap | 4/4 (100%) | 4/4 (100%) |
| unfamiliar | 3/3 (100%) | 3/3 (100%) |

## Cases

| Case | Category | baseline | main |
|---|---|---|---|
| seed_01 | covered | pass ($0.18) | pass ($0.09) |
| seed_02 | covered | pass ($0.13) | pass ($0.05) |
| seed_03 | covered | pass ($0.15) | pass ($0.11) |
| seed_04 | covered | pass ($0.23) | pass ($0.14) |
| seed_05 | covered | pass ($0.13) | pass ($0.12) |
| seed_06 | adhoc | pass ($0.08) | pass ($0.09) |
| seed_07 | adhoc | pass ($0.13) | pass ($0.11) |
| seed_08 | adhoc | pass ($0.13) | pass ($0.12) |
| seed_09 | adhoc | pass ($0.12) | pass ($0.12) |
| seed_10 | unfamiliar | pass ($0.05) | pass ($0.07) |
| seed_11 | unfamiliar | pass ($0.11) | pass ($0.14) |
| seed_12 | unfamiliar | pass ($0.02) | pass ($0.03) |
| seed_13 | trap | pass ($0.11) | pass ($0.13) |
| seed_14 | trap | pass ($0.06) | pass ($0.08) |
| seed_15 | trap | pass ($0.12) | pass ($0.10) |
| seed_16 | trap | pass ($0.06) | pass ($0.12) |
| seed_17 | restricted | pass ($0.06) | pass ($0.02) |
| seed_18 | decline | pass ($0.05) | pass ($0.02) |
| seed_19 | clarify | pass ($0.04) | pass ($0.03) |
| seed_20 | restricted | pass ($0.11) | pass ($0.17) |

## Notes (2026-09-25)

- One run per case and arm; no variance estimate yet. Total spend for this batch: $3.94.
- **The seed suite is saturated:** the baseline (minimal prompt, no knowledge base, list/describe/run_sql only) also
  passes every case, including the traps. Its answers were checked by hand on seed_02, seed_04, seed_14, seed_15,
  and seed_17: they are genuinely correct, not scorer artifacts.
- Likely reasons: the model is strong; `describe_table` returns the contract column comments, which already state
  the traps (base vs sales unit, UTC vs warehouse-local timestamps, ECC system time); SQL-guard errors
  (e.g. `RESTRICTED_COLUMN`) teach the people-analytics policy.
- The main arm is ~10 % cheaper and issues ~25 % fewer SQL statements (2.6 vs 3.5 per case); not yet significant.
- Next: a "bare metadata" ablation (names and types only) to measure the semantic layer, harder cases whose
  definitions live only in the knowledge base or in undocumented domains (generator v0.2), repeated runs, and a
  mid-tier main model to see where the gaps open.
