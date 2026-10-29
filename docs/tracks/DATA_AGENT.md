# Track: the data agent

The project runs as two working sessions from 2026-10-18 (the owner's decision): this track improves the **data agent** (the analytics agent of v1: questions about the warehouse and supply data, answered through the governed tools), and the **replenishment track** continues the agentic replenishment workflow (engine, case server, web pages, planner and intake agents). This file is the data-agent track's status and hand-off; `docs/STATUS.md` keeps one line pointing here. Start every session of this track by reading `CLAUDE.md`, this file, and the parts of `docs/STATUS.md` it names; update this file before the session ends.

## Scope of this track

| Area | Files |
|---|---|
| The agent loop and its arms (`main`, `main_v2`, `main_v1`, `baseline`, `*_bare`) | `harness/src/agent/runner.ts`, `harness/src/cli/ask.ts` |
| Answer, gate, task card, provenance | `harness/src/agent/{answer-tool,faithfulness,task-card,queries}.ts` |
| Prompts | `prompts/main/` (current `v4`), `prompts/baseline/` |
| Data tool server: SQL guard, metric tools, result handles, per-role credentials | `tool_server/` |
| Knowledge base (shared, see below) | `kb/` |
| Semantic layer outside planning | `semantic/metric_views/`, `data/pipelines/curated/` and their `data_contracts/` |
| The local question page (`snm-ask`, for the owner to try the agent) | `apps/ask/` |
| Evaluation of the data agent | `evals/cases/seed.yaml`, `bank_v1.yaml` … `bank_v8.yaml`, `evals/answers/`, scoring and runner (`snm-evals refresh / run / score / mlflow-upload`), reports `evals/reports/seed-*`, `bank-*` |

## Owned by the replenishment track (coordinate before changing)

`case_server/`, `apps/web/`, `data/src/snm_data/planning/`, `harness/src/agent/planner.ts`, `harness/src/mcp/case-server.ts`, `prompts/planner/`, `evals/src/snm_evals/{flow,planner}.py`, the planning views and their KB pages (`replenishment_case`, `supply_position`, `supplier_profile`, `kb/metrics/projected_stock.md`), decision records 003 to 005. The data generator (`data/src/snm_data/generator/`) serves both tracks: a generator change needs both tracks' reference answers refreshed, so agree on it first.

## Shared files

- **Harness modules both agents use**: `runner.ts` exports helpers the planner imports (`increment`, `newRunId`, `payloadOf`, `textOf`, `thinkingOf`, `UsageTotals`, `DEFAULT_RUNS_DIR`); `mcp/bridge.ts`, `config.ts`, `models.ts`, `agent/trace.ts`, `agent/kb-tools.ts`, `agent/manifest.ts`, `agent/prompts.ts`. Change a signature only with both tracks' tests green: `npm test -w harness` and `uv run pytest`.
- **`kb/`**: the planner reads it too; keep page names stable, and keep `kb/INDEX.md` consistent.
- **`docs/PROPOSAL.md`, `docs/DATA_MODEL.md`, the process guides**: edit only your track's sections; a cross-track change gets a decision record.
- **`docs/STATUS.md`**: this track writes here, not in STATUS (one pointer line there).
- **Branches**: each session develops on its own branch. Before starting work and before each push, merge the other track's latest branch (`git fetch origin` then `git merge origin/<branch>`); never rebase or force-push a branch the other session uses. The repository's default branch is `claude/friendly-johnson-k5eqp9` (the replenishment session's branch).

## State on 2026-10-19

- **Latest full dev run**: [`evals/reports/bank-v8-g080-dev-sonnet.md`](../../evals/reports/bank-v8-g080-dev-sonnet.md), the first on generator 0.8.0 data: `main` 89/92 (97 %) after two reference corrections (87/92 as run), USD 4.35 ($0.047 per question, 87 % cache read). Without c154 (a discovery task by the owner's decision): 89/91. Failures: c109 (new on 0.8, agent wrong: transfers to the Dongguan overflow hub left out although the question includes them; fixed by a KB rule, see open item 1), c142 (moves to a documented-cause version, KB note by the follow-up thread), c154 (discovery task). Earlier evidence: `bank-v8-dev-sonnet.md` on 0.7.0 (`main` 104/106, `main_v2` 102/106); the seed suite is saturated (`seed-baseline-v1.md`); bank v1 dev `main` 46/46 against `baseline` 42/46 (`bank-v1-dev-1.md`); the bare-metadata ablation in `seed-v1.md`.
- **The data**: generator 0.8 (`DATA_MODEL.md` §9: deliveries are the CO27 picking lists of production orders, no external customers, the Round Rock overflow hub's pull and move flows; the trade extract and scenario A5 removed). Reference answers refreshed on it; 24 cases retired (the dev split has 92 cases), `c139` reworded to fiscal period 11, 16 cases' reference SQL moved from movement types 601/602 to 311/312. Corrected on 2026-10-19: c176 reads the daily snapshot (MARD at the extraction already holds Longgang's morning of the next day), c184's supplier names. `snm-evals refresh` does not recompute `must_mention`: check data-bound names after every refresh.
- **Agent**: prompt `main/v4` with the task card, the numeric faithfulness gate (every value must cite a `result_id`; an untraced submission is rejected once), the governed metric views (`list_metrics`, `describe_metric`, `query_metric`), the knowledge-base tools; budgets per run 30 turns, USD 0.75, 300 s, 25 SQL statements (`harness/src/config.ts`).
- **Tool server**: single `SELECT`/`WITH` through the AST allowlist, mandatory limits, result handles (`get_result`), per-role OAuth service principals with row filters and masks in Unity Catalog (`snm-data govern`). This environment holds the credentials of `ops_analyst_global`, `site_manager_rr`, `site_manager_cn`, and `trade_compliance` (`test_live_governance.py`: 13 passed on 2026-10-19), which covers every role the cases use.
- **Reports** state the cache-read share of prompt tokens per arm (from 2026-10-19).

## Open items (order to be agreed with the owner)

1. ~~Dev-split run of `main` on generator 0.8 data, report, failure cards~~ (done 2026-10-19, `bank-v8-g080-dev-sonnet`). c109's KB rule (`concept.internal_transfers`; layer and diff approved by the owner on 2026-10-19) **adopted** on 2026-10-19 after `bank-v8-g080-c109kb-dev-sonnet`: 23/24 against 22/24 on the same 24 cases (c109 fixed, no regression), then c109 3/3 over three repetitions; USD 1.79 in all.
2. Before the milestone: test-split case c186 no longer fits the data (the projection of material 40001720 never goes negative on generator 0.8 with engine 1.0); pick a new material or question. Then the locked test split on the strong tier at the P1 milestone (about USD 15–25; the owner decides when).
3. ~~Owner exercises: adjudicate `c142` and `c154`; three threads for `followup_v1`~~ done 2026-10-19: `c142` is a documented-cause case (the late confirmation of emergency picks is in `kb/guides/rfid_tracking.md`); `c154` is a discovery task (`discovery: true`), reported outside the headline pass rate; the three threads are in decision record 006.
4. The `followup_v1` suite (multi-turn threads), context telemetry, compaction at thread boundaries (`PROPOSAL.md` §5.3): decision record 006 accepted 2026-10-19. The harness answers a thread in one session (`runQuestion` `earlierQuestions`, `ask.js --earlier`) with per-question budgets and context telemetry; `followup_v1.yaml` holds the owner's three threads with refreshed reference answers. Threads f04-f13 (assistant drafts, owner-reviewed 2026-10-23). First run 2026-10-23 (`evals/reports/followup-v1-dev-sonnet.md`, Sonnet 5.5): `main` 13/13 threads after one scorer fix (rate changes in percentage points), every turn's behavior right, USD 1.09; follow-ups start at 15,000–23,000 prompt tokens with 91 % read from the cache, so compaction is not needed at this thread length. Next: longer threads only if case-thread questions (item 8) need them.
5. Sub-agents: explorer and verifier (`PROPOSAL.md` §5.6), depth phase.
6. P1 backlog leftovers (`STATUS.md` next step 8): KB metric documents naming their metric views; the `executive` identity once metric views cover its questions (`c098`); the curated layer exposed to the agent; certification and deprecation.
7. Cleanups: the `trade_compliance` role is still deployed although generator 0.8 removed the trade extract (`harness/src/agent/roles.ts`, `data/src/snm_data/governance.py`, the `access` lists of the raw contracts, governance tests, and its service principal in Databricks; decision record 003 kept it until the export data was gone). Removing it is a governance change with an irreversible step in Databricks; the owner decided on 2026-10-19 to leave it as it is for now (it affects nothing else). The `ops_analyst_global` role description still mentions trade-compliance classification counts; it is rendered into the prompt, so it changes with a prompt version. ~~The cache-read share in the report template~~ (done 2026-10-19). The 15 lint findings of `uv run ruff check .` are all in replenishment-track files (planning engine and oracle, the decision 002 analysis, `evals/src/snm_evals/planner.py`). A scorer backlog item: derive data-bound `must_mention` names from the reference result.
8. **Joint feature, later (the owner, 2026-10-18): questions inside a case thread**, for purchasing and for suppliers, each within its permissions. Purchasing gets the data agent's read-only exploration under the `purchasing` principal (to be provisioned by the owner). A supplier gets quick answers about its own cases only, from a vendor-scoped view, never internal data (`PROPOSAL.md` §7.2, external-party isolation). The agent capability (tools, gate, task card, follow-up handling) belongs to this track; the case-thread surface in the web pages and the supplier scoping belong to the replenishment track. Specify it with a decision record before building.

## How to run

```bash
uv sync && npm ci && npm run build -w harness
# one question (reads SNM_ANTHROPIC_API_KEY and SNM_MODEL_*; behind a proxy set NODE_USE_ENV_PROXY=1)
node harness/dist/cli/ask.js --role ops_analyst_global --as-of 2026-09-21 "How many lines were shipped from Round Rock last week?"
# a batch: run, score, report, MLflow
uv run snm-evals run --suite bank_v8 --split dev --arm main --main-model <model id> --batch <name> --report --max-usd 10
uv run snm-evals score --suite bank_v8 --batch <name> --report
```

To try the agent in a browser on your own machine: `uv run snm-ask serve` (http://127.0.0.1:8001; setup in [`apps/ask/README.md`](../../apps/ask/README.md)).

Every run leaves `runs/<run id>/prompt.md`, `trace.jsonl`, and `result.json`; a batch lives under `runs/evals/<batch>/`.

## Process

Unchanged from the project: `docs/SPEC_GUIDE.md` (decision records), `docs/EVAL_GUIDE.md` (gates, case authoring, the owner's part), `docs/PROMPT_CHANGES.md` (a prompt change starts from a failure card and waits for the owner's hypothesis and diff reviews), `docs/COST_GUIDE.md` (caching, budget, approvals). The agent never reads the `eval` schema; evaluation answers never enter prompts, KB files, or tool outputs.
