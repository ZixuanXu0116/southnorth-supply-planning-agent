# CLAUDE.md

Project conventions for AI coding assistants and contributors.

## Project

Verified supply-planning agent (planner copilot) for the warehouse logistics and purchasing teams of the fictional company **SouthNorth Solutions**: a deterministic replenishment engine, an internal planner agent, and an isolated supplier-facing intake agent, built on the verified analytics agent of v1. Design: [`docs/PROPOSAL.md`](docs/PROPOSAL.md) (v2; v1 archived under `docs/archive/`). Data model: [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md).

**Start every session by reading [`docs/STATUS.md`](docs/STATUS.md)** (current state, next steps, pending owner actions, working agreements), and update it before the session ends.

Process guides: [`docs/SPEC_GUIDE.md`](docs/SPEC_GUIDE.md) (how a change is specified and decided; decision records in `docs/decisions/`), [`docs/EVAL_GUIDE.md`](docs/EVAL_GUIDE.md) (gates, case generation, metrics, the owner's part), [`docs/PROMPT_CHANGES.md`](docs/PROMPT_CHANGES.md) (failure cards in `evals/failures/`, prompt versions, the owner's checkpoints), [`docs/COST_GUIDE.md`](docs/COST_GUIDE.md) (caching, cost levers, budget, the owner's approvals).

## Language rules

- **English only** for every project artifact:
  - Repository: code, comments, docstrings, Markdown, prompts, `kb/`, eval cases and reports, contracts, data values, UI text, commit messages.
  - GitHub: PR titles and descriptions, issues, review comments and replies.
  - Databricks: catalog, schema, table, and column names and comments, tags, metric views, functions, notebooks, jobs, dashboards, MLflow experiments, runs, parameters, and tags.
- **Explanations to the project owner are mainly in Simplified Chinese**, with technical terms kept in English (identifiers, table and column names, metric names, tools, commands, file paths). This covers chat replies and progress updates, and owner-only material that is not committed or published to GitHub or Databricks (for example claude.ai artifact pages and scratchpad reports).
- Never let Chinese text leak into artifacts: translate anything the owner writes in Chinese before it goes into the repository, GitHub, or Databricks.

## Architecture boundaries

- **TypeScript (Node 22):** agent harness on PI core packages (`@earendil-works/pi-agent-core`, `@earendil-works/pi-ai`) — loop, context, prompts, sub-agents, policy, telemetry, MCP client bridge, KB retrieval tools.
- **Python (3.11):** MCP tool server (data access and SQL guards), data generator, pipelines, governance deployment, evaluation scoring.
- TS ↔ Python communicate only via **MCP over stdio**.
- Do not use `@earendil-works/pi-coding-agent` as a dependency; it may be read as reference code.
- Pin PI packages to exact versions; route all PI usage through the adapter layer.

## Non-negotiable rules

- **Read-only agent.** No tool may write to source or curated data. SQL goes through the AST allowlist in the Python tool server. The only writes in the system are append-only events in the `cases` schema, made by the separate case server with its own identity (`docs/PROPOSAL.md` §4.4, §7.1).
- **Every decision is made by a person.** No agent sends anything outside the company, records a decision, creates or changes a purchase order, or accepts a price or surcharge. The supplier-facing intake agent has no database credential and sees only one case's outward fields; supplier messages are untrusted content (`docs/PROPOSAL.md` §5.5, §7.2).
- **Append-only context.** Never edit or delete earlier transcript entries, the frozen system prompt, or the declared tool set within a session; append system messages or use server-side context editing/compaction instead.
- **Secrets:** never commit or log API keys, tokens, or credentials. Read them from environment variables. The Anthropic key lives only in the TS process; Databricks credentials live only in the Python tool server.
- **Synthetic data only.** Use publicly documented SAP standard structures with fictional values. Do not add real company names, configurations, material numbers, customers, suppliers, or volumes.
- The agent must never read the `eval` schema; evaluation answers must not leak into prompts, KB files, or tool outputs.
- Domain policies (site isolation, people analytics, export control, no commitments by agents, external-party isolation, scope of automation) are defined in `docs/PROPOSAL.md` §7.2 and must be preserved by any change.

## Repository layout (planned)

```
docs/                  design documents
harness/               TypeScript agent harness (planner and intake agent profiles)
apps/                  CLI (headless JSON mode) and the web app (case queue, decisions, supplier view)
tool_server/           Python MCP data tool server (read-only)
case_server/           Python MCP case server (append-only case store), planned in Phase B
data/                  generator, loaders, SQL pipelines, supply projection engine (planned in Phase A)
data_contracts/        per-table contracts (DDL, comments, tags, tests, access)
semantic/metric_views/ metric view definitions
kb/                    agent-facing knowledge base
prompts/               versioned prompt files
evals/                 cases, runners, scoring, reports
```

## Working agreements

- Keep changes minimal and scoped; prefer deterministic code over LLM calls when a step has a clear specification.
- Every behavior change to the agent should be measurable in `evals/`; add a regression case when fixing a traced failure. A change that picks among alternatives gets a decision record (`docs/SPEC_GUIDE.md` §3); a prompt change starts from a failure card and waits for the owner's hypothesis and diff reviews (`docs/PROMPT_CHANGES.md` §5).
- Model IDs, effort levels, and budgets live in configuration, not in code paths.
