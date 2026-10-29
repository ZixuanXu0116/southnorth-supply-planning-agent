# Evaluation Guide

How this project proves that the agents answer correctly and that the planning workflow is worth running. Companion guides: [`SPEC_GUIDE.md`](SPEC_GUIDE.md) and [`PROMPT_CHANGES.md`](PROMPT_CHANGES.md). The design intent is `PROPOSAL.md` §10; this guide is the operating manual.

## 1. Principles

1. **The ground truth is ours.** The data is generated, so every reference answer is computable and the future of every planning case is known. Grading is deterministic code; an LLM judge is used only for properties no code can check (tone, commitment language, in Phase C), and only after it is calibrated against the owner's labels.
2. **The eval never leaks into the agent.** No agent role can read the `eval` schema (the SQL guard denies it); reference answers, expected behaviors, and scenario ground truth never appear in prompts, KB pages, or tool outputs.
3. **Reference answers are bound to a data snapshot.** `snm-evals refresh` recomputes them from `reference_sql` on the loaded snapshot and records generator version and seed; an agent run is scored only against answers for the snapshot it ran on.
4. **Every failure links to its trace.** A run writes a JSONL trace and a run manifest (commit, prompt digest, KB digest, package versions, data snapshot); a report names the failing case and what was missing.
5. **Comparisons are paired.** Arms and prompt versions run on the same cases and are compared with paired statistics; headline numbers carry intervals and come from repeated runs.
6. **Locked splits run at milestones only.** The `test` split is never used to tune anything.

## 2. Gates: where evaluation happens

| Gate | Trigger | What runs | Cost | Who looks |
|---|---|---|---|---|
| **G0 commit** | every commit | `uv run pytest && npm test`: unit tests, contract tests on the `test` profile, SQL guard red-team cases, engine arithmetic, case-server rules, scorer tests, harness tests with the faux provider | none | assistant |
| **G1 data reload** | every regeneration or engine run on the lakehouse | contract conformance; `snm-data govern --check`; `snm-evals refresh --suite <each suite>`; the diff of reference answers reviewed case by case (each changed answer must be explained by the change that caused it) | none | assistant writes the review; owner reads the summary |
| **G2 behavior change** | any prompt, KB, tool, harness, or scorer change | the affected categories of the `dev` split, the new version paired against the previous one, same model and effort; one repetition to screen, three to adopt | about $0.04 per question on the mid tier | owner reads the report and adjudicates (§8) |
| **G3 engine change** | any change under `data/src/snm_data/planning/` | engine tests; `snm-data replay`; `snm-data oracle`; `snm-evals planner` on a local run | replay and oracle free; planner about $0.5 per run | owner reads the oracle and replay tables |
| **G4 milestone** | end of a phase, owner's call | the locked `test` split on the strong tier, three repetitions, all arms | $15–25 | owner signs off the arms and budget before, reads the report after |
| **G5 production (later)** | nightly | a canary subset replayed against the live snapshot; drift of reference answers and pass rate alerted; sampled sessions labeled by a person | small | both |

A change that fails its gate is not merged. A gate that cannot run (credit, credentials) is recorded as skipped in `STATUS.md`, never silently.

## 3. Suites and the case model

Suites are YAML files under `evals/cases/`: `seed.yaml`, `bank_v1.yaml` … `bank_v8.yaml` (each bank includes the previous one), `followup_v1.yaml` (multi-turn threads: earlier questions under `turns:`, scored on behavior; the last question scored like any case; decision record 006), and the planned `planning_v1.yaml` (replenishment scenarios). Answers live under `evals/answers/<suite>.json`; runs under `runs/evals/<batch>/`; reports under `evals/reports/<batch>.md`.

A case (`evals/src/snm_evals/cases.py`):

| Field | Meaning |
|---|---|
| `id`, `split` | stable identifier; `dev` or `test` (chosen at creation, stratified by category, never by score) |
| `question`, `role` | the user's words; the role the agent runs as (decides the database principal and the masks) |
| `category` | `covered` (a governed definition exists), `adhoc`, `unfamiliar` (tables the KB does not describe), `trap`, `multi_step`, `investigation`, `clarify`, `decline`, `restricted` |
| `expected_behavior`, `acceptable_behaviors` | `answer`, `answer_with_assumption`, `clarify`, `decline`, `policy_restricted` |
| `trap_tags` | which trap the case sets (double counting, site isolation, zero versus no data, fiscal periods, units, metric definition, role access, material numbers, …) |
| `required_kb_docs`, `required_tables` | what a correct run must consult; used for retrieval metrics |
| `reference_sql`, `answer` | one guard-passing statement for the case's role; the answer spec: `keys`, `values` with `abs` or `rel` tolerance, `must_mention` word groups, `order_matters`; `answer_variants` for other defensible groupings |
| `notes` | why the case exists and what makes it hard |

