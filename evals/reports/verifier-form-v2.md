# Planner check forms: none, deterministic, self-check, sub-agent

Change card [`change-20261101-verifier-form`](../failures/change-20261101-verifier-form.md). The planner-evaluate cases and scripted supplier answers (the sampling of `verifier-mutations-v1`); the planner (prompt v6) evaluates each case once per arm from the same store state, every form on every recommendation (`--verify always`). The final recommendation of each arm is audited by the verifier's checks on another model tier than arm D's verifier (`claude-sonnet-5-5`), next to the deterministic findings (a missed service target or mandate field left unsaid; figures found in no evaluation).

| Arm | Runs | Written | Rule order | Target unsaid | Fields unsaid | Untraced figures | Auditor fail | Checks / fails | Refused writes | Mean cost | Mean turns | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A none | 36 | 36 | 36/36 | 0 | 0 | 12 | 1/36 | 0 / 0 | 0 | $0.048 | 2.1 | 15s |
| D subagent, triggered | 36 | 36 | 36/36 | 0 | 0 | 15 | 1/36 | 27 / 0 | 0 | $0.058 | 2.1 | 20s |

Cost is the planner run with its check (arm D includes the verifier); the audit is not in it. Spent in all, audit included: $4.98.

## Per case

Auditor passes over runs, then the deterministic findings counted over runs.

| Case | Mix | A none | D subagent, triggered |
|---|---|---|---|
| C-US10-40000005-SH-20261214 | all_inside | audit pass 3/3; untraced 2 | audit pass 3/3; untraced 2 |
| C-US10-40000014-WA-20261221 | outside_only | audit pass 3/3; untraced 1 | audit pass 3/3; untraced 1 |
| C-US10-40000018-SH-20261023 | all_inside | audit pass 3/3; untraced 2 | audit pass 3/3; untraced 2 |
| C-US10-40000030-SH-20261110 | outside_only | audit pass 3/3; untraced 3 | audit pass 3/3; untraced 2 |
| C-US10-40000047-SH-20261026 | all_inside | audit pass 3/3 | audit pass 3/3; untraced 1 |
| C-US10-40000048-WA-20261116 | with_decline | audit pass 1/3; untraced 1 | audit pass 2/3 |
| C-US10-40000054-SH-20261028 | cheaper_late_split | audit pass 2/3; untraced 1 | audit pass 3/3; untraced 2 |
| C-US10-40000055-WA-20261214 | outside_only | audit pass 3/3; untraced 1 | audit pass 3/3 |
| C-US10-40000475-SH-20261029 | with_decline | audit pass 0/3 | audit pass 1/3 |
| C-US10-40000548-SH-20261029 | cheaper_late_split | audit pass 3/3 | audit pass 3/3 |
| C-US10-40001231-SH-20261028 | with_decline | audit pass 0/3; untraced 1 | audit pass 1/3; untraced 2 |
| C-US10-40001379-SH-20261029 | cheaper_late_split | audit pass 2/3 | audit pass 3/3; untraced 3 |

## Auditor fails and notes (for a person to read)

- **C-US10-40000054-SH-20261028, A none, run 3**:
  - Rationale: Recommend P-2-7 (partial, E-1da8941aeafd), first in purchasing's order. It covers on 2026-11-04, 0 days below zero, 15 days below safety, stock-out risk 3.30%, total cost -133.34 USD, expected total cost -128.64 USD. It misses one mandate field: arrival (second half of 47 arrives 2026-11-04, after the 2026-10-28 limit). This is a C part, so it is ranked by risk. It meets its service target.
P-3-7 (new order, E-7aab542fa912) is inside the mandate and covers earlier, on 2026-10-23. It has the same 0 days below zero, the same 15 days below safety and the same 3.30% risk. But its total cost is 1,040.56 USD and its expected total cost is 1,045.26 USD. It meets its service target.
P-1-7 (new order, E-97206f08070d) covers earliest of the three, on 2026-10-13, with 10 days below safety and 0.00% risk. But it misses freight and surcharge, and its total and expected total cost is 2,441.21 USD. It meets its service target.
Trade-off: P-2-7 is the cheapest, but it covers later than P-3-7 and P-1-7, misses the arrival date, and has more days below safety and higher risk than P-1-7. For reference, the engine's expedited_new_order (E-eaf90f7e8244) covers on 2026-09-26 with 0.00% risk and 10 days below safety, at an expected total cost of 1,914.71 USD. Doing nothing (E-dadce40ee645) gives 55 days below zero and an expected total cost of 2,352.52 USD.
  - "This is a C part, so it is ranked by risk.": The ranking rule is expected_cost. The order is P-2-7 (-128.64), P-3-7 (1,045.26), P-1-7 (2,441.21). P-1-7 has the lowest risk (0.00% against 3.30%) but is ranked last, so the order is not by risk.
