# Planner check forms: none, deterministic, self-check, sub-agent

Change card [`change-20261101-verifier-form`](../failures/change-20261101-verifier-form.md). The planner-evaluate cases and scripted supplier answers (the sampling of `verifier-mutations-v1`); the planner (prompt v5) evaluates each case once per arm from the same store state, every form on every recommendation (`--verify always`). The final recommendation of each arm is audited by the verifier's checks on another model tier than arm D's verifier (`claude-sonnet-5-5`), next to the deterministic findings (a missed service target or mandate field left unsaid; figures found in no evaluation).

> **Note on this report.** The batch's driver dropped the deterministic facts findings returned by the audit, so its first rendering showed no target or mandate field left unsaid in any arm. The columns were recomputed offline from the same final recommendations with the same check (`factsFindings`, no model call); everything else is as the batch wrote it.

| Arm | Runs | Written | Rule order | Target unsaid | Fields unsaid | Untraced figures | Auditor fail | Checks / fails | Refused writes | Mean cost | Mean turns | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A none | 12 | 12 | 12/12 | 2 | 0 | 6 | 4/12 | 0 / 0 | 0 | $0.060 | 2.2 | 14s |
| B deterministic | 12 | 12 | 12/12 | 0 | 0 | 0 | 2/12 | 20 / 8 | 8 | $0.053 | 2.7 | 17s |
| C self_check | 12 | 12 | 12/12 | 0 | 0 | 0 | 0/12 | 17 / 0 | 0 | $0.098 | 4.2 | 21s |
| D subagent | 12 | 12 | 12/12 | 0 | 0 | 3 | 2/12 | 14 / 2 | 2 | $0.072 | 2.4 | 21s |

Cost is the planner run with its check (arm D includes the verifier); the audit is not in it. Spent in all, audit included: $4.12.

## Per case

| Case | Mix | A none | B deterministic | C self_check | D subagent |
|---|---|---|---|---|---|
| C-US10-40000005-SH-20261214 | all_inside | audit pass | audit pass | audit pass | audit fail |
| C-US10-40000014-WA-20261221 | outside_only | audit pass; untraced | audit pass | audit pass | audit pass |
| C-US10-40000018-SH-20261023 | all_inside | audit fail; target unsaid; untraced | audit pass | audit pass | audit fail |
| C-US10-40000030-SH-20261110 | outside_only | audit pass; untraced | audit pass | audit pass | audit pass |
| C-US10-40000047-SH-20261026 | all_inside | audit pass | audit pass | audit pass | audit pass |
| C-US10-40000048-WA-20261116 | with_decline | audit fail; target unsaid | audit pass | audit pass | audit pass |
| C-US10-40000054-SH-20261028 | cheaper_late_split | audit pass; untraced | audit fail | audit pass | audit pass; untraced |
| C-US10-40000055-WA-20261214 | outside_only | audit pass | audit pass | audit pass | audit pass |
| C-US10-40000475-SH-20261029 | with_decline | audit fail | audit pass | audit pass | audit pass |
| C-US10-40000548-SH-20261029 | cheaper_late_split | audit fail | audit fail | audit pass | audit pass |
| C-US10-40001231-SH-20261028 | with_decline | audit pass; untraced | audit pass | audit pass | audit pass; untraced |
| C-US10-40001379-SH-20261029 | cheaper_late_split | audit pass; untraced | audit pass | audit pass | audit pass; untraced |

## Auditor fails and notes (for a person to read)

