# Change card: spill files for oversized tool outputs

- **Status:** proposed 2026-10-30; built behind the candidate arm `main_spill`; waits for the owner's hypothesis and diff reviews and a paired measurement
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
2. **`read_output`** (new tool). Description: "Read more of a tool output that was too long and was saved to a file (its truncation note gives the name). Returns up to 20000 characters from the offset and next_offset to continue from." Parameters: `name`, `offset`. It reads only files saved by the same run.
3. **Bridge** (`harness/src/mcp/bridge.ts`). Takes an optional spill; without one, the old cut and note are unchanged. The structured result stays whole in the call's details, so the faithfulness gate and the provenance of answers are unaffected.
4. **Arm** (`harness/src/agent/runner.ts`, `evals/src/snm_evals/run.py`). `main_spill` = `main` + spill; reports compare it with `main`. Every other arm is unchanged.

Expected effect: no change on questions whose outputs fit (the only difference is one more tool in the frozen tool set); on questions that hit the ceiling, the agent reads the part it needs instead of re-running the tool or answering from a fragment.

Tests: `harness/test/spill.test.ts` (head and tail, file saved whole, paging, no access to other files, the bridge against the real tool server with a 300-character ceiling, and the arm wiring with a scripted model).

## Affected categories

Any question whose tool output exceeds the ceiling (wide tables, long profiles or samples, long metric menus); none expected among today's covered cases.

## Owner review

- Hypothesis review: pending.
- Diff review: pending.

## Measurement (proposed)

Paired `main` against `main_spill` on the seed suite (20 cases) and the bank v8 dev cases of the `unfamiliar` and `adhoc` categories, where wide outputs are likeliest: 47 cases, 94 runs, about USD 4.4 at the development model's USD 0.047 per question (`bank-v8-g080-dev-sonnet`). Pass rate, cost, turns, and how many runs spilled. Adoption (making spill files part of `main`) needs no regression and the owner's decision.

## Decision

Pending.
