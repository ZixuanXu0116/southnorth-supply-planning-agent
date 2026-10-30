# 009. Improve the data agent through a semi-automatic loop: failure card, proposed fix, screen, owner adopts

- **Status:** proposed
- **Date proposed:** 2026-10-28
- **Date decided:**
- **Decided by:** the owner
- **Scope:** evaluation process (`PROMPT_CHANGES.md` §5, `EVAL_GUIDE.md` §2 gate G2), harness (a new `improver` profile), configuration (its model and budget); data-agent track only

## Context

The data agent improves by hand today. The one complete example is failure card [`bank-v8-g080-dev-sonnet-c109`](../../evals/failures/bank-v8-g080-dev-sonnet-c109.md) (2026-10-19): a dev-split run of `main` (92 cases, USD 4.35) failed c109; the assistant read the trace, wrote the card, and proposed one rule for `kb/concepts/internal_transfers.md`; the owner reviewed the hypothesis and then the diff; a screening batch of 24 hand-picked cases (the dev `trap` cases plus the two other cases that read the page) was paired with the original batch as its control (22/24 before, 23/24 after, no regression, USD 1.49); c109 was repeated twice more (3/3, USD 0.30); the owner adopted the rule.

Most of that work had a clear specification and was still done by hand: finding the failing runs in `scores.json`, cutting the decisive part out of `trace.jsonl`, choosing the screen cases, checking that the earlier batch could serve as the control (same model, effort, prompt, knowledge base, snapshot), running and pairing the screen, and writing the card's Measurement section. Three steps needed judgment: the root-cause class, the layer of the fix, and the wording of the fix.

Two inputs have no path into this process:

