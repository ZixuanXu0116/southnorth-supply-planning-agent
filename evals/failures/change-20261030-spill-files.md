# Change card: spill files for oversized tool outputs

- **Status:** adopted 2026-10-30 (spill files are part of `main`)
- **Trigger:** the owner's context-budget design (`docs/TECHNICAL.md` §6, step 2: spill to a file, keep a pointer), not a failed run
- **Arm:** `main_spill` (= `main` plus spill files); `main` is unchanged
- **Prompt surfaces touched** (`PROMPT_CHANGES.md` §1): one new tool (`read_output`, name and description) and the truncation note the model reads when an output is cut
- **Suite:** the data agent's suites (`snm-evals run --arm main --arm main_spill`)

## Expected

When a tool output is longer than the context ceiling (40,000 characters per call), the agent keeps the most useful part in the context and can still reach the rest on demand, without the context growing.

## Observed

Today the MCP bridge keeps the first 40,000 characters and drops the rest with a note (`[output truncated: N characters omitted]`). The dropped part is gone for the run: the agent can only call the tool again with narrower arguments. Query results are not affected (they stay on the tool server behind a `result_id`), nor are knowledge-base reads (capped at 20,000 characters, with a note to read a section). How often the ceiling is hit is not measured yet; the bridge records `truncatedChars` in each call's details.

## Root cause

`context design`: the ceiling protects the context, but it discards instead of setting aside.

## Fix hypothesis

Layer: the harness (mechanism) plus one tool (a prompt surface).

1. **Spill store** (`harness/src/agent/spill.ts`, new). An output over the ceiling is saved whole to `runs/<run>/spill/<nnn>-<tool>.txt`. The context keeps the head (JSON, documents) or, for tools registered as log-like, the tail (none of today's tools are; the option exists for check and command tools), followed or preceded by the note: `[output truncated: kept the first N of M characters; the full output is saved as "<name>"; read the rest with read_output]`.
1a. **Shape line** (added after the owner's review). A spilled JSON output starts with one line naming its keys and the length of every list, so the agent sees what lies past the cut before it reads on; the head fills the rest of the ceiling.
2. **`read_output`** (new tool). Description: "Read more of a tool output that was too long and was saved to a file (its truncation note gives the name). Returns up to 20000 characters from the offset and next_offset to continue from." Parameters: `name`, `offset`. It reads only files saved by the same run.
3. **Bridge** (`harness/src/mcp/bridge.ts`). Takes an optional spill; without one, the old cut and note are unchanged. The structured result stays whole in the call's details, so the faithfulness gate and the provenance of answers are unaffected.
4. **Arm** (`harness/src/agent/runner.ts`, `evals/src/snm_evals/run.py`). `main_spill` = `main` + spill; reports compare it with `main`. Every other arm is unchanged.

Expected effect: no change on questions whose outputs fit (the only difference is one more tool in the frozen tool set); on questions that hit the ceiling, the agent reads the part it needs instead of re-running the tool or answering from a fragment.

Tests: `harness/test/spill.test.ts` (head and tail, file saved whole, paging, no access to other files, the bridge against the real tool server with a 300-character ceiling, and the arm wiring with a scripted model).

## Affected categories

Any question whose tool output exceeds the ceiling (wide tables, long profiles or samples, long metric menus); none expected among today's covered cases.

## Owner review

- Hypothesis and diff review (2026-10-30): the owner approved adopting the change and asked how it keeps the key information in the context. Answer: it changes nothing below the ceiling; above it, today's cut loses the rest, while the spill keeps it reachable by name; query results never pass through this cut (they stay behind `result_id`). The risk is an agent that answers from the kept part without noticing what was cut, so the shape line was added, and a stress test measures it (below).

## Measurement

Seed suite (18 cases), development model, one run per case and arm, 2026-10-30:

| Batch | Ceiling | `main` (cut) | `main_spill` | Outputs over the ceiling | Spill files / `read_output` calls | Cost per question |
|---|---|---|---|---|---|---|
| [`spill-normal-seed-sonnet`](../reports/spill-normal-seed-sonnet.md) | 40,000 | 16/18 | 17/18 | none in either arm | 0 / 0 | $0.054 / $0.048 |
| [`spill-stress4k-seed-sonnet`](../reports/spill-stress4k-seed-sonnet.md) | 4,000 | 16/18 | 16/18 | 24 cut (`main`) | 17 / 22 | $0.050 / $0.054 |

- At the real ceiling no output reached 40,000 characters, so the two arms differ only by one listed tool; the one discordant case (1/0, McNemar p = 1.0) is noise.
- At a forced 4,000-character ceiling (metric menus, table descriptions, and table lists were cut), the spill arm read back what it needed 22 times across 17 spills, so the pointer is understood and used; no answer got worse.
- Not shown: a benefit. The cut arm lost nothing either, because on these questions the needed part sits at the head of the output. Questions whose answer lies past a cut (a late column of a wide table) are the cases that would show it.
- USD 3.7 for both batches.

## Decision

Adopted 2026-10-30 at the owner's direction: `main` has spill files; `main_spill` stays as an identical arm so this card's batches can be rerun. The ceiling is the budget `maxToolOutputChars` (40,000; `SNM_MAX_TOOL_OUTPUT_CHARS` lowers it for a stress test).