- **C-US10-40000054-SH-20261028, B deterministic**:
  - Rationale: Recommend P-2 (partial, 47 on 2026-10-23 and 47 on 2026-11-04), first in purchasing's order. Evaluation E-1da8941aeafd: coverage 2026-11-04, 0 days below zero, 15 days below safety, stock-out risk 3.30%, total cost -133.34 USD, expected total cost -128.64 USD. It misses one mandate field: arrival (second half after 2026-10-28). This is a C part, so the late arrival is priced by risk. P-3 (E-7aab542fa912) is the only offer fully inside the mandate: coverage 2026-10-23, 0 days below zero, 15 days below safety, same 3.30% risk, but total cost 1,040.56 USD and expected total 1,045.26 USD. Trade-off: P-3 meets the arrival date but costs much more, with the same risk and the same days below zero and below safety. P-1 (E-97206f08070d): coverage 2026-10-13, 0 days below zero, 10 days below safety, 0.00% risk, but it misses freight and surcharge, expected total 2,441.21 USD. Reference: the engine's expedited_new_order (E-eaf90f7e8244) covers 2026-09-26 at 0.00% risk for 1,914.71 USD; doing nothing (E-dadce40ee645) gives 55 days below zero and expected total 11,003.02 USD. Purchasing decides whether to accept the late second half or pay for P-3.
  - Doing nothing (E-dadce40ee645) gives "expected total cost 11,003.02 USD".: 11,003.02 is the no_action total_cost. The reference row has no expected_total_cost. For the ranked options, expected total equals total_cost plus expected_line_down_cost. For no_action that would be 11,003.02 + 2,349.5 = 13,352.52. The figure is mislabeled as an expected total.
- **C-US10-40000005-SH-20261214, D subagent**:
  - Rationale: Recommend P-2 (E-51ddf1ec62ea). Both proposals are inside the mandate (no fields missed), and both cover the gap with 0 days below zero. P-2 covers on 2026-12-09, with 30 days below safety and 11.04% stock-out risk. Total cost is 237.47 USD and expected total cost is 1147.97 USD. P-1 (E-245587541ed8) covers 2 days earlier, on 2026-12-07, with 28 days below safety and 10.44% risk. But its total cost is 750.17 USD and its expected total cost is 1586.67 USD. The earlier arrival lowers expected line-down cost only from 910.5 to 836.5 USD, which does not make up for the 615.24 USD price difference. Neither proposal meets the 97% service target. For reference: the engine's new_order at planned lead time (E-94204c79be6b) would cover on 2026-11-17 with 8 days below safety, 7.10% risk, and a total cost of 134.93 USD. The supplier did not offer that date, so purchasing may want to ask for it before deciding. Both offers are valid until 2026-11-08. The supplier's message has no other requests.
  - The earlier arrival lowers expected line-down cost only from 910.5 to 836.5 USD, "which does not make up for the 615.24 USD price difference.": 615.24 is P-1's price_difference_cost against the engine's baseline price. P-2's is 102.54. The price difference between P-1 and P-2 is 512.70 USD (615.24 − 102.54, or (3623.06 − 3452.16) × 3). The rationale gives the wrong figure for the comparison it makes.
- **C-US10-40000475-SH-20261029, A none**:
  - Rationale: Recommend P-3 (46 pcs, new order), ranked first. It is the only proposal inside the mandate (no fields missed). E-c4fe823992d6: covers on 2026-10-22, before the shortage date of 2026-10-29. 0 days below zero, 12 days below safety, stock-out risk 0.60%, total cost 334.50 USD, expected total cost 335.50 USD.
P-2 (E-c4a2ec69d7f8) covers earlier, on 2026-10-14, with 0 days below zero, 10 days below safety and 0.00% risk. But it misses the freight and surcharge limits, and its expected total cost is 672.12 USD, about twice P-3's.
For reference, the engine's expedited new order (E-0d3cd05af0b3) covers on 2026-09-24 with 0.00% risk and an expected total cost of 482.25 USD. That is still more than P-3. A normal new order (E-53348cf469fc) covers only on 2026-11-06, with 8 days below zero and an expected total cost of 1678.97 USD. Doing nothing (E-6c13420aa2c2) gives 54 days below zero.
P-1 is a decline from the supplier ("Cannot supply in this period."), so it was not ranked. The supplier's message had no requests.
  - A normal new order (E-53348cf469fc) has an expected total cost of 1678.97 USD.: 1678.97 is that evaluation's total_cost. The evaluation has no expected_total_cost, and its expected_line_down_cost is 52.30. In the ranked options, expected total cost is total cost plus expected line-down cost (334.50 + 1 = 335.50). The 1678.97 figure is therefore mislabeled as an expected total.
