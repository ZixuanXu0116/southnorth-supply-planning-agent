# 006. Answer follow-up questions in one session, compacting only between questions

- **Status:** accepted
- **Date proposed:** 2026-10-19
- **Date decided:** 2026-10-19
- **Decided by:** the owner
- **Scope:** agent behavior (data agent, `PROPOSAL.md` §5.3), evaluation (`followup_v1`), configuration (thread budgets)

## Context

The data agent answers one question per run: `runAgent` (`harness/src/agent/runner.ts`) builds a session, calls `agent.prompt(question)` once (plus at most one repair prompt), and ends when `submit_answer` terminates the loop. A planner asks follow-ups ("and Dongguan?", "same for period 11", "how does Round Rock compare?"), and the joint feature "questions inside a case thread" (track open item 8) needs the agent to keep the earlier questions' scope, definitions, and `result_id`s. `PROPOSAL.md` §5.3 already fixes the constraints: append-only transcripts, the frozen prompt and tool set, the task card preserved across compaction, compaction only at thread boundaries. Nothing measures follow-ups today: `followup_v1` is planned in `EVAL_GUIDE.md` §4 but does not exist, and the trace records token usage per response but not the context size per question.

This record is about one conversation. Memory across sessions stays as §5.3 says: a case thread that spans days starts a fresh session from the case store each time (events are the memory); no transcript is carried across sessions.

## Options

1. **Change nothing.** Each follow-up is a new run; the user restates the full question. No cost; no follow-up support; item 8 stays blocked.
2. **Rewrite the follow-up into a standalone question, then run it fresh.** A small model call (or the agent itself) turns "and Dongguan?" into a full question using the earlier turns. Cheap context; but the rewrite is a second LLM step with its own errors, earlier `result_id`s are lost, and the definitions the agent settled on (reversals excluded, site-local day) must be re-derived.
3. **Continue the same session** (proposed). The runner calls `agent.prompt()` once per question on the same agent: the transcript grows append-only, the task card carries over and is updated per question, each question ends with its own `submit_answer` that passes the faithfulness gate on its own, and earlier `result_id`s stay citable. Context grows with each question; it is measured per question, and compaction between questions (summary + task card + new question, no replay of old turns) is added only when the measurement shows it is needed.
4. **Option 3 with compaction from the start.** Same, but compact before every follow-up. Smaller context, but compaction loses detail the next question may need and adds a summarization call per question before we know it is needed.

## Owner questions

1. Option 3 (continue the session, compaction later and measured) or option 4 (compact before every follow-up)?
2. Budgets: per question as today (30 turns, USD 0.75, 300 s, 25 SQL), with a thread cap of the per-question budget times the number of questions? Proposed: yes, both from `harness/src/config.ts`.
3. Scoring: every question's behavior is checked, values only on the last question (computed standalone, `EVAL_GUIDE.md` §4)? Proposed: yes; a thread passes when every behavior is right and the last answer's values match.
4. The three threads drafted with the owner on 2026-10-19 (below) as the owner's authoring exercise for `followup_v1`?

Answers (owner, date):

1. Option 3: continue the session; compaction later, when the telemetry shows it is needed (owner, 2026-10-19).
2. Yes: the per-question budgets apply to every question of a thread (owner, 2026-10-19).
3. Yes: behavior on every question, values on the last (owner, 2026-10-19).
4. Yes, the three drafts as written (owner, 2026-10-19). Thread f02 moved from September 15 to September 16 because Round Rock confirmed no pick tasks on September 15 in the loaded data.

## Decision

Follow-up questions continue the same session (option 3). Every question of a thread gets the per-question budgets, its own faithfulness gate, and its own repair; earlier result_ids stay citable and the task card carries over. Each question records its context size, and compaction between questions is added only when that telemetry shows the context growing enough to matter. A thread is scored on the behavior of every question and on the values of the last question, computed standalone.

## Acceptance

- `npm test -w harness` and `uv run pytest` green (shared harness: the planner's imports of `runner.ts` unchanged).
- A single-question run produces the same `result.json` shape and the same scores as before (one existing suite case run through the new path, compared by a test with a fake model).
- `followup_v1` with the three owner threads plus about ten more, dev split, `main` arm on the mid tier: every thread's last answer passes at the rate of comparable single-question cases in `bank_v8` (within the noise floor), first run about USD 1–2 (asked before running).
- The trace records, per question, the input tokens of the first and last response and the cache-read share; the report shows the context growth per question, which decides when compaction is needed.

## Consequences

- `runAgent` gains a list of questions (a single question stays the default); `submitted`, the repair count, and the budgets reset per question; the result lists one answer per question. `ask.ts` takes the earlier questions as repeated `--earlier` options.
- Case format: a `followup` category and a `turns:` list of earlier questions with their expected behaviors (`evals/cases/followup_v1.yaml`); `question` stays the last question, so reference answers and `snm-evals refresh` keep working unchanged.
- Compaction, when added, appends a system message with the summary and the task card and starts the next question from it; the transcript before it is kept in the trace, never edited (`CLAUDE.md`, append-only context).
- Not in scope: memory across sessions, the case-thread surface in the web pages (replenishment track), and sub-agents.

## Draft threads (owner exercise, 2026-10-19)

| Thread | Role | Questions | Last question expects | Tests |
|---|---|---|---|---|
| f01 | `ops_analyst_global` | How many delivery lines did Longgang ship in fiscal period 10 of FY2026? / And Dongguan? / Same for period 11, both sites side by side. | answer: lines shipped by shipping point `C100`, `C300`, P11 | carrying the definition (reversals excluded) and the period; Dongguan shares plant `CN10` |
| f02 | `ops_analyst_global` | How many pick tasks were confirmed at Longgang on September 15, 2026? / What was the median cycle time for those? / How does Round Rock compare on the same day? | answer: median pick cycle time on 2026-09-15 site-local for `SZ01` and `RR01` | "those" and "same day" resolved; Round Rock's local day is America/Chicago |
| f03 | `site_manager_cn` | How many pick tasks did Dongguan confirm in fiscal period 11 of FY2026? / Now break it down by shift, without system users. / How does that compare with Round Rock? | `policy_restricted` (site isolation), Dongguan figures only | a restricted follow-up is refused even when the conversation flows into it; an empty filtered result is not "zero" |

## Evidence

- `harness/src/agent/runner.ts` (one `prompt` per run), `harness/src/agent/answer-tool.ts` (`submit_answer` terminates the loop).
- `docs/PROPOSAL.md` §5.2, §5.3; `docs/archive/PROPOSAL_v1.md` (task card for multi-turn follow-ups, compaction at question boundaries).
- `docs/EVAL_GUIDE.md` §4 (follow-up threads), §8 (owner authoring).
- `evals/reports/followup-v1-dev-sonnet.md` (2026-10-23): first run, `main` 13/13 threads, USD 1.09; the largest prompt in the batch was under 26,000 tokens, so compaction stays deferred.
