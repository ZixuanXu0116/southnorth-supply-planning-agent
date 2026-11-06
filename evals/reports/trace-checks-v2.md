# Trace checks v2: code checks, rubric v2, and the judge tested

Date 2026-11-05. The rules of a sound planner run are checked in code on every trace (`evals/src/snm_evals/trace_checks.py`, checks 1–7 and 9); the LLM judge grades only what needs judgment (`prompts/trace-judge/v2.md`: the real reason, omissions, comparisons, an actionable note). Both are measured on planted defects; the judge's consistency is measured on sound runs judged twice. LLM spend for this report: USD 1.04.

```
uv run snm-evals trace-checks --batch figure-check-default-sonnet --batch models-prepare-sonnet-5-5-medium --planted
uv run snm-evals trace-judge --rubric v2 --planted --batch trace-judge-v2-2026-11-05
```

## 1. Code checks on sound runs (no false failure)

### `figure-check-default-sonnet`

24 traces; 24 pass every check that applies.

| Check | Pass | Fail | Does not apply |
|---|---|---|---|
| 1. read before writing | 24 | 0 | 0 |
| 2. rank before recommending | 24 | 0 | 0 |
| 3. cited ids traced | 24 | 0 | 0 |
| 4. one write, last and alone | 24 | 0 | 0 |
| 5. no identical retry | 1 | 0 | 23 |
| 6. prepare write fits the case | 0 | 0 | 24 |
| 7. every figure traced | 24 | 0 | 0 |
| 9. ended within budget and without error | 24 | 0 | 0 |

No failure.


### `models-prepare-sonnet-5-5-medium`

9 traces; 9 pass every check that applies.

| Check | Pass | Fail | Does not apply |
|---|---|---|---|
| 1. read before writing | 9 | 0 | 0 |
| 2. rank before recommending | 0 | 0 | 9 |
| 3. cited ids traced | 0 | 0 | 9 |
| 4. one write, last and alone | 9 | 0 | 0 |
| 5. no identical retry | 0 | 0 | 9 |
| 6. prepare write fits the case | 9 | 0 | 0 |
| 7. every figure traced | 0 | 0 | 9 |
| 9. ended within budget and without error | 9 | 0 | 0 |

No failure.


Every other workflow batch passes too: `models-eval-opus-medium` 24/24, `models-eval-sonnet-high` 24/24, `models-eval-sonnet-medium` 24/24, `models-prepare-opus-5-5-medium` 9/9, `models-prepare-sonnet-5-5-high` 9/9, `planner-evaluate-v6-v5` 72/72, `verifier-form-v2` 72/72.

Reported apart, an older arm: `verifier-form-v1`, arm `self_check`, fails check 5 in 5 of its 12 runs. That arm refused the first recommendation with "Call check_recommendation on this exact draft before proposing it"; the planner checked the draft and sent it again unchanged, as the arm asked. The check is right that the call was repeated, and the arm (retired on 2026-11-01) is why. Check 7 does not apply to runs before 2026-11-02, which have no recorded figure check.

## 2. Planted structural defects

Each copy of a sound trace carries one defect: 12 evaluate traces of `figure-check-default-sonnet` for the first four kinds, the 9 prepare traces of `models-prepare-sonnet-5-5-medium` for the last.

| Defect | Copies | Caught by its check | Checks that failed |
|---|---|---|---|
| `rank_options` removed | 12 | 12 (check 2) | check 2: 12, check 3: 12 |
| the case read moved after the write | 12 | 12 (check 1) | check 1: 12, check 4: 12 |
| a refused write sent again unchanged | 12 | 12 (check 5) | check 5: 12 |
| the first option cites the second option's id | 12 | 12 (check 3) | check 3: 12 |
| the other write type (prepare) | 9 | 9 (check 6) | check 6: 9 |

