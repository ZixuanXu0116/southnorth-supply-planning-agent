# Developer guide

## Where to start reading

1. [`STATUS.md`](STATUS.md): what is done, what comes next, lessons learned.
2. [`PROPOSAL.md`](PROPOSAL.md): the design (§3 architecture, §4 engine and flows, §5 agents, §7 guardrails, §10 evaluation).
3. [`TECHNICAL.md`](TECHNICAL.md): technical notes (process boundaries, context, caching, memory, the loop).
4. [`DATA_MODEL.md`](DATA_MODEL.md): sites, SAP tables, traps, scenarios.
5. [`decisions/`](decisions/): decision records.
6. [`../kb/INDEX.md`](../kb/INDEX.md): the agents' knowledge base.
7. [`../evals/reports/`](../evals/reports/): evaluation reports (data snapshot, arms, model, cost, failures).

## What is built

| Part | Location | Summary |
|---|---|---|
| Replenishment engine | `data/src/snm_data/planning/` | Deterministic stock projection per material and site over 91 days; shortage, warning, pull, and excess cases; levers and triage; a demand model that gives every option its stock-out risk |
| Case server (Python, MCP) | `case_server/` | Append-only case store (local or Databricks), state machine, option pricing and ranking, outward-field check on supplier text, separate tool profiles for the planner and the intake agent |
| Agent harness (TypeScript) | `harness/` | Agent loop on PI core packages for the planner and the data agent: frozen prompt and tools, knowledge-base tools, budgets, one terminal write, faithfulness gate, JSONL traces |
| Data tools (Python, MCP) | `tool_server/` | Read-only tools; every query passes a sqlglot guard and runs as the role's own service principal, so Unity Catalog grants, row filters, and masks enforce the role a second time; results behind handles; audit log |
| Web pages | `apps/web/`, `apps/ask/` | Purchasing queue and case page, supplier page; the data agent's question page |
| Synthetic data | `data/`, `data_contracts/` | Deterministic generator of SAP-shaped data with documented traps and scenarios; one contract per table; loader into Unity Catalog |
| Semantic layer | `data/pipelines/`, `semantic/` | Curated views and Unity Catalog metric views with the governed metric definitions; `query_metric` writes the SQL |
| Knowledge base | `kb/` | Sites, policies, concepts, metric definitions, join guides |
| Evaluation | `evals/` | Question banks (dev and locked test splits), planner suites, engine replay and hindsight oracle, deterministic scoring; batches logged to MLflow |

## Quickstart

Local role play, no Databricks needed (details in [`ROLE_PLAY.md`](ROLE_PLAY.md)):

```bash
uv sync && npm ci && npm run build -w harness
uv run snm-data generate --profile small       # synthetic SAP-shaped data
uv run snm-data plan --from-profile small      # offline engine run
uv run snm-web serve                           # http://127.0.0.1:8000
```

With Databricks:

```bash
uv run pytest && npm test                      # unit, red-team, and scripted agent-loop tests
uv run snm-data load --profile small           # load into the catalog `snm`
uv run snm-data govern                         # role identities, grants, row filters, masks
uv run snm-data build                          # curated views and metric views
uv run snm-ask serve                           # the data agent's question page, http://127.0.0.1:8001
uv run snm-evals run --suite bank_v8 --split dev --arm main --report
```

Set `NODE_USE_ENV_PROXY=1` behind a proxy for commands that call the Anthropic API.

## Credentials

- Set the variables in [`.env.example`](../.env.example) in the environment, or copy it to `.env` (git-ignored).
- The harness reads its Anthropic key from `SNM_ANTHROPIC_API_KEY` only.
- `DATABRICKS_TOKEN` is used by the Python tooling (load, govern, reference answers, MLflow). The agents' tool server never receives it: it connects as the active role's service principal (`SNM_DATABRICKS_CLIENT_ID_<ROLE>`, `SNM_DATABRICKS_CLIENT_SECRET_<ROLE>`).
- Never commit tokens or keys.