- **C-US10-40000548-SH-20261029, A none**:
  - Rationale: Recommend P-3 (E-2f2fc3a19741): the only proposal inside the mandate (no fields missed). It covers on 2026-10-24, 0 days below zero, 14 days below safety, stock-out risk 2.10%. Total cost 32,263.38 USD, expected total cost 32,281.88 USD.
P-2 (E-3de8d8a613ce) is much cheaper: total cost -12,729.49 USD, expected total cost -12,710.99 USD. It has the same 0 days below zero, 14 days below safety and 2.10% risk. But it misses the mandate arrival date: half arrives 2026-10-24 and half 2026-11-05, so coverage is 2026-11-05, after the needed date 2026-10-29. Purchasing may accept this if the split delivery is acceptable; it would need a mandate change.
P-1 (E-e4a4bc312f28) covers earliest, on 2026-10-14, with 0.00% risk and 10 days below safety. But it misses the freight and surcharge limits, and its expected total cost is 83,841.68 USD.
For reference, the engine's expedited_new_order (E-0c1cf6355356) covers on 2026-09-27 for 59,417.85 USD. Doing nothing (E-9a86a2ca2761) gives 54 days below zero and an expected total cost of 54,168.93 USD.
The supplier's message had no requests beyond the options.
  - Doing nothing (E-9a86a2ca2761) gives 54 days below zero and an expected total cost of 54,168.93 USD.: 54,168.93 is the no_action total_cost. The reference row has no expected_total_cost. On the ranked options, expected total cost is total cost plus expected_line_down_cost. Doing nothing has expected_line_down_cost 8,878.50, so the comparable figure is about 63,047.43 USD. The rationale mislabels total cost as expected total cost.
- **C-US10-40000548-SH-20261029, B deterministic**:
  - Rationale: Recommend P-3 (E-2f2fc3a19741). It is the only proposal inside the mandate: 46 pieces, covers 2026-10-24, 0 days below zero, 14 days below safety, stock-out risk 2.10%. Total cost 32,263.38 USD (surcharge 26,463.17, freight 105). Expected total cost 32,281.88 USD.
P-2 (E-3de8d8a613ce) is cheaper: total cost -12,729.49 USD, expected total cost -12,710.99 USD. It has the same 0 days below zero, 14 days below safety and 2.10% risk. But it misses the mandate's arrival date: the second 23 pieces come 2026-11-05, so coverage is 2026-11-05, after the 2026-10-29 need date. This part is criticality B, so a late arrival is not priced by risk. Purchasing can choose to accept the late half for the saving.
P-1 (E-e4a4bc312f28) covers earliest, 2026-10-14, with 0.00% risk and 10 days below safety. But it misses the surcharge and freight limits. Total cost 83,841.68 USD.
For reference, the engine's expedited_new_order (E-0c1cf6355356) covers 2026-09-27 with total cost 59,417.85 USD. Doing nothing (E-9a86a2ca2761) gives 54 days below zero, 6.54% risk and expected total cost 54,168.93 USD. The supplier message held no requests.
  - Doing nothing (E-9a86a2ca2761) gives ... expected total cost 54,168.93 USD.: 54,168.93 is the no_action total_cost. Its expected line-down cost is 8,878.50. Using the same method as the other options (total_cost + expected_line_down_cost), its expected total cost would be about 63,047.43. The figure is mislabeled and understates the expected cost of doing nothing.
