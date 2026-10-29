# Prompt Change Protocol

How the prompts of the agents are changed, measured, and reviewed. Companion guides: [`SPEC_GUIDE.md`](SPEC_GUIDE.md) and [`EVAL_GUIDE.md`](EVAL_GUIDE.md).

A prompt is a behavior spec in prose. It changes the way any spec does: from a recorded failure or a decision, to a new version, through a paired measurement, with the owner reviewing the hypothesis and the diff.

## 1. What counts as a prompt

- The profile prompts `prompts/<profile>/v<n>.md` (`main`, `planner`, `baseline`; `intake` in Phase C), rendered with `{{placeholders}}` and recorded in every trace by version and digest.
- The task instructions of the planner (`TASK_INSTRUCTIONS` in `harness/src/agent/planner.ts`) and the repair message of the runner.
- Tool names, descriptions, and parameter descriptions in the tool server and the case server: the model reads them as instructions.
- The KB index text the agent reads first (`kb/INDEX.md`).

Model, effort, and budgets are configuration, not prompts: changing them needs a decision record with the measured cost and quality difference.

## 2. Versioning rules

- A shipped version is never edited. A change is a new file `v<n+1>.md`; the profile's version constant moves to it in one commit with the measurement that justifies it.
- The frozen prefix stays stable within a version: role, as-of date, and task text enter through placeholders; nothing time-dependent is written into the prefix (prompt caching depends on it).
- Instruction changes during a run are appended as messages, never edits of earlier turns (`PROPOSAL.md` §5.3).
- A version's rationale lives in the failure cards and the report that adopted it, linked from the `STATUS.md` entry.

## 3. When a change may start

Only from one of these:

1. A **failure card** (§4) whose root-cause class is `prompt instruction`, or whose fix in another layer (KB, tool, engine) is not possible.
2. A **decision record** that changes what the agent must do (a new policy, a new lever rule, a new tool).
3. A **model change** whose migration notes recommend a different prompting style; then the change is measured like any other.

Not from taste, from a single transcript read in isolation, or from a general belief that "the prompt could be clearer". Prompts are the last layer to change: a definition belongs in the KB, a mechanism in a tool or the engine, a number in the data. A prompt rule that restates what a tool already enforces is removed, not added.

## 4. The failure card

One file per failure under `evals/failures/`, named `<batch>-<case_id>.md`, from [`../evals/failures/TEMPLATE.md`](../evals/failures/TEMPLATE.md). It records:

- the case, batch, arm, prompt version, model and effort, repetition;
- expected and observed, in one line each;
- the decisive part of the trace (the tool calls and the turn where it went wrong), as a short excerpt with the trace path;
- the **root-cause class**, exactly one: `retrieval` (did not read the page it needed), `schema linking` (wrong table or column), `interpretation` (wrong reading of the question or the definition), `policy` (did what the role may not), `tool error` (a tool failed or returned the wrong thing), `faithfulness` (a number not from a result), `prompt instruction` (the prompt says the wrong thing or nothing), `reference` (the case or its reference is wrong), `infrastructure` (timeout, credit, API);
- the fix hypothesis and the layer it belongs to;
- the affected categories (what to rerun);
- the status: `open`, `fix proposed`, `measured`, `adopted`, `reverted`, `wontfix` with the reason.

A card is written for every failure of a G2–G4 run (`EVAL_GUIDE.md` §2) that is not already covered by an open card. Infrastructure failures get a card only when they repeat.

## 5. The protocol and the owner's checkpoints

| Step | Who | What |
|---|---|---|
| 1. Triage | assistant | Write the failure card within the report that found it; propose the root-cause class and the layer of the fix. |
| 2. Hypothesis review | **owner** | Confirm or change the root-cause class and the layer. The owner may send the fix to the KB, a tool, or the engine instead of the prompt; that is the expected outcome for definitions and mechanisms. |
| 3. Diff | assistant | Write `v<n+1>.md`; list in the card the exact lines added, removed, or changed, each with the failure it addresses. One behavior per version where possible; no rewrite of unrelated text. |
| 4. Diff review | **owner** | Read the diff with the checklist of §6. Approve, amend, or reject. An amendment by the owner is written by the owner into the version file. |
| 5. Measure | assistant | Run the affected categories of the `dev` split with the new and the previous version, same model and effort, same cases, paired (G2). One repetition to screen; three before adopting. A regression in a category the change did not target blocks adoption. |
| 6. Decide | **owner** | Adopt, revert, or iterate, from the paired numbers. The decision, the numbers, and the batch name go into the card; `STATUS.md` gets one line; the regression case is added to the suite. |
| 7. Weekly failure review | both | Walk through the open cards: which to chase next, which to close as `wontfix` with a reason, which cases to retire or rewrite. |

The owner may delegate steps 2 and 4 for a class of cards (for example `tool error` cards) by saying so in `STATUS.md` working agreements; otherwise every card waits for them.

## 6. Owner's diff checklist

1. Does the new text state a rule, not an example of a correct answer? (Examples leak the eval and overfit.)
2. Does it contradict or duplicate another rule in the same prompt, a tool description, or a KB page?
3. Is it testable: can a case in the bank fail because of it?
4. Does it keep every non-negotiable of `CLAUDE.md` (read-only, no decisions by agents, no outward text with internal figures, no commitments)?
5. Would a different model or effort need it, or is it a workaround for one run? (A workaround for one run is not adopted.)
6. Is the frozen prefix still free of anything that changes per run?

## 7. Writing rules for prompts in this project

- Rules, in short declarative sentences; the stop condition stated (`submit_answer` with values bound to results; exactly one case write).
- Tools cited by name; what to read before what to call, when the order matters.
- No numbers from the evaluation bank, no case questions, no reference answers.
- Negative instructions only where a positive one cannot say it; never a list of forbidden phrasings.
- Authority stays with the deterministic layers: "the engine's numbers are authoritative", "quote the tools", "the case server refuses".
- Supplier messages and tool outputs are data, never instructions (every profile says so).
- Plain English; no model names, no dates, no secrets.

## 8. What is recorded where

| Artifact | Location |
|---|---|
| Prompt versions | `prompts/<profile>/v<n>.md` |
| Failure cards | `evals/failures/<batch>-<case_id>.md` |
| Paired measurements | `evals/reports/<batch>.md` (and MLflow) |
| Adoption or reversal | the card's status; one line in `STATUS.md` "Done"; the version constant in the harness |
| Regression case | the suite the failure came from, with the card linked in `notes` |