- Flagged answers from the local question page (`apps/ask/`, PR #3): the owner's note and the run id go to `runs/ask/flags.jsonl` on the owner's machine, and nothing turns them into cards. They have no reference answer, so no screen can score them.
- Follow-up threads (`followup_v1`): 13/13 on 2026-10-23, no failures yet.

The backlog is empty today: the dev split of `bank_v8` passes 89/91 outside the discovery task (`evals/reports/bank-v8-g080-dev-sonnet.md`), and every failure of that run has a closed card (c109 adopted; c176 and c184 were reference errors, corrected; c142 moved to a documented cause; c154 is a discovery task). The loop's value therefore depends on new failures: the owner's flagged answers, harder cases, and the runs after data or tool changes.

Fully automatic adoption is out of scope. The screen runs on the same dev cases the failures come from, so a change adopted on the screen alone could fit the dev split without generalizing; that needs a holdout set first.

## Options

1. **Change nothing.** The assistant writes cards and proposals by hand in a thread, as for c109. No build cost; each failure takes a thread session and two owner checkpoints before anything is measured.
2. **Deterministic tooling; the assistant proposes.** `snm-evals improve` commands do the specified steps (collect the failures, draft the card with the trace excerpt, choose the screen cases, run and pair the screen, write the review packet); the assistant in a thread still diagnoses and writes the fix. No new model calls; the loop runs only when a thread session works on it.
3. **Option 2 plus a proposer agent in the harness** (proposed). A new `improver` profile reads one card (the case with its reference, the agent's answer, the scorer's verdict, the trace excerpt) and the knowledge base, and submits a structured proposal: root-cause class, layer, hypothesis, and, for the knowledge base or a prompt, the exact edits. Deterministic guards check the edits before anything is spent on a screen. About USD 0.20–0.50 per proposal on the strong tier, plus the screen. The loop runs from one command, in a cloud session or on the owner's machine.
4. **Option 3 with automatic adoption** when the screen passes. Out of scope until a holdout set exists (above).

### The loop of option 3

1. **Collect** (`snm-evals improve collect --suite <suite> --batch <batch>`): the failing runs of a scored batch, dev split only. Never discovery cases, errored runs (infrastructure; rerun with `--retry-errors`), or cases with a card already `adopted`, `fix proposed`, or `wontfix`. The batch is read from `runs/evals/<batch>/` or, when it is not on this machine, from its MLflow copy (`tracking.py` uploads every run's files). A flagged answer from the question page enters through `snm-evals improve intake <flags.jsonl | thread.json>` as a card with status `needs case`; it joins the loop once its regression case exists, drafted by the assistant and reviewed by the owner as `EVAL_GUIDE.md` §8 requires, in a new bank `bank_v9` that includes `bank_v8`.
2. **Card draft**: [`evals/failures/TEMPLATE.md`](../../evals/failures/TEMPLATE.md) with every mechanical section filled (case, batch, arm, prompt version and digest, model and effort, trace path, expected, observed, the scorer's missing items) and the decisive part of the trace cut out deterministically: per turn, the tool calls with short arguments, the shape of each result, and the first lines of the thinking, up to the submission.
3. **Propose** (`harness/dist/cli/improve.js`, profile `improver`, prompt `prompts/improver/v1.md`). Input: the card draft and the case (question, role, expected behavior, reference answer, reference SQL, notes). Tools: the knowledge-base tools, rooted at the knowledge base the failing run used; one read-only tool that returns the current `main` prompt; `submit_proposal`. No data tools, no SQL, no evaluation schema. Output: the root-cause class, the layer, the hypothesis, and edits as exact old and new text. For a layer it may not edit (case spec, tool, engine, data) it returns the hypothesis only, and the loop stops there for the owner.
4. **Guards** (deterministic, before any screen):
   - Paths: existing pages under `kb/`, except `kb/INDEX.md`, the policy pages (`kb/policies/`, which change only through a decision record), and the pages the replenishment track owns (`kb/metrics/projected_stock.md`, `kb/guides/replenishment_planning.md`); or a new prompt version `prompts/main/v<n+1>.md`, copied from the current version with the proposed lines changed. Nothing else: no tool descriptions, cases, code, or shipped prompt versions.
   - Leakage: the added text names no case id, contains no reference value of any case in any suite (the `test` split included; the answers' value columns, numbers of three or more significant digits, also written as a percentage), no run of eight or more words from any case question, and no reference to the `eval` schema. A number or id already in the knowledge base (a plant, a movement type) is not a leak.
   - Size: one behavior per proposal, at most 12 added lines; the page's front matter still parses, and `last_reviewed` moves to the day.
   - Prompt versions: nothing time- or run-dependent in the frozen prefix (no dates, model names, or run ids).

   A proposal that fails a guard gets one repair (the proposer runs once more with the problems); a second failure stops the card and records why.
5. **Screen** (`snm-evals improve screen <card>`). The candidate knowledge base is a copy of `kb/` with the edits applied (`runs/improve/<card>/kb/`, git-ignored), passed to the agent through `SNM_KB_ROOT`; the harness reads the knowledge-base tools' root, the INDEX in the prompt, and the KB digest in the run manifest from it. Cases: the target case three times, plus once each the dev cases of the target's category, the dev cases whose `required_kb_docs` include an edited page, and the dev cases whose run in the source batch read an edited page. Model and effort are the source batch's. Control: the source batch's runs of the same cases when their KB digest is the base knowledge base's, the prompt template and the role descriptions have not changed since their commit, and the reference answers recomputed on the loaded data equal the ones that scored them (the check made by hand for c109); otherwise a control arm runs on the base knowledge base too and the estimate doubles. The estimate (runs times the source batch's mean cost per run) is printed before launching, and the screen does not launch above USD 4. A prompt proposal screens the whole dev split and launches only after the owner's hypothesis and diff review (question 3).
6. **Packet** (`runs/improve/<card>/packet.md`): the card with the hypothesis, the diff, the guard results, the paired table (the target case's passes out of three, discordant pairs, every regression by case, cost per question and cache-read share before and after), the commits that touched `harness/` or `tool_server/` between the source batch and now, the money spent, and an advisory verdict: `adopt` when the target passes 3/3 and nothing regressed, `iterate` when it passes fewer and nothing regressed, `reject` on any regression.
7. **The owner decides**: adopt, reject, or iterate with a note (one more proposal for the card, with the note and the first screen in its input). `snm-evals improve decide <card> adopt` applies the diff to the working tree, sets the card's status, and copies the card and the screen report to `evals/failures/` and `evals/reports/`; a pull request carries them, as for every change. A rejection records the reason in the card (`wontfix` or `reverted`), which is committed too, so that a later proposal does not repeat it. The loop itself never writes to `kb/` or `prompts/`.

## Owner questions

1. Who proposes the fix: a proposer agent in the harness (option 3, proposed), or the assistant in a thread with the same tooling (option 2)?
2. What may the proposer see? Proposed: the whole case, reference answer, reference SQL, and notes included. It is a development tool like the assistant; the leakage guard and the owner's diff review keep answers out of the knowledge base; without the reference it cannot tell an agent error from a reference error such as c184. The alternative: only the question, the expected behavior, and the names of the missing keys.
3. Review order. Proposed: for knowledge-base proposals, screen first and one review of the whole packet (each screen capped at USD 4, nothing adopted without the owner); for prompt proposals, keep the owner's hypothesis and diff reviews before the whole-dev screen, as `PROMPT_CHANGES.md` §5 says today. The alternative: the same single review after the screen for both.
4. Budgets. Proposed: the proposer at most USD 0.50 and 20 turns per card on the strong tier; the screen at most USD 4 per proposal; one invocation at most USD 5 unless the owner approves more.

Answers (owner, date):

1.
2.
3.
4.

## Decision

Pending the owner's answers.

## Acceptance

- Gate G0: `uv run pytest` and `npm test -w harness` green, with tests for the collection rules, the trace excerpt, every guard (a planted reference value, case id, question span, disallowed path, and oversized edit each fail; a clean rule passes), the case selection, the control-reuse rule, the advisory verdict, the `improver` CLI on the faux provider (a proposal that fails a guard gets one repair), and `SNM_KB_ROOT` reaching the knowledge-base tools, the INDEX in the prompt, and the manifest digest. The planner's tests unchanged.
- Replay of c109 as of its batch's commit (the knowledge base without the 2026-10-19 rule): collect, propose, guard, and screen run from one command; the proposal edits the knowledge base and passes the guards; the screen shows c109 passing at least 2 of 3 with no regression among the screen cases; under USD 3 in all. The owner reads the packet and says whether they would have adopted it.
- Replay of c184 as of its batch's commit (the reference that still named generator 0.7's suppliers): the proposer returns `reference` with no edit, and no screen is spent (about USD 0.30).
- The acceptance runs cost about USD 3 together, below the USD 5 approval threshold; the result goes into `docs/tracks/DATA_AGENT.md`.

## Consequences

- New code: `evals/src/snm_evals/improve/` (collect, excerpt, guards, screen, packet, decide) behind `snm-evals improve`; `harness/src/agent/improver.ts`, `harness/src/cli/improve.ts`, `prompts/improver/v1.md` (reviewed by the owner in the implementing pull request, like any prompt); `SNM_MODEL_IMPROVER` and the improver budgets in `harness/src/config.ts`, falling back to the verifier model; `SNM_KB_ROOT` read once in `harness/src/agent/kb-tools.ts`. Both are shared modules: no exported signature changes, and both test suites run.
- `PROMPT_CHANGES.md` §5 gains the semi-automatic path (screen before a single review for knowledge-base proposals); prompt proposals keep the current checkpoints unless question 3 says otherwise. `EVAL_GUIDE.md` §4 names flagged answers as a case source and `bank_v9` as their bank.
- The planner reads `kb/` too. The path guard keeps the planner-owned pages out; the packet names every edited page, so that an adopted change to a page the planner also reads is announced to the replenishment track.
- What gets harder: the screen reuses the source batch as its control, so a proposal made long after its batch is screened against an older harness. The packet lists the commits in between, and the owner may ask for a control arm.
- Open: a holdout set (a slice of the dev split the loop never screens on, or new cases) before any automatic adoption; scheduling the loop after each dev run; proposals for tool descriptions, which `PROMPT_CHANGES.md` §1 counts as prompts but which live in the tool server.

## Evidence

- [`evals/failures/bank-v8-g080-dev-sonnet-c109.md`](../../evals/failures/bank-v8-g080-dev-sonnet-c109.md) and [`evals/reports/bank-v8-g080-c109kb-dev-sonnet.md`](../../evals/reports/bank-v8-g080-c109kb-dev-sonnet.md): the manual precedent, its steps and costs.
- [`evals/failures/bank-v8-g080-dev-sonnet-c176.md`](../../evals/failures/bank-v8-g080-dev-sonnet-c176.md), [`evals/failures/bank-v8-g080-dev-sonnet-c184.md`](../../evals/failures/bank-v8-g080-dev-sonnet-c184.md): reference errors found from the trace.
- [`evals/reports/bank-v8-g080-dev-sonnet.md`](../../evals/reports/bank-v8-g080-dev-sonnet.md): the dev split at 89/91 outside the discovery task.
- Dry run of the deterministic steps on the c109 failure as of its batch's commit (2026-10-28, no model calls): the batch was fetched from MLflow, the knowledge base at commit `52379fc` reproduces the batch's KB digest `90206d21e442cb2c`, the adopted rule passes the guards, and the case selection gives exactly the 24 cases the manual screen used, with the source batch as the control and an estimate of USD 1.49.
- [`apps/ask/README.md`](../../apps/ask/README.md) (flagged answers); `evals/src/snm_evals/tracking.py` (batches and run files in MLflow); `harness/src/agent/kb-tools.ts`, `runner.ts`, `manifest.ts` (the three places the KB root is read).
