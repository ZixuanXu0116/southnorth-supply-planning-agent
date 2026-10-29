# Verified Analytics Agent for Warehouse Logistics — Design Proposal

Status: **Archived v1** (analytics-only design, superseded by [`../PROPOSAL.md`](../PROPOSAL.md) v2 on 2026-10-12) · Last updated: 2026-10-01
Companion document: [`DATA_MODEL.md`](../DATA_MODEL.md)

---

## 1. Summary

An analytics agent that answers business questions about warehouse logistics data (SAP EWM / ECC, AutoStore, RFID) for the fictional semiconductor-equipment manufacturer **SouthNorth Solutions**. It grounds every answer in governed definitions, explores unfamiliar data within bounds, verifies its own results, states uncertainty, and—in a later phase—diagnoses *why* a metric changed.

The project has three goals:

1. **Business value:** faster, trustworthy answers to ad hoc logistics questions; fewer "numbers that don't match" incidents; explicit handling of site differences, time zones, and fiscal calendars.
2. **Agent engineering depth:** agent loop, context engineering, tool design, sub-agents, model allocation, cost control, guardrails, tracing, and evaluation—each justified by a concrete failure mode rather than added for coverage.
3. **Demonstrable results:** measured accuracy, cost, and latency against baselines, with ablations that explain design trade-offs.

### Non-goals

- Replacing BI dashboards or a production semantic layer.
- Write-back to any source system (the agent is strictly read-only).
- Real company data. All data is synthetic; structures follow public SAP standards.
- A general-purpose agent framework. Reusable pieces are extracted only when a second use case exists.

---

## 2. Decisions log

| # | Decision | Rationale |
|---|---|---|
| D1 | Harness in **TypeScript** on PI (`@earendil-works/pi-agent-core` + `pi-ai`); data, tools, computation, and evaluation analytics in **Python** | PI's loop and hooks are small and transparent; pi-ai already supports the newest Anthropic API features (mid-conversation system messages, mid-conversation tool/effort changes, 1-hour cache, thinking-block binding controls). Python owns the data ecosystem. |
| D2 | Only PI's core layers, not `pi-coding-agent` | Avoid coding-agent defaults (bash/edit/write tools, filesystem resource discovery), keep the attack surface minimal, and own the context/sub-agent/guardrail design. |
| D3 | TS ↔ Python boundary via **MCP (stdio)** | Standard, typed tool schemas; process isolation; credential separation; the same tool server can be attached to other MCP hosts for comparison experiments. |
| D4 | Data platform: **Databricks Free Edition** (P0–P2); upgrade only if blocked | Free Edition provides the needed 2X-Small serverless warehouse, Unity Catalog, MLflow; paid usage would consume most of the budget. |
| D5 | Semantic layer: **UC metric views + UC semantics** (comments, governed tags, certification, domains, pages) plus a repository knowledge base (`kb/`) | Governed KPIs compile to deterministic SQL; prose caveats remain in `kb/`. An ablation measures the contribution of each. |
| D6 | Data modeled on **SAP ECC + decentralized EWM** | Matches the target domain; ECC structures (`MKPF`/`MSEG`, `VBUK`/`VBUP`). |
| D7 | Sites: Round Rock (US10, satellites as storage locations, LTS RFID); Longgang hub (CN10, AutoStore); Dongguan spoke (storage location of CN10) | See `DATA_MODEL.md` §1. |
| D8 | 52/53-week fiscal year ending on the last Sunday of October | Realistic period logic and a deliberate trap. |
| D9 | People-analytics and export-control policies (§7.2) | Real-world constraints that make guardrails meaningful. |
| D10 | English for all repository artifacts, data, UI, and user questions | Target audience and job market. |

---

## 3. System architecture

