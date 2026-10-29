# Specification Guide

How a change to this project is specified, decided, and accepted. Companion guides: [`EVAL_GUIDE.md`](EVAL_GUIDE.md) (how a change is measured) and [`PROMPT_CHANGES.md`](PROMPT_CHANGES.md) (how prompt changes are made). Decisions live in [`decisions/`](decisions/).

The one rule: **a change to behavior starts as a written spec and ends as a measurement.** No eval or test that can show the change, no change.

## 1. What counts as a spec here

Specs are not one document. They are layered, and every layer below the first is checked by a machine.

| Layer | Where it lives | What it fixes | How it is checked |
|---|---|---|---|
| Intent and policy | [`PROPOSAL.md`](PROPOSAL.md) (numbered sections), [`../CLAUDE.md`](../CLAUDE.md) non-negotiables | Why the system exists, its boundaries, the seven domain policies (§7.2), the cost model defaults (§14), the phases (§12) | Owner review; policy cases in the evaluation bank (decline, restricted) |
| Decisions | [`decisions/NNN-*.md`](decisions/) | One choice among alternatives, with its evidence and its consequences | Owner sign-off line; superseded only by a later record |
| Data | [`DATA_MODEL.md`](DATA_MODEL.md); `data_contracts/<layer>/<table>.yaml` | Tables, columns, types, comments, sensitivity, site keys, role access | Contract tests on generator and engine outputs; `snm-data govern --check` against the deployed catalog |
| Semantics | `semantic/metric_views/*.yaml`; `kb/**/*.md` with front matter | Metric definitions; agent-facing definitions, traps, and guides | Metric-view tests; eval cases name the pages they require (`required_kb_docs`) |
| Engine | `data/src/snm_data/planning/params.py` (defaults of §14), module docstrings, `data/tests/test_planning.py` | Projection arithmetic, lever rule, triage | Hand-computed references, property tests, replay, oracle |
| Agent behavior | `prompts/<profile>/v<n>.md`; tool schemas in `tool_server/` and `case_server/`; the task instructions in `harness/src/agent/planner.ts`; `evals/cases/*.yaml` | What the agent is told, what it can call, what it must do on each case | Evaluation runs (the gates of `EVAL_GUIDE.md` §2) |
| Working state | [`STATUS.md`](STATUS.md) | What is done, what is next, what waits on whom, what we learned | Read at session start, updated at session end |

Rules that hold across layers:

- English only in every artifact (`CLAUDE.md`).
- Synthetic data only; never a real company, material, supplier, or volume.
- Prefer a deterministic implementation wherever the layer gives a clear spec; a model call is for the parts no spec can pin down.
- A spec that a machine can check (contract, test, eval case) beats one only a person can read. When both exist, the machine-checked one wins on conflict, and the prose gets fixed.

## 2. Which layers a change touches

| Change | Touch, in this order |
|---|---|
| New source table or column | contract → `DATA_MODEL.md` → generator → KB page (if agent-facing) → at least one eval case |
| New or changed metric | metric view YAML → KB metric page → covered eval case → reference answers refreshed |
| Engine rule or parameter | decision record → engine test with a hand-computed reference → `snm-data replay` and `snm-data oracle` rerun → contract comments, KB, `PROPOSAL.md` §4 → planner evaluation |
| Domain policy | decision record → `PROPOSAL.md` §7.2 → guard or case-server check → decline/restricted eval cases |
| Prompt or task instruction | `PROMPT_CHANGES.md` protocol (failure card → hypothesis → new version → paired measurement) |
| Tool added or changed | tool schema and server check → tests → KB guide if the agent needs to know when to use it → eval cases that exercise it |
| Model, effort, or budget | configuration only; a decision record with the measured cost and quality difference |
| Scaffolding, performance, bulk operations | an increment note in `STATUS.md`; tests; no decision record unless behavior changes |

## 3. Two sizes of spec

**A decision record (ADR)** is required when a change

- chooses among alternatives that produce different agent behavior, data semantics, or costs;
- changes a domain policy, a role's access, or what an agent may write;
- changes an engine rule or a cost-model default;
- changes the model, effort, or budget of a profile;
- or reverses an earlier decision.

**An increment note** (a dated entry in `STATUS.md` "Done") is enough for plumbing, refactors, performance work, test additions, documentation, and bug fixes whose expected behavior was already specified. An increment note still names the tests or evals that cover it.

When unsure, write the decision record: it is short, and the owner questions in it are the point.

## 4. The decision record

Files are `docs/decisions/NNN-short-title.md`, numbered in order of creation, with the template [`decisions/000-template.md`](decisions/000-template.md). Sections:

1. **Status**: `proposed` → `accepted` (or `rejected`) → `superseded by NNN`. A record is never edited after acceptance except to add the superseded line.
2. **Context**: the measurable symptom or the question, with the numbers that raised it and where they come from.
3. **Options**: at least two real alternatives, including "change nothing", each with its expected effect and cost.
4. **Owner questions**: the two to four questions whose answers decide the record. Written by the assistant; answered by the owner in the record.
5. **Decision**: one paragraph, in the owner's words or approved by the owner, dated.
6. **Acceptance**: which suites, tests, and reports show the change worked, and the numbers expected. A record without a measurable acceptance is not ready for acceptance.
7. **Consequences**: what else changes (contracts, KB, docs, evals), what gets harder, what is now open.
8. **Evidence**: links to the reports and commits.

The first record, [`001-expedite-only-against-stockout.md`](decisions/001-expedite-only-against-stockout.md), is the worked example.

## 5. How the owner takes part

The goal is that every decision is understood by the owner well enough to defend or reverse it, and that the owner can write a spec alone by the end of Phase B.

- **Every decision record waits for the owner.** The assistant writes Context, Options, and Owner questions and marks the record `proposed`. The owner answers the questions (in chat or directly in the file) and writes or approves the Decision paragraph. Nothing is implemented before that.
- **One record per phase is written by the owner.** The assistant supplies the Context numbers and the Evidence; the owner writes Options and Decision; the assistant reviews for policy conflicts and missing acceptance. The next candidate is the demand-variability term for the projection (`002`).
- **Review checklist** the owner applies to any spec before saying yes:
  1. Does Context name a measurable symptom, not an opinion?
  2. Are the options real, with "change nothing" among them?
  3. Does Acceptance name a suite or test and an expected number?
  4. Does the change keep every §7.2 policy and the non-negotiables of `CLAUDE.md`?
  5. If the decision is wrong, how will we notice, and how do we go back?
- **Reading order** to understand a finished change: the decision record → the evaluation report it cites → the diff. The assistant links all three in the `STATUS.md` entry.
- **Questions back to the assistant are part of the process**, not an interruption. A question that cannot be answered from the spec means the spec is missing something; the answer goes into the record, not only into chat.

## 6. Spec hygiene

- Keep `PROPOSAL.md` the source of intent; when a decision changes it, edit the affected section and cite the record number there.
- Keep contract comments, KB pages, and engine docstrings saying the same thing as the decision; the evaluation refresh after a change is the moment to check (`EVAL_GUIDE.md` §2, gate G1).
- Retire specs explicitly: a KB page gets `status: deprecated`, a case gets removed with a note in the suite header, a record gets `superseded by`.
- Owner-only explanations (chat, artifact pages) may be in Chinese; everything in this repository, GitHub, and Databricks is English.