- **C-US10-40000018-SH-20261023, A none**:
  - Rationale: Recommend supplier option 2 (P-2, eval E-bd46a6a7fcdd). Both offers are 77 pieces and meet every mandate field (none missed). P-2: covers 2026-10-18, 0 days below zero, 15 days below safety, stock-out risk 6.12%, total cost 8,597.53 USD, expected total cost 8,639.03 USD. P-1 (eval E-11b6f5179b0b): covers 2026-10-16, two days earlier, 0 days below zero, 13 days below safety, stock-out risk 3.60%, but total cost 28,981.47 USD and expected total cost 29,001.47 USD. P-1's lower risk is worth only about 21.50 USD in expected line-down cost (20 vs 41.50), far less than its higher price difference (24,034.78 vs 4,005.54 USD). Both arrive before the trigger date 2026-10-23. For reference only: the engine's new_order (E-0f15c8f968e5) covers 2026-10-17 at 4,767.51 USD total, but the supplier did not offer that price. The expedited_new_order (E-f6fb1ca75fc1) covers 2026-09-24 with 0.00% risk but costs 115,939.51 USD. Doing nothing (E-f5f06d1a6a39) leaves 60 days below zero. Supplier message had no instructions, only "Our options are below."
  - The rationale recommends P-2 and gives its 6.12% stock-out risk. It never says whether P-2 meets the service target.: P-2 (E-bd46a6a7fcdd) has meets_service_target=false. The part's service target is 0.97, and 6.12% risk means about 93.9% service. The rationale leaves out this missed service target. It does contrast P-2 with the expedited option's 0.00% risk, but it never says P-2 falls short.
- **C-US10-40000018-SH-20261023, D subagent**:
  - Rationale: Recommend P-2 (77 pcs, arrives 2026-10-18), first in purchasing's order. Both proposals are inside the mandate (no fields missed) and both cover the gap with 0 days below zero. P-2 (E-bd46a6a7fcdd): coverage 2026-10-18, 15 days below safety, stock-out risk 6.12%, total cost 8,597.53 USD, expected total cost 8,639.03 USD. P-1 (E-11b6f5179b0b): coverage 2026-10-16, 13 days below safety, stock-out risk 3.60%, total cost 28,981.47 USD, expected total cost 29,001.47 USD. P-1 arrives 2 days earlier and has lower risk, but its expected line-down saving (20 vs 41.5 USD) is far smaller than its extra price difference (24,034.78 vs 4,005.54 USD). Neither proposal meets the 97% service target. For reference only: engine new_order (E-0f15c8f968e5) covers 2026-10-17, risk 6.06%, total cost 4,767.51 USD, at the planned price the supplier did not quote. The engine expedited_new_order (E-f6fb1ca75fc1) covers 2026-09-24 with 0.00% risk and meets the service target, but costs 115,939.51 USD. Doing nothing (E-f5f06d1a6a39) leaves 60 days below zero, with an expected total cost of 19,637.5 USD line-down plus holding. The supplier's message contained no requests or instructions.
  - Doing nothing leaves 60 days below zero, "with an expected total cost of 19,637.5 USD line-down plus holding.": For no_action (E-f5f06d1a6a39), 19,637.5 is only the expected line-down cost. Holding cost is 350.62 on top of that, so line-down plus holding is 19,988.12. The evaluation's total_cost is 60,350.62, which includes the 60,000 stock-out cost. The stated figure is mislabeled.
- **C-US10-40000048-WA-20261116, A none**:
  - Rationale: Recommend the engine's new order (E-61855b31ab37) over the supplier offers. It covers on 2026-10-22, with 0 days below zero and 0 below safety, stock-out risk 10.44%, total cost 582.05 USD and expected line-down cost 5755.5 USD.