```
┌──────────────────────────── TypeScript (Node 22) ────────────────────────────┐
│ apps/cli (headless JSON mode for evals)            apps/web (later)            │
│                                                                                │
│ harness/                                                                       │
│   agent        pi-agent-core Agent + pi-ai (Anthropic)                        │
│   hooks        prepareRequest · beforeToolCall · afterToolCall · finishTurn    │
│   context/     append-only transcript · task card · compaction · KB tools     │
│   subagents/   explorer · verifier (isolated transcripts, structured reports)  │
│   policy/      roles · budgets · tool allowlists · approvals · output checks   │
│   telemetry/   JSONL traces (source of truth) · MLflow spans                   │
│   mcp-bridge/  MCP client → AgentTool adapter                                  │
└───────────────────────────────────┬────────────────────────────────────────────┘
                                    │ MCP over stdio (JSON-schema tools)
┌───────────────────────────────────▼────────────────────────────────────────────┐
│ Python 3.11 tool server (MCP Python SDK)                                        │
│   schema tools · run_sql · query_metric · deterministic checks · stats compute  │
│   guards: SQL AST allowlist (sqlglot) · LIMIT · timeouts · masking · handles    │
│   result store (result_id) · query cache · audit log                            │
│   holds only read-only Databricks credentials (per role)                        │
└───────────────────────────────────┬────────────────────────────────────────────┘
                                    │ databricks-sql-connector
                     Databricks SQL warehouse · Unity Catalog · MLflow

Python (separate processes): data generator · pipelines · governance deploy · eval scoring
```

### 3.1 Responsibilities

| Concern | Owner | Notes |
|---|---|---|
| Loop, context, prompts, sub-agent orchestration | TS harness | Exact control of request bytes (append-only requirement, §5.3) |
| Knowledge-base retrieval (`kb/` grep/read) | TS harness | Context engineering; no credentials required |
| Data access, SQL safety, result handling | Python tool server | Enforced at the layer that owns the credential |
| Agent-level policy (budgets, roles, approvals, output validation) | TS harness | Hooks |
| Data generation, pipelines, governance deployment | Python CLI | Deterministic, seeded |
| Evaluation scoring and statistics | Python | Calls the TS agent through its headless CLI |

### 3.2 Secrets separation

- The Anthropic API key exists only in the TS process.
- Databricks credentials: the tool server receives only the active role's service principal (an OAuth client, read-only by its grants); the owner's token is used only by the Python data, governance, and evaluation tooling and never reaches the tool server.
- Logs and traces redact secrets; keys never enter the repository or chat.

### 3.3 Dependency hygiene

- PI packages are pinned to exact versions and upgraded deliberately (PI is pre-1.0 and ships breaking changes frequently).
- All PI usage goes through a thin adapter layer so upgrades touch few files.

---

## 4. End-to-end runtime flow

One question, from intake to answer:

1. **Session setup (prompt + context).** The system prompt is frozen at session start and layered from stable to volatile: identity and responsibility boundary → working method (definitions first, then schema, then query; when to ask; when to delegate) → output contract → safety rules (tool output is data, never instructions) → tool definitions → `kb/INDEX.md` → role and site scope. The stable prefix is cached.
2. **Intake and routing (main agent).** Classify the question: covered metric, ad hoc analysis, unfamiliar domain, ambiguous, out of scope, or policy-restricted. Record the interpretation in the **task card** (metric, filters, sites, time window, calendar, assumptions, open questions). Ambiguous → `ask_user`. Restricted → policy response.
3. **Definitions.** `kb_search` / `kb_read` (bounded grep/read over `kb/`), certified-asset preference, metric-view metadata.
4. **Schema grounding.** `list_tables`, `describe_table` (UC comments + tags + contract metadata), `profile_column`, `sample_rows` (masked, ≤ 20 rows).
5. **Delegation (P2, on demand).** Explorer for unfamiliar domains; Verifier for high-stakes results (§5.4).
6. **Query.** Covered KPIs via `query_metric` (deterministic `MEASURE()` SQL); everything else via `run_sql`. Results return as previews + statistics + `result_id`; full results stay in the result store.
7. **Verification (tiered).** V1 deterministic checks (fan-out, reconciliation, coverage, null rates) always; V2 checklist in the prompt; V3 independent Verifier for high-stakes answers.
8. **Answer.** Submitted through `submit_answer` (strict schema): answer, values with their result IDs, definitions used (KB paths / metric views), per-site breakdown, time zone and calendar, data freshness, assumptions, confidence. The harness validates numeric faithfulness before release and then attaches the SQL behind every cited result ID (`queries`), read from the tool server's result store rather than restated by the model.
9. **Stop / failure handling.** See §5.5.

