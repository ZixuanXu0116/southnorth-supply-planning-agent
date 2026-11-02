# Change card: the planner's write checked by default, two attempts, then a visible failure

- **Status:** adopted 2026-11-02 (the owner: build both, the figure check on by default; this reverses the 2026-11-01 decision of [`change-20261101-verifier-form`](change-20261101-verifier-form.md), "no runtime check by default", for the figure check only; the verifier sub-agent and the facts check stay off by default)
- **Trigger:** the owner's interview practice (2026-11-02): the numbers in the free-text rationale were not checked, while the data agent's answers are (faithfulness gate); a refused write was limited only by the turn and cost budgets and could end without anything visible to purchasing
- **Agent:** the planner (prompt v6, unchanged); **layers:** harness (`agent/planner.ts`, `agent/verifier.ts` `untracedFigures`, `cli/case.ts`), pages (the planner note card)

## What changes

1. **Figure check on every recommendation.** Before `propose_recommendation` runs, the harness reads the case and the server's ranking and lists the rationale's numbers and dates that no evaluation holds. Any listed figure refuses the write (code `UNTRACED_FIGURES`), with the figures named. `--figure-check off` turns it off.
2. **Derived figures.** A figure is traced when it is an evaluated value (up to rounding and percent, as before), the difference between the same field of two options (a cost gap, a risk gap in percentage points), the days between two of their dates, or, written as rounded ("about 20,400"), one of those to its last non-zero digit. Sums, ratios, and differences across fields are not accepted.
3. **Two attempts, then a visible failure.** The second refused write of a task ends the run (status `refused`). The harness itself records a note on the case: what purchasing should do ("No planner recommendation: decide from the ranked options table."), the refusal codes, and the run id of the trace. The case page shows the planner's last note on its own card; the planner button reports the failure.

## Measurement (2026-11-02)

**Offline, on the 24 recommendations of `models-eval-sonnet-medium`** (Sonnet 5.5, no check; the store and ranking of each run):

| Rule | Runs refused (false alarms) | Suite's figure mutations caught | Every figure shifted by 7 % caught |
|---|---|---|---|
| Before (evaluated values only) | 10/24 | 22/24 | 165/165 |
| After (with derived figures) | 0/24 | 21/24 | 164/165 |

All ten flagged runs passed the auditor; every flagged figure was a correct difference between two options ("cheaper by 438.70", "about 20,400 higher", "2.52 points less risk", "22 days earlier"). With the rule as it was, the default check would have refused 42 % of correct recommendations, and with two attempts some of them would have ended without one. The derived rule lets one planted wrong figure in 189 through, where the wrong value happens to equal a difference.

**Live replay** ([`figure-check-default-sonnet`](../reports/figure-check-default-sonnet.md), USD 1.02 with the audit), the same 12 cases, two runs each, against `models-eval-sonnet-medium`:

| Planner | Written | Rule order | Refused writes | Runs ended refused | Untraced figures in the final text | Auditor fail | Mean cost | Mean latency |
|---|---|---|---|---|---|---|---|---|
| No check (before) | 24/24 | 24/24 | 0 | 0 | 10 | 1/24 | $0.023 | 11 s |
| Figure check, two attempts (now) | 24/24 | 24/24 | 1 | 0 | 0 | 2/24 | $0.028 | 10 s |

The one refusal was right: "a 2.6-point risk gap" for 15.77 % against 13.10 % (2.67); the planner dropped the figure and the second attempt was written. Both auditor fails are the same case (`C-US10-40000548`): 51,578.30 called the gap in expected total cost, where it is the gap in total cost (the expected gap is 51,559.80). The figure check traces a number, not the field it is said to belong to, so it passes; the run without the check wrote the same figure and its auditor passed it then. The decision-critical facts stay in the facts line the case server writes.

## Tests

`harness/test/planner-loop.test.ts` runs the loop with a scripted model and a stand-in case server: a recommendation whose figures come from the evaluations is written; an untraced figure is refused, a second refusal (the server's `UNRANKED`) ends the run with the note on the case and no third model call; the check can be turned off. `harness/test/verifier.test.ts` keeps catching an invented figure; `apps/web/tests/test_web.py` shows the note card.