Best supplier offer, P-3 (E-12104e0f5f62): inside the mandate (no fields missed). It covers on 2026-11-09, with 0 days below zero and 0 below safety. Stock-out risk is 15.77%, total cost 2626.55 USD (2174.28 of it price difference), and expected total cost 9505.05 USD.
P-2 (E-c7d985e20921): misses the freight and surcharge fields. It covers on 2026-11-01, with 0 days below zero and 0 below safety. Stock-out risk is 13.10%, total cost 3910.91 USD, and expected total cost 10118.41 USD.
Reason to prefer the engine option: it covers earlier, has lower stock-out risk and costs less than both offers.
Caveat: the new order is the engine's standard order at planned lead time and contract price. It is not a quote from Straits Machining. Their option 1 (P-1) was a decline: 'Cannot supply in this period.' Purchasing should confirm the supplier will accept a standard order on those terms. If not, P-3 is the best offer inside the mandate. Both offers are valid until 2026-11-08.
No action (E-ed87ac29ebb2) has a 21.98% stock-out risk and 36 days below safety.
  - The rationale describes the recommended engine new order (risk 10.44%, covers 2026-10-22) and never says it misses the service target. It also doesn't say the supplier offers miss it.: The reference row E-61855b31ab37 has meets_service_target=false. The service target is 0.97, and a 10.44% stock-out risk is far from it. Both supplier offers also have meets_service_target=false.

## Review (2026-11-01)

Every auditor fail was read by hand against the evaluations: all eight are real, none is a false alarm.

| Class | Fails | Arms | What the rationale says |
|---|---|---|---|
| A reference row's cost labeled "expected total" | 5 | A (475, 548), B (054, 548), D (018) | Doing nothing or an engine option given its plain total, or its line-down alone, as an "expected total cost". `rank_options` gives `expected_total_cost` for the proposals only, so the planner labels whatever total it has; the label understates doing nothing (548: 54,168.93 against about 63,047.43) |
| Missed service target left unsaid | 2 | A (018, 048) | The recommended option's risk (6.12 %, 10.44 %) without saying it misses the 3 % target; the deterministic check finds the same two |
| Wrong comparison figure | 1 | D (005) | "the 615.24 USD price difference" between P-1 and P-2, which is P-1's price difference against the reference price; the difference between the two is 512.70. The conclusion holds; the figure is wrong |

What each check did before the write:

- **B (deterministic)** refused 8 drafts: 1 for a missed target left unsaid (014), 7 for figures found in no evaluation that are correct differences or sums of evaluated figures (for example 438.70 = 1,586.67 − 1,147.97, and 20,362 = 29,001.47 − 8,639.03). Every refused draft was rewritten and written, but the rewrites drop the amount of the trade-off ("cheaper on both totals" instead of "438.70 USD more"). The figure check cannot tell a correct derivation from a wrong one, so in this arm it costs information.
- **C (self-check)** caught the missed target in two drafts (005, 055) and an untraced figure in one (030); the planner revised and checked again. In 6 of 12 runs it proposed a draft it had not checked as is and was refused once (not counted as a refused write). It did not happen to write a reference-row mislabel.
- **D (sub-agent)** failed 2 drafts (030: the target called "a reference value, not a decision rule"; 055: the missed target left unsaid); both were rewritten and passed. It passed two final texts the auditor failed (005, 018).

Reading, with one repetition of 12 cases (no difference between B, C, and D is beyond noise):

- **Any check removes the missed target left unsaid** (2 in A, none in B, C, D).
- **The most frequent remaining defect is a gap in the data the planner gets**, not in its checking: reference rows without an expected total cost. It shows in three arms, and neither the sub-agent nor code checks catch it reliably. A deterministic fix (the case server gives every reference row its expected total cost, as for the proposals) removes it at the source.
- **The self-check is the most expensive form here, against the guidance's expectation**: $0.098 per case against $0.072 for the sub-agent and $0.060 without a check, and 4.2 turns against 2.4. The planner runs on the strong tier with the whole case in its context, so each extra planner turn costs more than one verifier call on a compact packet.
- **The deterministic check is free but blunt**: 7 of its 8 refusals were false alarms on correct derived figures.

Not decided by this pass. Next: fix the reference rows, add the facts line and planner v6 (change card `change-20261030-planner-recommendation-facts`, approved), then compare the forms again on that base with three repetitions.