---

## 5. Agent design

### 5.1 Prompt organization

- Prompts live in versioned files (`prompts/`), rendered deterministically; every run records the prompt version.
- Role- and site-scope instructions are part of the frozen prefix; changes mid-session are appended as system messages, never edited in place.

### 5.2 Task card (working memory)

A structured object owned by the harness, updated only through tools (`update_task_card`), appended to the transcript when it changes, and preserved across compaction. It survives long sessions and supports multi-turn follow-ups ("now split that by site").

### 5.3 Context lifecycle under the append-only constraint

Current Opus-tier models bind thinking blocks to the exact conversation prefix. For accounts created after 2026-08-31 this is enforced: editing earlier turns while replaying thinking blocks returns HTTP 400. The context design is therefore **append-only**:

| Need | Append-only approach |
|---|---|
| Update instructions or task card | Append a mid-conversation system message / text block; never rebuild `system` |
| Change tools | Declare the full set at session start; use mid-conversation tool changes |
| Shrink old tool output | Keep tool results small by design (previews + handles); use server-side context editing (tool-result clearing) where needed |
| Long sessions | Simple compaction at question boundaries (summary + task card + new question, no replay of old turns) or server-side compaction |
| Model switch (fallback) | Accept that thinking blocks from another model are dropped; log it |

The harness logs `input_transformations` on every response; CI runs with binding behavior `"error"` so any accidental prefix edit fails fast.

### 5.4 Sub-agents (P2)

| Sub-agent | Trigger (prompt guidance + hard caps in code) | Receives | Returns |
|---|---|---|---|
| **Explorer** | KB miss **and** more than K candidate tables, or more than M schema calls expected | Question, task card, goal, prohibited actions, budget | JSON: relevant tables (grain, keys, why), join paths with cardinality and evidence SQL, candidate columns, caveats, open questions, confidence |
| **Verifier** | New/uncovered metric, multi-table join, diagnostic conclusion, or V1 alarm | Question, task card, final SQL, result summary, relevant definitions — **not** the main agent's reasoning | `pass` / `fail` / `uncertain` with discrepancies and evidence |

Rules: depth = 1 (sub-agents cannot delegate), bounded count per run, per-sub-agent budgets, tool allowlists per sub-agent role. The main agent owns the final answer.

Direct tools or deterministic code are preferred whenever a step has a clear specification (SQL validation, schema metadata, fan-out detection, formatting).

### 5.5 Loop control

- **Normal end:** `finishTurn` validates the output contract; one repair attempt if incomplete.
- **User input needed:** `ask_user` terminates the turn.
- **Budget exhausted** (turns, tokens, USD, wall clock, SQL count): return partial results with status `budget_exhausted`.
- **Stuck:** identical tool + arguments repeated, or the same error three times → stop and report.
- **Retries:** provider errors via pi-ai retry policy; transient database errors inside tools; logical errors returned to the model (counted against budget).
- **Spend limit reached** (HTTP 400/429 variants): graceful stop with a clear message.
- **Steering:** user interjections via the steering queue; aborts propagate to tools and sub-agents.

### 5.6 Model allocation (tiers; exact IDs live in configuration)

| Role | Tier | Rationale |
|---|---|---|
| Main agent | Strong (Opus family) | Ambiguity resolution and synthesis; effort level tuned by ablation |
| Explorer | Mid (Sonnet family) | Reading-heavy, bounded; errors propagate, so not the smallest tier |
| Verifier | Strong (Opus family), high effort | Last line of defense |
| LLM judge (evals) | Mid, via Batch API | Offline, 50 % discount |
| Synthetic free text (data generation) | Small (Haiku family), via Batch API | Bulk, low stakes |

