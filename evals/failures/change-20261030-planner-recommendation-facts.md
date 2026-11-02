# Change card: what the planner's recommendation must state

- **Status:** adopted 2026-11-01 (both layers): the facts line is on the case page and `planner/v6` is the default
- **Trigger:** the review of [`verifier-mutations-v1`](../reports/verifier-mutations-v1.md) (2026-10-30): the verifier failed 6 of 12 real recommendations of `planner/v5`, and all six are real defects of the planner's text
- **Batch, arm:** `verifier-mutations-v1`, planner prompt `v5`, verifier off while the planner wrote
- **Model and effort:** the configured main model (`SNM_MODEL_MAIN`), effort medium
- **Trace:** `runs/evals/verifier-mutations-v1/planner-runs/` (runs are not committed)
- **Suite:** planner-evaluate cases and supplier mixes

## Expected

Purchasing reads the recommendation card and learns, without opening the options table, whether the recommended option meets its service target and which mandate fields it misses; every comparison in the text is true figure by figure; every option is named with its own evaluation.

## Observed

| Defect | Cases | Example |
|---|---|---|
| The recommended option misses its service target and the text does not say so | 4 of 12 (C-US10-40000005, -40000014, -40000048, -40000055) | "P-1 ... about halves the stock-out risk (16.44%)" for a class A part whose target allows 1% |
| A comparison overstated | 2 of 12 (-40000014, -40000047) | "the engine's new order is better on every figure" although its holding cost is higher and its risk equal |
| One option's evaluation cited under the other option's name | 1 of 12 (-40000030) | P-2 described with P-1's evaluation id, arrival, and costs |

## Root cause

- Missed service target: `prompt instruction`, and a gap in what the system states by itself. The v5 instructions list the figures to cite ("... stock-out risk, the mandate fields missed ...") but not the verdict on the target, so the model gives the risk and stops. The verdict exists as a field (`meets_service_target`), but nothing puts it in front of purchasing in words.
- Overclaim and mix-up: `prompt instruction` (comparisons and naming are free text with no rule).

## Fix hypothesis

Two layers, the deterministic one first:

1. **System design: a facts line the case server writes** (`case_server/.../service.py`, `propose_recommendation`; the case page shows it above the planner's text). From the evaluation of the recommended option, never from the model: `Recommended P-1-10: arrives 2026-11-05; stock-out risk 9.89% (target at most 10%: met); mandate: freight and surcharge missed; expected total cost 376.28 USD.` (the role-play case's second offer) A missed target or mandate field can then never be left out, whatever the planner writes. Not a prompt change; a case-event field and a page line.
2. **Prompt: planner `v6` evaluate instructions** (`harness/src/agent/planner.ts`, `TASK_INSTRUCTIONS_V6.evaluate`). v5 unchanged, plus three sentences at the end:

   > Say whether the recommended option meets its service target. Name each option with its own id and evaluation_id. Compare options figure by figure: never call one better on every figure when any of its figures is worse.

   `prompts/planner/v6.md` is `v5.md` unchanged; the tool descriptions are unchanged.

The verifier (off by default, `triggered` in production) stays as the runtime net for what wording cannot prevent.

Alternatives considered: the prompt sentence alone (cheaper to build, but four omissions in twelve say the model follows a listed figure more reliably than an implied verdict); the verifier alone (catches it at about USD 0.03 per check and one more planner turn, every time, instead of preventing it).

## Affected categories

The evaluate task in every mix; the prepare and handoff tasks are unchanged.

## Owner review

- Hypothesis review (2026-11-01): both layers, as complements: the facts line guarantees that the facts are shown; the three sentences make the planner's own text complete and true.
- Diff review (2026-11-01): the facts line format and the three sentences approved as shown.

Built (2026-11-01):

- Layer 1: `CaseService.facts_line` (`case_server/src/snm_cases/service.py`), stored as `facts_line` in the `recommendation_proposed` event and shown in bold above the planner's text on the case page (recomputed for a recommendation written before it). Example, case C-US10-40000005 of `verifier-mutations-v1`: `Recommended P-2-7: arrives 2026-12-09; stock-out risk 11.04% (target at most 3%: missed); mandate: inside; expected total cost 1,147.97 USD.` Without a priced risk it says `stock-out risk not priced`; for an engine option `not a supplier offer`; for a C part's late offer `arrival missed (priced by risk)`; costs only for a role that sees costs. Tests in `case_server/tests/test_case_service.py` and `apps/web/tests/test_web.py`.
- Layer 2: `planner/v6` = v5 plus the three sentences (`V6_EVALUATE_SENTENCES` in `harness/src/agent/planner.ts`; `prompts/planner/v6.md` is v5 unchanged). v5 stays the default until the measurement.

## Measurement (proposed)

The same 12 cases and supplier answers: `planner/v6` (with the facts line) against `v5`, three repetitions. Checks: the verifier's verdict on every recommendation (target omissions, overclaims, and mix-ups should fall to zero), the suite's rule-order check (no regression from 36/36), and the prepare suite (no regression from 9/9). About USD 4 (planner runs and verifier checks).

## Decision

Measured 2026-11-01 ([`planner-evaluate-v6-v5`](../reports/planner-evaluate-v6-v5.md), 12 cases, three runs each, after every evaluation gained its expected total cost; USD 4.50): v6 left no missed target unsaid (v5: 3 of 36) and mixed up no labels (v5: 1), rule order 36/36 in both, cost $0.045 against $0.048 per case; overstated comparisons 3 against 1, two of them the "every figure" claim the third sentence forbids. Real defects in 5 runs of 36 for v5, 3 for v6, read by hand. Adopted by the owner on 2026-11-01: `planner/v6` is the default (`PLANNER_PROMPT_VERSION`); v5 stays runnable for paired measurements. The overstated comparisons stay as they are for now (no v7); the verifier remains the runtime net for them. The audit packet now carries the engine's lever detail (the planned lead time), the cause of two false alarms. The supplier's own words (messages and a proposal's free-text conditions) were briefly added and then removed at the owner's direction: the verifier receives data and figures only, and supplier text reaches purchasing through the planner; a claim about a message is left `uncertain`, which does not block the write.