Planner cases are not YAML: `snm-evals planner` samples them from a local engine run, stratified by lever, and checks `one_event`, `right_action` (the engine's action, or a deviation justified by a dominance check), and `no_refusal`.

## 4. How cases are generated

Each source has a rule and an owner's part. A suite is not done until every source that applies has been covered.

| Source | Rule | Owner's part |
|---|---|---|
| A KB metric page | at least one `covered` case per page, one per natural breakdown (site, period); one variant that uses an alias instead of the page's title | reads the page and asks the question the way a planner would |
| The trap list in `DATA_MODEL.md` | at least one `trap` case per trap, tagged; the wrong reading must produce a different number | picks the traps that would fool them |
| Scenario ground truth (`eval.anomaly_ground_truth`, scenarios A1–A8) | one `investigation` case per scenario: "is X underperforming / why", with `must_mention` of the cause; the case notes say whether the cause is documented in the KB (a discoverable fact) or not (a discovery task, graded as such) | decides whether an undocumented cause is fair to ask |
| Domain policies (`PROPOSAL.md` §7.2) | one `decline` or `restricted` case per policy per affected role (site isolation, people analytics, export control, commercial columns) | checks the expected behavior is what the policy says, not what is convenient |
| The engine run | planner cases stratified by lever × condition × triage (`--per-lever`) | reads two prepared cases per run and says whether a purchaser would accept the draft |
| A traced failure that was fixed | one regression case, mandatory, with the failure card linked in `notes` | adjudicates the failure first (§8) |
| A follow-up thread (`followup_v1`) | two or three questions where the later ones depend on the earlier ones ("now by site", "same for last period"); the reference is the last question's answer computed standalone; threads long enough to cross a compaction boundary once compaction exists | writes the threads: they know what they would ask next |
| Supplier personas (Phase C) | per persona × scenario, with the hidden truth as the reference; injection text in the messages | reviews the personas for realism |

Writing rules:

- Ask in the user's words, not the table's; a question that names a column is a test of the schema, not of the agent.
- Every question has one defensible reading, or it is a `clarify` case; a second reading that is also defensible becomes an `answer_variants` entry.
- About 15 % of each suite are negative cases (`clarify`, `decline`, `restricted`).
- `reference_sql` is a single statement that passes the guard for the case's role; it is the spec of the number, so it is reviewed like code.
- Tolerances are justified in `notes` (rounding, rate denominators), never widened to make a run pass.
- `must_mention` lists the fact, with its reasonable spellings, never a sentence.
- The `test` split is drawn when the suite is written, never after seeing scores.
- Suite headers state the generator version and the as-of date they need.

## 5. Metrics

| Layer | Metric | Definition | Where |
|---|---|---|---|
| Answer correctness | pass rate, per category | a case passes when the behavior is acceptable and every expected value and mention is present (`score.py`) | `snm-evals run`, report table |
| | paired difference | McNemar on discordant pairs between arms or prompt versions; bootstrap for costs | report |
| Retrieval | required docs read | share of runs that read every `required_kb_docs` page | trace |
| Efficiency | cost, turns, SQL statements, wall clock per question | from API usage and the trace, per run, reported next to quality | report |
| Guardrails (zero tolerance) | faithfulness violations | a reported number not traceable to a cited result (`faithfulness.ts`) | trace, must be 0 |
| | guard violations | a refused or rewritten statement in the audit log during an eval run | tool server audit, must be 0 |
| | leakage | a masked value or an off-scope row in an answer; canaries in Phase C | scorer and canary checks, must be 0 |
| | commitments | an agent text that accepts, promises, or orders (Phase C) | rule plus calibrated judge, must be 0 |
| Engine | reference cases reproduced | hand-computed projections and levers in `test_planning.py` | G0, must be 100 % |
| | replay recall, flagged share, precision, lead days, timing error | shortage cases against the emergency orders that followed (`history.py`) | `snm-data replay` |
| | regret per case, agreement with the oracle, per condition | each option priced on the realized demand (`oracle.py`) | `snm-data oracle` |
| Planner | one event, right action, no refusal; justified deviations; cost per case | deterministic checks on the case store after the run | `snm-evals planner` |
| Later (Phase C–D) | escalation recall and precision; proposal extraction accuracy; best-feasible-option recovery; rounds and days against the human baseline; 13-week closed-loop regret against the standard-MRP policy | `PROPOSAL.md` §10.2, §10.9 | |

Targets are in `PROPOSAL.md` §10.9. The noise floor of a pass rate is about `1/sqrt(cases × repetitions)` (106 cases × 1 repetition is about ±10 points on a difference; 3 repetitions about ±6), so a difference smaller than that is reported as "not distinguishable", not as a win.

## 6. Running and reporting

```
uv run snm-evals refresh --suite bank_v8                       # reference answers for the loaded snapshot
uv run snm-evals run --suite bank_v8 --split dev --arm main --arm main_v2 --parallel 4 --max-usd 10 --report
uv run snm-evals run ... --repeat 3                             # repetitions for a headline
uv run snm-evals run ... --retry-errors --batch <batch>         # rerun the errored runs of a batch
uv run snm-evals score <batch>                                  # rescore after a scorer fix
uv run snm-evals mlflow-upload <batch>                          # when the upload at the end of a run failed
uv run snm-data replay && uv run snm-data oracle                # engine gates (offline, on out/small)
uv run snm-evals planner --planning out/planning/<as-of> --report --batch planner-prepare-<tag>
```

A report (`evals/reports/<batch>.md`) states: suite, split, snapshot, arms with prompt version, model and effort, repetitions, total cost; the pass table per case and per category; every failure with what was missing and a path to its trace; the adjudication of each failure (§8); notes on anything the numbers do not show. Batch names say what was compared (`bank-v8-dev-sonnet`, `planner-prepare-v3-engine030`); a rerun of the same configuration gets `-rep2`.

Errored runs (timeouts, API errors, credit exhaustion) are not scored as failures: they stay in the batch as errors, are reported separately, and are rerun with `--retry-errors`.

## 7. Health checklist for the evaluation itself

Run this when a suite is created or changed, before a milestone run, and whenever a result is surprising. Most surprising results are bugs in the eval.

- **Infra is not the model.** Errors, truncations, and refusals are separate rows from wrong answers.
- **"No answer" is not "negative answer".** `clarify` and `decline` are graded behaviors, not missing output.
- **Clean state per run.** The planner evaluation syncs the engine run into a fresh scratch case store; analytics runs share nothing.
- **Deterministic.** The generator is seeded; the engine is deterministic; grading is code; anything the model sees is sorted.
- **Config parity.** The runner calls the real CLI entry points (`dist/cli/ask.js`, `dist/cli/case.js`) with the shipped prompts and tools; nothing is reimplemented for the eval.
- **Trajectories saved.** Every run has its JSONL trace and manifest; a report links them.
- **Oracle and null through the pipeline.** The reference answers themselves must score 100 % through the scorer; a constant or empty answer must score 0. Run both when the scorer changes.
- **Ground truth unreachable.** The `eval` schema is denied to agent roles by the guard (red-team case); answers are not in `kb/`; scenario causes are documented in the KB only when the case notes say the cause is discoverable.
- **Grader neither rigid nor lenient.** Key aliases cover the codes a correct answer may use; tolerances are justified; a plausible wrong answer must fail. Spot-check failures after every run: if more than one in ten is a grader error, fix the grader before reading the numbers.
- **The mechanism is wired.** The ablation arms (`baseline` without KB, `baseline_bare` with metadata only) must score lower on the categories the KB serves; if they do not, the eval is not measuring the KB.
- **Headline recomputed from rows.** Reports are generated from `results.jsonl`, never typed in.
- **Resolution checked before paying.** Compare the noise floor (§5) with the smallest difference worth acting on before a run; add repetitions or cases rather than reading noise.

## 8. How the owner takes part

The point is that the owner can judge whether a number means what it says, and can extend the bank alone.

- **Authoring.** The owner writes three cases of every new suite (one trap, one negative case, one of their choice) from the sources of §4, in the case format; the assistant writes `reference_sql` with them, and the owner checks the reference against the data once by hand (a `run_sql` through the tool server as the case's role). First exercise: three threads for `followup_v1`.
- **Review.** Every new case passes the owner's checklist before `refresh`: the question is natural; the expected behavior is what a planner would want; the trap is fair; the tolerance makes sense; the role is the one who would ask.
- **Adjudication.** After each G2–G4 run, every failure is triaged into one of four classes: *agent wrong*, *reference wrong*, *spec ambiguous*, *infrastructure*. The assistant proposes the class with the trace excerpt; the owner decides the ambiguous ones, and the decision is recorded in the report and, when it changes a case, in the suite. First exercise: `c142` and `c154`, which fail in every arm because the scenario behind each (late RFID confirmations, a duplicated replication day) is not documented anywhere the agent can read; the owner decides whether they stay as discovery tasks, move to a documented-cause version, or both. Decided 2026-10-19: `c142` moved to a documented-cause version (`kb/guides/rfid_tracking.md`); `c154` stays a discovery task. A case marked `discovery: true` is reported on its own line and kept out of the headline pass rate and the paired comparisons.
- **Break-the-eval drill.** Once per phase the owner picks one deliberate corruption (a wrong KB definition, an unmasked commercial column, an off-by-one in a reference query) without telling the assistant which; the gates are run and the report says which gate caught it. The result goes to `STATUS.md` facts; a corruption nothing caught becomes a new check.
- **Milestone sign-off.** Before a G4 run the owner approves the arms, the model tier, and the budget; afterwards they read the report and name the next three failures to chase.
- **Reading an oracle or replay table.** The assistant explains the first table of each new report in plain words (what the policy is, what the cost model charged, why the regret moved); the owner restates the conclusion in their own words in chat before the next engine change starts. A conclusion that cannot be restated is not understood, and the report is rewritten.