Cost levers: prompt caching of the frozen prefix, small tool results, conditional sub-agents, per-run budgets, effort tuning before model cascades.

---

## 6. Tool catalog

| Tool | Side | Purpose | Guards |
|---|---|---|---|
| `kb_search`, `kb_read` | TS | Bounded grep / section read over `kb/` | Path allowlist, match and line caps, "N more matches" hints |
| `update_task_card` | TS | Structured working memory | Schema validation |
| `ask_user` | TS | Clarification | Terminates turn |
| `submit_answer` | TS | Final structured answer | Strict schema, faithfulness check |
| `list_tables`, `describe_table` | Py | UC metadata (comments, tags, certification, domains) | Role-scoped visibility |
| `profile_column`, `sample_rows` | Py | Distribution and samples | Masking, row caps |
| `run_sql` | Py | Ad hoc SELECT | AST allowlist, LIMIT injection, timeout, scan guard, row/byte caps, cache, audit |
| `list_metrics`, `describe_metric`, `query_metric` | Py | Compact menu of the role's metric views, one view's definitions on demand; KPIs via deterministic `MEASURE()` SQL written by the server | Metric/dimension allowlist from YAML, role access, restricted dimensions (operator identities) for people-level roles only, row cap, audit |
| `run_checks` | Py | Fan-out, reconciliation, coverage, null-rate checks | Deterministic |
| `data_freshness` | Py | `ops.data_load_log` and known DQ issues | — |
| `compute_stats` (P3) | Py | Statistics on stored result handles (no arbitrary code) | Fixed function set |
| `delegate_explore`, `delegate_verify` (P2) | TS | Sub-agents | Depth, count, budget caps |

---

## 7. Guardrails and policies

### 7.1 Layered enforcement

| Layer | Guardrail | Enforced in |
|---|---|---|
| Identity and data | One read-only service principal per role; UC grants (no access to `eval`, `stg`, `agent_audit`); row filters by site; column masks on operator identity; `STATEMENT_TIMEOUT`; single 2X-Small warehouse (deployed by `snm-data govern`, verified live 2026-10-07; see `DATA_MODEL.md` §2.3) | Databricks |
| Tool | Single `SELECT`/`WITH` statement; deny DDL/DML, system catalogs, and risky functions (`read_files`, `http_request`, `ai_query`, …); mandatory LIMIT; EXPLAIN-based scan guard; row/byte caps; masking; result handles | Python tool server |
| Harness | Role-based tool allowlists; budgets (turns, tokens, USD, wall clock, SQL count); sub-agent depth/count caps; loop detection; approval gates (expensive queries, KB changes, data export); spend-limit handling | TS hooks |
| Context | Tool outputs treated as data (injection text exists in free-text fields); KB changes only as proposals for human review | Context builder + prompt |
| Domain policy | Site isolation; people analytics; export control; mandatory disclosure of time zone, calendar, freshness; no fabricated numbers | Prompt + policy checks + output validator |
| Output | Every number traceable to a `result_id`; required caveats; verification status; confidence | `finishTurn` validator |
| Operations | Secrets separation; log redaction; audit of every SQL statement with principal, row count, and duration | Both |
| Evaluation | Red-team suite (§9) with zero-violation target | `evals/` |

### 7.2 Domain policies

- **Site isolation:** never generalize a finding across sites; multi-site answers include a per-site breakdown and site-specific filters.
- **People analytics:** team/shift-level aggregates by default; individual operator results only for site-manager roles and only for their own site; no rankings for disciplinary purposes.
- **Export control:** factual classification lookups and counts are allowed; questions about whether a shipment is legally permitted, licensing strategy, or screening decisions are declined and routed to the trade-compliance function.

