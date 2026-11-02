# Change card: which form the planner's verifier should take

- **Status:** decided 2026-11-01: no runtime check by default (the verifier stays built, off); measured as `verifier-form-v1` and `verifier-form-v2`
- **Trigger:** the owner's question (2026-10-31) whether the verifier must be a sub-agent, and guidance from Anthropic's Applied AI team that a single agent loading skills and tools when it needs them often matches or beats a main agent with specialist sub-agents at lower cost and latency; sub-agents still help when one agent would hold too much information or when tasks run in parallel. The guidance's test: fold one sub-agent into the main agent, compare on a small set of cases, and keep the sub-agent if results get worse.
- **Agent:** the planner, evaluate task (`planner/v5`)
- **Suite:** planner-evaluate cases and supplier mixes, plus the 48 mutations of `verifier-mutations-v1`

## Expected

Every recommendation that reaches purchasing states true figures, names each option with its own evaluation, and does not leave out a missed service target or mandate field, at the lowest cost and latency that achieves it.

## Observed

Without any check, 6 of 12 `planner/v5` recommendations had a real defect (`verifier-mutations-v1`). The independent verifier sub-agent caught all six and all 48 planted defects at about USD 0.026 per check. Whether a cheaper form catches as much has not been measured.

## Root cause

`design choice not yet measured`: the sub-agent form was chosen for independence (it never sees the planner's reasoning), not on evidence against the alternatives.

## Fix hypothesis

Four forms, each an arm of the planner run (`--verify-form`):

| Arm | Form | What runs before `propose_recommendation` is written |
|---|---|---|
| A `none` | no check | nothing (today's default) |
| B `deterministic` | code only | the figure check (numbers and dates found in no evaluation) and a facts check (when the recommended option misses its service target or a mandate field, the rationale must say so: `target` / `mandate` named); a failure is refused with the findings |
| C `self_check` | the verifier folded into the planner: one tool plus a skill | the planner must call `check_recommendation` on its draft before proposing it; the tool returns the deterministic findings, the evaluated facts of the recommended option, and the verifier's four checks as instructions (the skill, loaded only when called); the planner judges and revises itself |
| D `subagent` | today's verifier | the independent sub-agent in its own context returns pass, fail, or uncertain; a fail is refused |

Expected, if the guidance holds here: C catches about as much as D at lower cost and latency, because the planner already holds the evaluations. Expected, if independence matters: C misses claims the planner believes it made correctly (an overclaim it reasoned its way into), and D stays better.

**New prompt surfaces (the diff to review):**

1. **Tool `check_recommendation`** (arm C only). Parameters: the same as `propose_recommendation`. Description:

   > Check a draft recommendation before you propose it: returns the figures and dates of the rationale found in no evaluation, the evaluated facts of the recommended option, and the checks a recommendation must pass. Call it with the exact draft you will propose.

2. **The skill text the tool returns** (arm C), after the deterministic findings (`untraced_figures`) and the recommended option's evaluated facts (arrival, stock-out risk, service target met or missed, mandate fields missed, expected total cost):

   > Check your draft against the evaluations and the case's facts only, not against what you remember of them:
   > 1. Every claim you make about an option is true: arrival or coverage date, days below zero and below safety stock, stock-out risk and whether it meets the service target, the mandate fields missed or "inside the mandate", costs and expected total cost, and every comparison (cheaper, earlier, riskier, the lowest).
   > 2. Every figure listed under untraced_figures is a correct difference, sum, or rounding of evaluated figures; otherwise correct or remove it.
   > 3. If you recommend an option that is not first in purchasing's order, give a reason the figures support.
   > 4. Do not leave out a material fact about the recommended option: a missed mandate field, a missed service target, or an arrival after the trigger date.
   >
   > If a check fails, revise the draft and check the revised draft before you propose it.

3. **Refusals.** Arm B, when the deterministic check finds a problem:

   > The recommendation does not state that <option> misses its service target / the mandate fields <fields>; say so, or correct the figures: <list>.

   Arm C, when `propose_recommendation` is called with a draft that was not checked as is:

   > Call check_recommendation on this exact draft before proposing it.

**Arm B's facts check (code, not a prompt):** when the recommended option misses its service target, a sentence of the rationale must contain "target" together with a negation ("miss", "not meet", "does not reach", "above"; after the first smoke run also "neither", "none", "no", and "exceed", since the planner wrote "Neither offer meets the service target"); when it misses mandate fields, the rationale must name each field. A keyword check: it catches an omission, not a false claim in other words, which only the figure check and arms C and D can catch.

The planner's prompt and its other tools are unchanged in every arm.

## Affected categories

The evaluate task in every mix.

## Owner review

- Hypothesis review (2026-11-01): approved, with the first pass capped at USD 5 (a small overrun is acceptable if reported).
- Diff review (2026-11-01): the three texts of arm C approved as written; arm B's keyword check approved.

## Measurement (proposed)

First pass, one repetition, the 12 cases of `verifier-mutations-v1`'s sampling, same supplier answers for every arm:

- **Defects of the final recommendation**, judged by an auditor that is not any arm's checker: the verifier's checks run on a different model tier, with every failure and every disagreement with arm D's own verdicts read by hand and the contested ones shown to the owner.
- **Cost and latency per case** (planner plus checker), **turns**, and the suite's **rule order** (no regression).
- **Detection on the 48 mutations** for C and D: C runs the planner on a session that starts from the mutated draft as its own and must decide whether to propose it unchanged.

Cap USD 5 for the pass. A second pass (three repetitions) only if the first separates the arms.

**As run in the first pass (2026-11-01), and why it differs from the plan above:**

- Every form runs on every recommendation (`--verify always`), so the arms differ by form only; the triggers that would have fired in production are recorded per run.
- The auditor is the verifier prompt on the development tier, another tier than arm D's verifier and than the planner (both on the strong tier in this environment), so it shares a model with neither checker. Its fails are read by hand; the deterministic findings (a missed target or mandate field left unsaid, untraced figures) are reported next to it and need no model.
- Detection on the 48 mutations: arm D's is known from `verifier-mutations-v1` (48/48, same prompt and packet). Arm C's needs a session that starts from a mutated draft as the planner's own; with extended thinking, a fabricated earlier assistant turn without its thinking block is refused by the API, and a draft handed over as a user message tests a reviewer, not a self-check. It is left to the second pass, if the first separates the arms.
- Found while building the arms: after two refused writes the planner was also blocked from `add_note`, so it could not record the blocker for purchasing as `docs/TECHNICAL.md` §7 says; fixed (`writeBlockReason` in `harness/src/agent/checks.ts`, with a test) before the batch.

Decision rule, from the guidance: adopt the cheapest form whose defect rate is not worse than D's beyond noise; if every folded form is worse, keep the sub-agent.

## Decision

First pass [`verifier-form-v1`](../reports/verifier-form-v1.md) (2026-11-01, USD 4.12): real defects in the final recommendation, after reading every auditor fail by hand, A none 4/12, B deterministic 2/12, C self-check 0/12, D sub-agent 2/12; cost per case $0.060, $0.053, $0.098, $0.072. Any check removed the missed target left unsaid; B, C, and D are not separated at this size; the self-check cost the most; 7 of B's 8 refusals were false alarms on correct derived figures; 5 of the 8 defects come from reference rows without an expected total cost, a gap in the data rather than in the checking. No form is adopted yet: the reference rows, the facts line, and planner v6 come first, then a second pass on that base.

Follow-up (2026-11-01, the owner's approval): every evaluation the case server returns now carries its expected total cost, the reference rows of `rank_options` and `evaluate_option` included (`public_evaluation` in `case_server/src/snm_cases/service.py`), as the `rank_options` description already said of every row.

Second pass [`verifier-form-v2`](../reports/verifier-form-v2.md) (2026-11-01, the owner's option 1, USD 4.98 plus USD 0.58 for [`verifier-mutations-v2-comparisons`](../reports/verifier-mutations-v2-comparisons.md)), on the new base (facts line, expected totals on every row, planner v6, a packet without supplier text), 12 cases and three runs per arm: no check 1 real defect in 36 runs, the sub-agent as triggered in production 1 in 36 (on a run it did not check); the verifier ran on 27 of 36 runs and failed none, at $0.010 (21 %) and 5 seconds more per case; it still failed 22/22 planted comparison defects. By the rule above the cheapest form not worse than the sub-agent is no runtime check: the first pass's defects were removed upstream. Decided by the owner on 2026-11-01: the verifier stays built and off by default (as today); this comparison is run again when a new source of recommendations appears (supplier documents, the intake agent) or a new defect class shows in a batch.