- **C-US10-40000475-SH-20261029, D subagent, triggered, run 2**:
  - Rationale: Recommend P-3-7-C-US10-40000475-SH-20261029 (E-c4fe823992d6): 46 units, coverage 2026-10-22, 0 days below zero, 12 days below safety, stock-out risk 0.60%, misses no mandate field, total cost 334.50 USD, expected total cost 335.50 USD. It meets its service target.
Trade-off with P-2-7-C-US10-40000475-SH-20261029 (E-c4a2ec69d7f8): P-2 covers earlier (2026-10-14 vs 2026-10-22), has fewer days below safety (10 vs 12), lower stock-out risk (0.00% vs 0.60%) and lower holding cost (11.82 vs 8.58 USD is higher for P-2; P-3 is lower here). But P-2 misses the freight and surcharge mandate fields, and costs about twice as much: total 672.12 vs 334.50 USD, expected total 672.12 vs 335.50 USD. P-3 has a higher price difference cost (50.60 vs 8.28 USD) but lower surcharge (125.32 vs 307.02 USD) and freight (75 vs 270 USD). Both have 0 days below zero and meet the service target.
Engine reference: expedited_new_order (E-0d3cd05af0b3) covers 2026-09-24 with 0.00% risk, but expected total cost 482.25 USD, higher than P-3; not recommended. Plain new_order (E-53348cf469fc) covers only 2026-11-06 with 8 days below zero. Doing nothing (E-6c13420aa2c2): 54 days below zero, expected total 1803.68 USD.
P-1-7-C-US10-40000475-SH-20261029 is a decline ("Cannot supply in this period") and was not ranked.
  - P-2 has "lower holding cost (11.82 vs 8.58 USD ...)" than P-3, listed among P-2's advantages. The text then says P-2 is higher.: P-2 holding cost is 11.82 USD and P-3's is 8.58 USD, so P-2's holding cost is higher. The 'lower holding cost' claim for P-2 is false, and the sentence contradicts itself.
  - P-1 is a decline ("Cannot supply in this period").: The case facts show P-1 as kind 'decline' but contain no such quoted text, so the quote is unsupported. This is minor.

## Review (2026-11-01)

Second pass of change card `change-20261101-verifier-form`, on the new base: the facts line, an expected total cost on every row, planner v6, and a verifier packet with data and figures only. Arms: no check (A) against the verifier sub-agent as it would run in production, only when triggered (D). Every non-pass verdict was read by hand.

| | A none | D sub-agent, triggered |
|---|---|---|
| Runs with a real defect | 1 of 36 (054 run 3: "this is a C part, so it is ranked by risk"; the rule is expected cost) | 1 of 36 (475 run 2: "lower holding cost (11.82 vs 8.58 USD)", a comparison that contradicts its own figures) |
| Uncertain verdicts | 9 | 4 |
| Verifier runs / fails | - | 27 of 36 runs triggered (high value 21, missed target 16, departs from the order 7) / 0 fails |
| Cost per case, latency | $0.048, 15 s | $0.058, 20 s |
| Rule order | 36/36 | 36/36 |

- **Every uncertain verdict is about the supplier's own words**: a decline's reason ("Cannot supply in this period") or the supplier's message, quoted by the planner. The verifier no longer receives supplier text (the owner's decision), so it cannot confirm a quote; `uncertain` does not block the write. They are not defects.
- **D's one defect was not checked**: case 475 run 2 met its service target, followed purchasing's order, and stayed below the value threshold, so the verifier was skipped by design.
- **The verifier still catches planted comparison defects**: on the 12 recommendations of A's first run, 10/10 "better on every figure" overclaims and 12/12 misstated gaps between expected totals were failed ([`verifier-mutations-v2-comparisons`](verifier-mutations-v2-comparisons.md), USD 0.58).
- **Reading**: on this base, real defects are rare in both arms (1 in 36 each); the triggered verifier ran on three runs in four, failed none, and added $0.010 (21 %) and 5 seconds per case. The change card's rule (the cheapest form whose defect rate is not worse than the sub-agent's) picks **no runtime check** here. The defects the first pass found were removed upstream, by the facts line, the expected totals, and v6, rather than caught at runtime.