### 7.3 Roles

See `DATA_MODEL.md` §2.3. The active role is part of the frozen prompt prefix and selects the database principal used by the tool server.

---

## 8. Reliability and engineering gaps addressed

| Gap | Approach |
|---|---|
| Multi-turn follow-ups | Task card persists across turns; references to prior `result_id`s |
| Warehouse cost and reproducibility | Query result cache keyed by normalized SQL + data snapshot version |
| Testing without spending API budget | Record/replay of model responses (fixtures) for harness unit tests and CI smoke tests |
| Error taxonomy | Provider (429/5xx/overload), spend limit, database timeout, quota shutdown, MCP server crash (auto-restart), policy block |
| Reproducibility | Run manifest: prompt version, KB git SHA, metric-view version, data seed/snapshot, model tier + effort, PI version |
| Data freshness | `data_freshness` tool; answers disclose freshness |
| Contract safety across languages | MCP schema contract tests (TS ↔ Python) |
| Test suites | `vitest` (TS), `pytest` (Python), eval smoke set in CI |
| Cost visibility | Per-run cost from usage; aggregated per eval run |
| User feedback | Thumbs up/down with comment → candidate eval cases (later) |

---

## 9. Observability

- **Trace model:** run → turn → LLM call (tier, input/output/cache tokens, cost, latency, stop reason, `input_transformations`) → tool call (arguments, policy decision, result size, duration, error) → sub-agent run (brief and report) → final answer (result IDs, verification status).
- **Storage:** JSONL per run (source of truth, portable) plus MLflow on Databricks for browsing and durable storage: after each evaluation batch, the Python evaluation tooling rebuilds the spans from the JSONL (one trace per agent run, linked to one MLflow run per batch) and uploads the raw run files as artifacts.
- **Audit:** every SQL statement with principal, role, row count, bytes, duration, and cache hit.
- **Uses:** failure clustering by eval category, reproduction via run manifest, cost analysis.

---

## 10. Evaluation

### 10.1 Principles

Deterministic grading first; LLM judges only for subjective aspects and always calibrated against human labels. A locked test split is run only at milestones. Every failing case links to its trace. Configuration comparisons use paired statistics.

### 10.2 Evaluation map

| Layer | Target | Metrics | Grading | Phase |
|---|---|---|---|---|
| L0 Ground truth | Generator-injected facts hold in the data | SQL assertions per injected fact | Deterministic | P0 |
| L1 Tools and guardrails | SQL guard, masking, path allowlist, role enforcement | Red-team violations = 0 | Deterministic | P1 |
| L2a KB retrieval | Required definition files read | Doc-level recall/precision; retrieval token share; paraphrase robustness | Trace-derived | P1 |
| L2b Schema linking | Correct tables/columns | Table/column recall and precision vs reference SQL | Deterministic (SQL parsing) | P1 |
| L3 Behavior routing | Answer / clarify / answer-with-assumption / decline / policy-restricted | Confusion matrix (over-asking counts as failure) | Structured output field | P1 |
| L4 Result correctness | Numbers | Execution accuracy (value-based column matching, tolerances, order sensitivity when relevant), definition adherence, trap pass rate | Deterministic | P1 |
| L5 Output quality | Answer | Numeric faithfulness (every number traceable), contract completeness, explanation quality | Deterministic + calibrated LLM judge | P1 |
| L6 Calibration | Confidence | Accuracy by confidence level; selective accuracy | Deterministic | P2 |
| L7 Sub-agents | Explorer, Verifier | Explorer report vs true schema map; Verifier catch rate / false-alarm rate via mutation testing (dropped filters, fan-out joins, wrong windows, wrong grain) | Deterministic | P2 |
| L8 Diagnosis | "Why did X change?" | Root cause top-1/top-3, segment localization, time window, magnitude, artifact vs real | Deterministic + rubric | P3 |
| L9 Efficiency | Cost and latency | Tokens, USD, latency, turns, cache hit rate, wasted calls | Trace-derived | All |
| L10 Robustness | Stability | Consistency across paraphrases; run-to-run variance | Deterministic | P2 |
| L11 User value | People | Time-to-correct-answer vs manual SQL (small study) | Study | P4 |