The code checks catch every planted structural defect: 57 of 57. They catch none of the 12 false comparisons, which are in the text only (by design: that is the judge's part).

## 3. The LLM judge, rubric v2

### False comparisons

Each of the 12 sound evaluate traces gets one sentence that its own evaluations make false, added to the first paragraph of its rationale.

- First planting: 11 of 12 caught. The one miss was the planting's fault: "P-2 costs less than P-1" is true of the option cost (-867.66 against 3,708.89 USD) and false only of the expected total (25,799.84 against 13,671.39), and the judge said so ("the cheaper-on-direct-cost P-2"). The planted sentences now name the figure they compare.
- Second planting: **12 of 12 caught**, each on the comparisons question (12 of 12). No miss to list.

| Trace | Planted sentence | Verdict |
|---|---|---|
| `20261102T192334-eedb16` | P-2 is better than P-1 on every figure. | unsound |
| `20261102T192342-807ff5` | P-3 is better than P-2 on every figure. | unsound |
| `20261102T192345-14b522` | P-2 is better than P-1 on every figure. | unsound |
| `20261102T192355-f7e4b0` | P-2 is better than P-1 on every figure. | unsound |
| `20261102T192403-2dccfc` | P-3 is better than P-2 on every figure. | unsound |
| `20261102T192404-dc6065` | P-1 has the lowest expected total cost of the options. | unsound |
| `20261102T192415-e5e30f` | P-2 is better than P-1 on every figure. | unsound |
| `20261102T192424-2c97b0` | P-3 is better than P-2 on every figure. | unsound |
| `20261102T192424-3987d3` | P-2 has a lower expected total cost than P-1. | unsound |
| `20261102T192334-ee0ebf` | P-2 is better than P-1 on every figure. | unsound |
| `20261102T192343-2ee534` | P-3 is better than P-2 on every figure. | unsound |
| `20261102T192345-a98723` | P-2 is better than P-1 on every figure. | unsound |

### Consistency and false alarms

- The 12 sound sources, each judged twice: **12 of 12 with the same verdict** both times.
- False alarms: 0 of 24 judgments of sound runs were `unsound`.

### Structure is not the judge's part

On the labeling page's planted structural copies, rubric v2 judged the wrong evaluation id and the wrong write type `sound`, and the run without a ranking `unclear` (it had no figures to compare). That is the split by design: the code checks catch all three.

## 4. The owner's labels

A page with 20 runs (8 sound evaluate, 4 with a false comparison, 1 refused once and corrected, 4 prepare, 3 with a structural defect), every step in plain words. The owner checks the flow of a few runs; whether figures and recommendations are right is the checks' part (decided 2026-11-05). Six runs labeled:

| Run | What it is | Owner (flow) | Code checks | Judge (v2) |
|---|---|---|---|---|
| t01 | planted: the first option cites the second option's id | not sound ("get_case first, then rank_options") | fail, check 3 | sound (structure is not its part) |
| t02 | planted: a false comparison | not sound (same reason) | pass | unsound, comparisons |
| t03 | real prepare | sound | pass | sound |
| t04 | real evaluate, refused once by the figure check and corrected | sound | pass | sound |
| t05 | planted: a false comparison | sound | pass | unsound, comparisons |
| t06 | real evaluate | sound | pass | sound |

The verdicts agree on five of six; t05 is the false comparison the owner passed on flow and the judge caught in the text. The owner's reason for t01 and t02 holds for every evaluate run: 287 of 288 send `rank_options` and `get_case` in one turn, in parallel, and the case server ranks from the stored case itself, so the order the trace lists them in is not an order of use. The owner kept it so (2026-11-05); the pages now show such calls as simultaneous, and each run's `case_state_after` (a run ends one task; the case goes on).

## 5. Limits

- The harness keeps the first 4,000 characters of each tool result in the trace. Every evaluate trace's `get_case` result is cut (288 of 288) and 24 of 288 `rank_options` results are cut, so check 7 reads the figure check the harness made on the full results (`figure_check`, recorded since 2026-11-02) instead of recomputing it, and check 3 fails an id only when the visible results contradict it.
- The planted false comparisons are of three forms ("has the lowest expected total cost", "has a lower expected total cost than", "better on every figure"); subtler errors, such as a wrong margin or a missed field stated as met, are not measured here.
