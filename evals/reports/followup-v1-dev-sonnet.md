# Evaluation report: followup_v1 (followup-v1-dev-sonnet)

As-of date 2026-09-21; data snapshot {'profile': 'small', 'generator_version': '0.8.0', 'seed': 20260924}. Deterministic scoring (behavior routing, value matching with tolerances, operator-identity leak check). Pass rates are over runs; repetitions of a case are paired across arms.

| Arm | Model | Runs | Passed | Answering runs correct | Behavior correct | Consistent cases | Traced values | Total cost | Mean cost | Cache read | Mean turns | Mean SQL | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| main | claude-sonnet-5-5 | 13 | 13/13 (100%) | 11/11 (100%) | 13/13 (100%) | - | 43/43 (100%) | $1.09 | $0.084 | 91% | 7.8 | 5.3 | 46s |

## Context per question

Prompt tokens of the first response to each question of a follow-up thread (decision record 006).

| Arm | Question | Threads | Mean prompt tokens | Max prompt tokens |
|---|---|---|---|---|
| main | 1 | 13 | 8,825 | 8,837 |
| main | 2 | 13 | 16,185 | 22,879 |
| main | 3 | 10 | 17,794 | 21,267 |

## By category

| Category | main |
|---|---|
| followup | 13/13 (100%) |

## By split

| Split | main |
|---|---|
| dev | 13/13 (100%) |

## Cases

Passed runs / runs, mean cost, and the first failure reason.

| Case | Category | main |
|---|---|---|
| f01 | followup | 1/1 ($0.11) |
| f02 | followup | 1/1 ($0.09) |
| f03 | followup | 1/1 ($0.10) |
| f04 | followup | 1/1 ($0.09) |
| f05 | followup | 1/1 ($0.07) |
| f06 | followup | 1/1 ($0.12) |
| f07 | followup | 1/1 ($0.07) |
| f08 | followup | 1/1 ($0.10) |
| f09 | followup | 1/1 ($0.06) |
| f10 | followup | 1/1 ($0.06) |
| f11 | followup | 1/1 ($0.07) |
| f12 | followup | 1/1 ($0.06) |
| f13 | followup | 1/1 ($0.08) |

## Notes (2026-10-23)

- First run of `followup_v1` (decision record 006): 13 threads, 36 questions, one session per thread. Every earlier question's behavior was right, including the clarification in f07, the refused delete in f09, and the refusals that close f03 (site isolation) and f08 (individual ranking).
- Re-scored once after one evaluation fix found by reading the failed run: f04 reported the change of each site's rate as `+0.16` with unit "percentage points", which the scorer read as a fraction. Units meaning percent (`pp`, "percentage points", "points") now convert like `%`. Before the fix: 12/13.
- Context: the first question starts at about 8,800 prompt tokens (the frozen prompt and tools); a follow-up starts at 15,000 to 23,000, and no response in the batch exceeded 26,000 prompt tokens. With 91 % of prompt tokens read from the cache, a follow-up question costs less than the first (f01: $0.061, $0.020, $0.031). Compaction is not needed at this thread length.