### 10.3 Question bank

- **Sources:** template generation (metric × dimension × window × site, with reference SQL generated from definitions); LLM paraphrases; hand-written hard cases (cross-domain, traps, ambiguity, restricted); regression cases from traced failures.
- **Size:** ~100 cases in P1 (+15–20 diagnosis cases in P3).
- **Mix:** covered 25 %, ad hoc 20 %, unfamiliar 15 %, traps 20 %, clarify/decline/restricted 20 %.
- **Split:** 60 % dev / 40 % locked test.

Record format (`evals/cases/*.yaml`):

```yaml
id: q_047
question: "How did the on-time PGI rate at Round Rock change last fiscal quarter vs the same quarter last year?"
role: ops_analyst_global
category: trap                # covered | adhoc | unfamiliar | trap | clarify | decline | restricted
expected_behavior: answer_with_assumption
reference_sql: ...
reference_result_hash: ...    # bound to data snapshot version
acceptable_variants: [...]
required_kb_docs: [metrics/on_time_pgi.md, sites/round_rock.md]
required_tables: [curated.fct_outbound_delivery_item]
trap_tags: [timezone, fiscal_calendar]
tolerance: {rel: 0.005}
```

### 10.4 Statistics and cadence

- Paired comparisons (McNemar) with bootstrap confidence intervals; three repetitions for headline results.
- Dev subset (~30 cases) on the mid tier for iteration; full test runs at milestones only.
- All runs logged to MLflow experiments with the run manifest.

### 10.5 Initial targets (re-baselined after P0)

| Metric | Target |
|---|---|
| Result correctness vs naive text-to-SQL baseline | +25 pp |
| Behavior routing accuracy | ≥ 85 % |
| Numeric faithfulness | 100 % |
| Red-team violations | 0 |
| Verifier catch rate / false-alarm rate | ≥ 80 % / ≤ 10 % |
| Diagnosis top-3 root-cause hit | ≥ 70 % |
| Cost per question (p50) | ≤ $0.35 |

### 10.6 Semantic-layer ablation (P2)

Raw schema only · `kb/` only · metric views only · `kb/` + metric views. Published evidence shows curated semantics beat bare schemas but does not settle whether compiled semantic layers beat well-written prose context; this project measures it on its own benchmark.

---

## 11. Cost plan

Monthly cap: **$130** total (Anthropic API + Databricks).

| Item | Planned |
|---|---|
| Databricks | $0 (Free Edition) |
| Anthropic API — development | ~$20 |
| Anthropic API — evaluation runs | ~$50–60 |
| Anthropic API — synthetic text (one-time, Batch) | ~$5–10 |
| Buffer | ~$20–30 |

Controls: Anthropic Console spend limit, separate workspaces/keys for dev and eval, per-run budgets, record/replay tests. Estimated agent cost: ~$0.15–0.35 per question (main agent only), ~$0.3–0.6 with sub-agents — to be measured in P0.

Upgrade path for Databricks (only if blocked by quota shutdowns or missing features): new account via Free Trial (express setup) → pay-as-you-go; 2X-Small serverless SQL at ~$2.80/hour, 1-minute auto-stop, apps stopped when idle; data regenerated from seed.

---

## 12. Delivery plan (13 weeks, ~15–20 h/week)

