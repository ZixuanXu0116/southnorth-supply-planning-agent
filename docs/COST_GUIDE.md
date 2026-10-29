# Cost Guide

How this project keeps model and platform spend under control, what is measured, and the owner's part. Companion guides: [`SPEC_GUIDE.md`](SPEC_GUIDE.md), [`EVAL_GUIDE.md`](EVAL_GUIDE.md), [`PROMPT_CHANGES.md`](PROMPT_CHANGES.md).

The budget is USD 130 per month for the model API and Databricks together (`STATUS.md` working agreements); Databricks stays on Free Edition through Phase B, so in practice the budget is the model API.

## 1. What a run costs

Every model request is billed on four kinds of tokens; the harness records all four per run (`usage` in the run result: `input`, `output`, `cache_read`, `cache_write`, `cost_usd`) from the API's own usage block, never estimated.

| Token kind | What it is | Price on the mid tier (per million) |
|---|---|---|
| uncached input | input the API had to process in full | 2.00 |
| cache write | input written to the prompt cache for later turns (first time it is seen) | 2.50 |
| cache read | input served from the cache (seen before in the same prefix) | 0.20 |
| output | the model's text, tool calls, and thinking | 10.00 |

An agent run re-sends the whole transcript on every turn, so input tokens grow with the square of the number of turns. That is why caching is the first lever: on the bank v8 development batch (212 runs, 4.4 turns per run, 11.8 M input tokens), 88 % of input was served from the cache, 12 % written, 0 % uncached, and the batch cost USD 9.36 (USD 0.044 per question). Without caching the same batch would have cost about USD 27.

## 2. How prompt caching works, and what our design does for it

The cache matches a **prefix**: tools, then the system prompt, then the messages, byte for byte. Any change anywhere invalidates everything after it. Reads cost a tenth of uncached input; a write costs a quarter more; a cached prefix lives about five minutes after its last use (a one-hour option costs more to write).

Choices in this project that exist for the cache as much as for anything else:

- **Frozen prompt prefix**: the rendered prompt is fixed for the run; role and as-of date enter once through placeholders, nothing time-dependent is written into it (`PROPOSAL.md` §5.1).
- **Tool set declared at session start and never changed** (`PROPOSAL.md` §5.3): adding or removing a tool mid-run would invalidate the prefix.
- **Append-only transcript**: earlier turns are never edited; an instruction change is appended as a message, which keeps the prefix intact.
- **One model per profile for the whole run**: a model switch invalidates the cache (the cache is per model).
- **Small, deterministic tool results**: compact JSON, result handles instead of full result sets, the compact `list_metrics`, KB read by section. Fewer tokens written are fewer tokens read on every later turn.

Silent invalidators to keep out of the prefix: timestamps or run identifiers in the system prompt, unsorted JSON in tool schemas, a tool list that differs between runs, prompt text rendered differently per run. The check is `cache_read` on every turn after the first: zero means something invalidated the prefix.

Context editing and compaction (the context-management work planned in `STATUS.md`) interact with the cache: server-side compaction keeps the prefix and appends a compaction block; our own summary at a question boundary starts a new prefix, which is acceptable only at boundaries.

## 3. The levers, in the order they are pulled

1. **Caching** (free): keep the prefix stable; verify the cache-read share in every report.
2. **Input hygiene** (free): fewer tokens in tool results and the KB index; read by section; handles for large results.
3. **Loop hygiene** (free): budgets per run (`maxTurns` 30, `maxSqlStatements` 25, `maxUsdPerRun` 0.75, `maxWallClockSeconds` 300); one repair turn at most; the case server refuses a second write so the planner stops.
4. **The deterministic core** (free): the engine, the scorer, the case-server checks, the replay and the oracle cost no model tokens. A step with a clear spec is written as code, not prompted.
5. **Effort level** (configuration): the profile's effort is the first quality-for-cost knob; it changes only through a decision record with the measured difference.
6. **Model tier** (configuration): the mid tier for development batches, the strong tier for milestone runs and for the verifier when it exists; a change is a decision record.
7. **Batch size and repetitions**: screening runs use one repetition on the affected categories; three repetitions only when adopting a change; the locked test split only at milestones.
8. **Platform**: Databricks Free Edition, serverless warehouse with auto-stop; the engine runs offline on the generated profile where the lakehouse is not needed (`snm-data plan --from-profile`, `replay`, `oracle`, the planner evaluation).

Not a lever here: the Batch API (half price, asynchronous). It fits single requests, not an agent loop that needs each turn before the next; it becomes relevant for the calibrated judge of Phase C.

## 4. What is measured and where

| Measure | Where |
|---|---|
| Cost, tokens, turns, SQL statements, wall clock per run | run result and trace; `runs/evals/<batch>/results.jsonl` |
| Cost per question, per arm, per category | `evals/reports/<batch>.md` |
| Cache-read share per batch | computed from the usage totals (to be added to the report template) |
| Spend per session | `STATUS.md` entry of the session |
| Monthly spend against the budget | the API console; the owner's monthly review (§5) |
| Budget stops | `--max-usd` on `snm-evals run` stops launching new runs; `maxUsdPerRun` stops one run |

Targets (`PROPOSAL.md` §10.9): planner plus intake cost per case at the median at most USD 0.40 on the mid tier. Current: about USD 0.04 per analytics question, USD 0.02–0.05 per planner prepare task.

## 5. How the owner takes part

- **Monthly review.** At the start of each month the owner reads the previous month's spend (API console) next to the `STATUS.md` session entries and sets the split for the coming month: development batches, milestone runs, platform. The assistant does not start a run that would exceed the month's development share.
- **Approval above a threshold.** A single batch expected to cost more than USD 5 waits for the owner's go (the expected cost is stated with the batch name and the number of runs); every milestone run (G4) waits regardless of cost.
- **Cost line of every report.** Every report states cost per question and the cache-read share; the owner reads that line with the pass rate. A pass-rate gain that doubles cost is a trade-off for the owner, not a win.
- **Cache audit exercise (once).** The owner opens one trace, identifies what sits in the cached prefix (tools, prompt, KB index) and what is per-run (question, tool results), and checks that `cache_read` is non-zero from the second turn. Then the quiz: which of these would break the cache: the as-of date in the system prompt; a tool added after the first turn; a different model for one turn; a KB page read in turn three. (Answers: the first three; the fourth is just new tokens.)
- **Model, effort, and budget changes** go through a decision record with the measured quality and cost difference, decided by the owner (`SPEC_GUIDE.md` §3).
- **Reading a cost regression.** When cost per question rises by more than the noise between repetitions, the assistant writes the cause (more turns, larger tool results, a cache miss) before the next change, and the owner decides whether it is acceptable.