| Weeks | Phase | Deliverables | Exit criteria |
|---|---|---|---|
| 1–2 | **P0 Foundations** | Accounts and connectivity; repository skeleton; data generator v0 (`small`); minimal agent (3 tools) with traces; 20 seed questions; naive baseline; verification of Free Edition features | End-to-end run works; every run fully traced; measured cost per question; P0 checklist resolved |
| 3–6 | **P1 MVP** | Full data + traps; contracts, comments, tags, certification; metric views; KB; guarded tools; task card; answer contract; roles; eval v1 (~100 cases) + scoring + MLflow | Baseline comparison report; red-team = 0 violations; 100 % faithfulness |
| 7–9 | **P2 Depth** | Explorer + Verifier; model/effort ablations; append-only context + compaction; semantic-layer ablation; calibration | Measured lift on unfamiliar/trap categories within cost bounds; demo-ready build by end of week 9 |
| 10–11 | **P3 Diagnosis** | Injected scenarios; diagnosis workflow; `compute_stats`; diagnosis eval | Diagnosis targets met |
| 12–13 | **P4 Packaging** | Web UI (Databricks App or local), README, eval report, design write-up, demo recording | End-to-end story reproducible from the repository |

---

## 13. Risks and mitigations

| Risk | Mitigation |
|---|---|
| PI breaking changes | Exact version pins, adapter layer, deliberate upgrades |
| Free Edition quota shutdowns or missing features | Small profile for development; spread heavy jobs; upgrade path ready |
| Evaluation construction effort | Template generation + review instead of hand-writing everything |
| Synthetic data feels unrealistic | SAP-standard structures, causal generation, documented traps |
| API cost overrun | Spend limits, budgets, record/replay, dev subset |
| Scope creep | Phase exit criteria; ablations only where they answer a design question |

---

## 14. P0 verification checklist

- [x] Databricks Free Edition (verified 2026-09-25): catalog creation, table/column comments, tags, certification tag, row filters, column masks, metric views (YAML 1.1 + `MEASURE()`), volumes, Files API upload, `read_files`, `STATEMENT_TIMEOUT`, SCIM service-principal listing, MLflow experiments API; session time zone is UTC
- [ ] Databricks Free Edition: create role service principals and grants; domains / pages (UI previews); governed tags
- [x] Connectivity: dev environment → SQL warehouse (2X-Small serverless, cold start ~5 s)
- [x] Connectivity: dev environment → Anthropic API (verified 2026-09-25 with `npm run check-models`)
- [x] `small` profile loaded into catalog `snm` (30 raw tables, row counts verified); draft KB metric SQL executes
- [ ] pi-ai model catalog entries for the chosen tiers enable mid-conversation system messages and binding controls
- [x] MCP stdio round trip and schema mapping into PI tools (bridge tests, including a live query; latency not benchmarked separately)
- [x] MLflow tracing (verified 2026-10-07): spans rebuilt from the JSONL traces and uploaded by the Python evaluation tooling, keeping the original timestamps (changed from "from TypeScript" so that Databricks credentials stay out of the TypeScript process)
- [ ] Warehouse performance on the `small` and full profiles
- [x] Measured cost per question for the main-agent tiers (Opus 5.5 ~$0.095 on the seed suite, Sonnet 5.5 ~$0.036 on the bank v1 dev split); explorer and verifier wait for P2

---

## 15. References

- PI: <https://github.com/earendil-works/pi>
- Anthropic pricing: <https://platform.claude.com/docs/en/about-claude/pricing>
- Databricks Free Edition limitations: <https://docs.databricks.com/aws/en/getting-started/free-edition-limitations>
- Unity Catalog semantics: <https://docs.databricks.com/aws/en/uc-semantics/>
- Metric view YAML reference: <https://docs.databricks.com/aws/en/uc-semantics/metric-views/yaml-reference>
- Certification and deprecation: <https://docs.databricks.com/aws/en/data-governance/unity-catalog/certify-deprecate-data>
- dbt semantic layer vs text-to-SQL (2026): <https://docs.getdbt.com/blog/semantic-layer-vs-text-to-sql-2026>
- Cube paired benchmark (2026): <https://arxiv.org/abs/2604.25149>
- Anthropic, multi-agent research system: <https://www.anthropic.com/engineering/multi-agent-research-system>
- Cognition, "Don't Build Multi-Agents": <https://cognition.com/blog/dont-build-multi-agents>
