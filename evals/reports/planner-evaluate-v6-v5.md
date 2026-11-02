# Planner prompt versions v6 against v5

Change card [`change-20261030-planner-recommendation-facts`](../failures/change-20261030-planner-recommendation-facts.md). The planner-evaluate cases and scripted supplier answers (the sampling of `verifier-mutations-v1`); each version evaluates every case 3 times, each run from its own copy of the same store state. Every final recommendation is audited by the verifier's checks (`claude-sonnet-5-5`), next to the deterministic findings (a missed service target or mandate field left unsaid; figures found in no evaluation).

| Arm | Runs | Written | Rule order | Target unsaid | Fields unsaid | Untraced figures | Auditor fail | Checks / fails | Refused writes | Mean cost | Mean turns | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A none, v5 | 36 | 36 | 36/36 | 3 | 0 | 19 | 5/36 | 0 / 0 | 0 | $0.048 | 2.3 | 14s |
| A none, v6 | 36 | 36 | 36/36 | 0 | 0 | 14 | 5/36 | 0 / 0 | 0 | $0.045 | 2.0 | 15s |

Cost is the planner run with its check (arm D includes the verifier); the audit is not in it. Spent in all, audit included: $4.50.

## Per case

Auditor passes over runs, then the deterministic findings counted over runs.

| Case | Mix | A none, v5 | A none, v6 |
|---|---|---|---|
| C-US10-40000005-SH-20261214 | all_inside | audit pass 3/3; untraced 3 | audit pass 1/3; untraced 2 |
| C-US10-40000014-WA-20261221 | outside_only | audit pass 1/3; target unsaid 2; untraced 1 | audit pass 2/3; untraced 2 |
| C-US10-40000018-SH-20261023 | all_inside | audit pass 3/3; untraced 3 | audit pass 3/3; untraced 3 |
| C-US10-40000030-SH-20261110 | outside_only | audit pass 2/3; untraced 3 | audit pass 3/3 |
| C-US10-40000047-SH-20261026 | all_inside | audit pass 3/3 | audit pass 3/3; untraced 1 |
| C-US10-40000048-WA-20261116 | with_decline | audit pass 3/3 | audit pass 1/3; untraced 2 |
| C-US10-40000054-SH-20261028 | cheaper_late_split | audit pass 3/3; untraced 3 | audit pass 3/3; untraced 1 |
| C-US10-40000055-WA-20261214 | outside_only | audit pass 1/3; target unsaid 1; untraced 1 | audit pass 2/3 |
| C-US10-40000475-SH-20261029 | with_decline | audit pass 3/3 | audit pass 3/3 |
| C-US10-40000548-SH-20261029 | cheaper_late_split | audit pass 3/3 | audit pass 2/3 |
| C-US10-40001231-SH-20261028 | with_decline | audit pass 3/3; untraced 3 | audit pass 3/3; untraced 2 |
| C-US10-40001379-SH-20261029 | cheaper_late_split | audit pass 3/3; untraced 2 | audit pass 3/3; untraced 1 |

## Auditor fails and notes (for a person to read)

- **C-US10-40000005-SH-20261214, A none, v6, run 1**:
  - Rationale: Recommend P-2-7-C-US10-40000005-SH-20261214 (E-51ddf1ec62ea), first by expected total cost. Both proposals are inside the mandate (no fields missed), cover the gap, and have 0 days below zero.
P-2 (E-51ddf1ec62ea): coverage 2026-12-09, 30 days below safety, stock-out risk 11.04%, total cost 237.47 USD, expected total cost 1147.97 USD.
P-1 (E-245587541ed8): coverage 2026-12-07, 28 days below safety, stock-out risk 10.44%, total cost 750.17 USD, expected total cost 1586.67 USD.
Trade-off: P-2 costs 512.70 USD less (as total cost) and 438.70 USD less in expected total cost, but it covers 2 days later, has 2 more days below safety, and slightly higher stock-out risk (11.04% vs 10.44%). P-1 is better on those three figures. P-2 is better on cost.
Service target: P-2 does NOT meet its service target (meets_service_target false); neither does P-1.
Reference only (not offered by the supplier): the engine's new_order (E-94204c79be6b) would cover 2026-11-17, 8 days below safety, risk 7.10%, total 134.93 USD, expected total 535.43 USD. It assumes the planned 57-day lead time at standard price; the supplier's actual quotes arrive later and at a higher price. Purchasing may want to ask the supplier whether an earlier arrival is possible. Expedited new order (E-e01ea7ece36f): coverage 2026-09-26, risk 5.37%, but expected total 3836.21 USD. Doing nothing (E-20ca3d23b0b8): 8 days below zero, risk 13.30%, expected total 1894.43 USD. Offers are valid until 2026-11-08.
  - The engine's new_order "assumes the planned 57-day lead time at standard price".: 57 is untraced. No evaluation or case fact states a lead time. It is not a difference or sum of any evaluated dates. For example, the reference coverage date is 2026-11-17, and the quotes ship on 2026-12-06 and arrive on 2026-12-07 and 2026-12-09. The figure cannot be verified, so it counts as wrong.
- **C-US10-40000014-WA-20261221, A none, v5, run 1**:
  - Rationale: Recommend P-2 (partial, 2 pcs: 1 on 2026-12-16, 1 on 2026-12-28, no surcharge, no freight). It has the lowest expected total cost: USD 1,017.46 (cost 135.36 + expected line-down 882.10), against USD 1,822.30 for P-1 (cost 1,082.60 + expected line-down 739.70). It misses only the arrival field of the mandate (second piece arrives 2026-12-28, after the 2026-12-21 latest arrival). For this C part the engine prices that lateness by risk (E-1ccc7665c572). Trade-off: P-2 covers only on 2026-12-28. It has 0 days below zero, 1 day below safety stock, and a stock-out risk of 29.79%, the same as doing nothing (E-049210d0ca70). P-1 (E-8ec2c3b232ef) covers on 2026-12-06 with 0 days below zero and 0 below safety, and its risk is lower at 22.12%. But P-1 misses the freight (USD 120) and surcharge (8%) fields, and its expected total cost is USD 804.84 higher. For reference only (not ranked): the engine's new order (E-fd6f9aaef488) would cover on 2026-10-25 with 0 days below safety and 22.12% risk, at an expected total cost of USD 1,079.78. That is USD 62.32 more than P-2. Purchasing may weigh whether the lower risk is worth that cost. Both supplier offers are valid until 2026-11-08. The supplier message contained no requests.
  - The rationale gives P-2's 29.79% stock-out risk and says P-1's risk is "lower at 22.12%". It never says whether either option meets the service target.: The service target is 0.9. E-1ccc7665c572 (P-2) has meets_service_target=false. E-8ec2c3b232ef (P-1) also has meets_service_target=false. The recommended option misses the service target, and the rationale leaves that out.
- **C-US10-40000014-WA-20261221, A none, v5, run 2**:
  - Rationale: Recommend P-2 (partial, 2 pcs: 1 arrives 2026-12-16, 1 arrives 2026-12-28). It has the lowest expected total cost: 1017.46 USD (total cost 135.36 + expected line-down 882.10). Evaluation E-1ccc7665c572. It misses only the arrival field (coverage date 2026-12-28, after the 2026-12-21 need date). Because this is a C part, it is priced by risk. Trade-off: 0 days below zero, but 1 day below safety. It does not cover the gap. Stock-out risk is 29.79% (expected 4.41 days below zero), the same as doing nothing. P-1 (new order, arrives 2026-12-06), evaluation E-8ec2c3b232ef, covers on 2026-12-06 with 0 days below zero and 0 below safety. Its stock-out risk is lower at 22.12% (3.70 expected days below zero). But it misses the freight field (120 USD) and the surcharge field (8%). Its total cost is 1082.60 and its expected total cost is 1822.30. For reference: the engine's new order (E-fd6f9aaef488) covers on 2026-10-25 with an expected total cost of 1079.78. That is higher than P-2's, so no engine option is recommended. Doing nothing (E-049210d0ca70) has an expected total cost of 1238.62. Both offers are valid until 2026-11-08. The supplier message held no requests.
  - The rationale gives P-2's stock-out risk (29.79%) but never says that P-2 misses the service target. It presents P-1's 22.12% as only "lower".: E-1ccc7665c572 has meets_service_target=false, and the case's service_target is 0.9. P-1 (E-8ec2c3b232ef) also has meets_service_target=false. The rationale leaves out this missed service target for the recommended option.
  - P-2's stock-out risk and expected days below zero are "the same as doing nothing".: The risk is the same, 29.79%. Expected days below zero are 4.41 for P-2 and 4.42 for no_action, so they are close but not identical. This is minor.
- **C-US10-40000014-WA-20261221, A none, v6, run 2**:
  - Rationale: Recommend P-2-7 (partial, 1 piece on 2026-12-16 and 1 on 2026-12-28; E-1ccc7665c572), first in purchasing's order. It has the lowest expected total cost: USD 1,017.46 (total cost 135.36 + expected line-down 882.10). P-1-7 (new order, arrival 2026-12-06; E-8ec2c3b232ef) costs USD 1,822.30 expected total (total cost 1,082.60 + expected line-down 739.70).

Trade-off, figure by figure:
- Coverage: P-1-7 covers on 2026-12-06; P-2-7 on 2026-12-28, after the need date 2026-12-21 (the engine notes this receipt is after the projection horizon). P-1-7 is better here.
- Days below zero: 0 for both.
- Days below safety: P-2-7 has 1, P-1-7 has 0. P-1-7 is better here.
- Stock-out risk to 2026-12-21: P-2-7 29.79% (expected 4.41 days below zero), P-1-7 22.12% (3.70 days). P-1-7 is better here.
- Mandate: P-2-7 misses only arrival (C part, priced by risk). P-1-7 misses freight (USD 120) and surcharge (8%, USD 474.72).
- Cost: P-2-7 total 135.36 vs P-1-7 1,082.60; expected total 1,017.46 vs 1,822.30. P-2-7 is better here.
So P-2-7 is cheaper but later and riskier; P-1-7 serves better but costs USD 804.84 more in expected total.

Service target: P-2-7 does NOT meet its service target (meets_service_target false). P-1-7 does not meet it either.

For reference, not ranked: the engine's new_order (E-fd6f9aaef488) covers on 2026-10-25, 0 days below safety, risk 22.12%, total cost 454.88, expected total 1,079.78. That is higher than P-2-7's 1,017.46 in expected total, but it covers earlier and has lower risk. Doing nothing (E-049210d0ca70): risk 29.79%, expected total 1,238.62. P-2-7 lowers the risk only slightly compared with doing nothing. Purchasing may want to weigh this.

Both supplier offers are valid until 2026-11-08. The supplier message held no requests.
  - P-2-7 lowers the risk only slightly compared with doing nothing.: P-2-7's stock-out risk is 29.79%. Doing nothing is also 29.79%, so the risk is not lowered. Only the expected days below zero differ, 4.41 against 4.42. Days below safety are also the same, 1 for each.
- **C-US10-40000030-SH-20261110, A none, v5, run 2**:
  - Rationale: Recommend P-2 (partial, 7 pcs: 3 on 2026-11-05, 4 on 2026-11-17), ranked first by expected total cost (E-4441530787fc). It covers on 2026-11-17 with 0 days below zero and 31 days below safety. Total cost -188.33 USD (price difference -270.97, no surcharge, no freight). Stock-out risk 13.04%, expected line-down 160.50 USD, expected total cost -27.83 USD. It misses one mandate field: arrival (the second delivery comes after 2026-11-10). It also does not meet the C-part service target of 10% (a reference value, not yet a decision rule). P-2 (E-fa5fdd6671b0) is the trade-off: it covers earlier, on 2026-10-26, with 0 days below zero, 14 days below safety and 6.63% risk, and it meets the service target. But it misses freight (120 USD) and surcharge (8%, 437.89 USD), with total cost 709.98 USD and expected total cost 764.58 USD, about 792 USD more than P-2 for about 6.4 points less risk. Correction: the option described in the last three sentences is P-1, not P-2. For reference only, not offered by the supplier: the engine's new_order (E-350990e0b125) covers 2026-10-19, risk 0.76%, expected total cost 147.21 USD; doing nothing (E-ca86c4a8cae6) has 42 days below zero and expected total cost 2,756.84 USD. Both offers are valid until 2026-11-08. If purchasing wants the service target met, choose P-1.
  - "P-2 (E-fa5fdd6671b0) is the trade-off: it covers earlier, on 2026-10-26... meets the service target. But it misses freight... about 792 USD more than P-2": E-fa5fdd6671b0 is P-1, not P-2. P-2 is E-4441530787fc: it covers 2026-11-17, misses arrival only, and does not meet the service target. The rationale says P-2 for both options. The added correction names "the last three sentences", but the third-last sentence (service target not met) is correctly about P-2. The "more than P-2" comparison is not fixed.
  - "does not meet the C-part service target of 10%": The case's service_target is 0.9, a service level. 10% is not a stated target, and the 10% figure was untraced. It can only be read as 1 − 0.9, the implied maximum stock-out risk. Calling it a "service target of 10%" misstates the target.
- **C-US10-40000048-WA-20261116, A none, v6, run 1**:
  - Rationale: Recommend P-3-7 (E-12104e0f5f62), ranked first. It is inside the mandate (no fields missed): 6 pcs, arrival 2026-11-09, no surcharge, no freight. Figures: coverage 2026-11-09, 0 days below zero, 0 days below safety, stock-out risk 15.77%, total cost 2626.55 USD, expected total cost 9505.05 USD. It does not meet the service target (meets_service_target false).

P-2-7 (E-c7d985e20921): coverage 2026-11-01 (earlier), 0 days below zero, 0 below safety, stock-out risk 13.10% (lower), expected days below zero 6.21 vs 6.88 (lower). But it misses the mandate on freight and surcharge, total cost 3910.91 USD (higher) and expected total cost 10118.41 USD (higher). It does not meet the service target either. So P-2 is better on date and risk, P-3 on cost, expected total cost and mandate fit.

P-1-7 is a decline ("Cannot supply in this period") and has no evaluation, so it is not ranked.

Engine reference rows, for information (not recommended): new_order E-61855b31ab37 shows coverage 2026-10-22, risk 10.44%, total 582.05 USD, expected total 6337.55 USD; but it assumes the planned price and 31-day lead time, which the supplier's quotes do not confirm. expedited_new_order E-f0fde12ef966: coverage 2026-09-25, risk 9.34%, total 9901.88 USD, expected total 15562.88 USD. No action E-ed87ac29ebb2: risk 21.98%, 36 days below safety, expected total 17746.52 USD. No option meets the service target.

Note: P-3 and P-2 are both valid until 2026-11-08.
  - The reference new_order row "assumes the planned price and 31-day lead time, which the supplier's quotes do not confirm.": The untraced figure "31" appears in no evaluation. It is not a difference, sum, or rounding of any evaluated figure. The case facts give no order date or lead time. The reference row only shows coverage 2026-10-22.
- **C-US10-40000048-WA-20261116, A none, v6, run 2**:
  - Rationale: Purchasing's order (expected cost rule): P-3 first, then P-2. P-1 is a decline ("Cannot supply in this period.") and is not ranked. I recommend the engine's new_order (E-61855b31ab37) over the first proposal, because its evaluated figures are better or equal on every figure.

P-3-7 (E-12104e0f5f62): covers 2026-11-09, 0 days below zero, 0 below safety, stock-out risk 15.77%, misses no mandate field, total cost 2626.55 USD (price difference 2174.28), expected total cost 9505.05 USD. Does not meet the service target.

P-2-7 (E-c7d985e20921): covers 2026-11-01, 8 days before P-3; 0 days below zero, 0 below safety; risk 13.10%, lower than P-3. But it misses the freight and surcharge mandate fields. Total cost 3910.91 USD (surcharge 2928.02, freight 120) and expected total cost 10118.41 USD are both higher than P-3. Does not meet the service target.

Engine new_order (E-61855b31ab37): covers 2026-10-22, before both proposals; 0 days below zero, 0 below safety; risk 10.44%, the lowest of the three; total cost 582.05 USD and expected total cost 6337.55 USD, the lowest of the three. It does NOT meet the service target (meets_service_target false). No option evaluated does, not even the expedited new order (E-f0fde12ef966, risk 9.34%, expected total cost 15562.88 USD). Doing nothing (E-ed87ac29ebb2) leaves 36 days below safety and a 21.98% risk.

Caveat for purchasing: new_order means a normal order at the planned 31-day delivery time and the standard price. The supplier has not confirmed that date or price. Its own quotes are later and at a higher price. If the supplier cannot deliver on those terms, take P-3, which is inside the mandate. Note that P-3 and P-2 are both valid only until 2026-11-08.
  - The engine new_order is recommended over P-3 because its evaluated figures are better or equal on every figure.: Holding cost for new_order (E-61855b31ab37) is 507.05 USD. P-3 (E-12104e0f5f62) is 377.27 USD and P-2 is 425.49 USD. So new_order is worse on holding cost.
  - new_order means a normal order at the planned 31-day delivery time.: The untraced figure 31 is not in any evaluation or in the case facts. The evaluation gives only a coverage date of 2026-10-22, with no order date or lead time. It cannot be derived or confirmed.
- **C-US10-40000055-WA-20261214, A none, v5, run 1**:
  - Rationale: I recommend P-1 (rank 2), not P-2 (rank 1). P-2 is first only because it misses fewer mandate fields. The figures favor P-1.

P-1 (E-02d1b662d842): new order of 3, coverage 2026-11-29. 0 days below zero, 0 below safety. Stock-out risk 16.44%. Total cost 3,708.89 USD. Expected total cost 13,671.39 USD. It misses the freight and surcharge fields: an 8% surcharge (2,531.18 USD) and 120 USD freight, where the mandate allows 0.

P-2 (E-b15d12efdd0a): partial delivery, 1 unit on 2026-12-09 and 2 units on 2026-12-21. Coverage 2026-12-21, which is after the needed date of 2026-12-14. 0 days below zero, 0 below safety. Stock-out risk 31.25%. Total cost -867.66 USD (lower unit price). Expected total cost 25,799.84 USD. It misses the arrival field.

Trade-off: P-1 costs 4,576.55 USD more in direct cost. But this is an A part. P-1 covers 22 days earlier and has about half the stock-out risk. Its expected total cost is 12,128.45 USD lower. Doing nothing (E-6977dea16239) has 45.20% risk and an expected total cost of 38,293.67 USD.

For reference only: the engine's standard new order (E-edf78f73fc79) shows coverage 2026-11-18, 12.64% risk, and an expected total cost of 7,488.49 USD. The supplier did not offer that lead time. Purchasing may want to ask whether the 8% surcharge and the freight can be waived. Both offers are valid until 2026-11-08. The supplier message held no instructions.
  - The rationale describes P-1 (the recommended option) with its 16.44% stock-out risk and favors it for an A part. It never says that P-1 misses the service target.: E-02d1b662d842 has meets_service_target=false. The part's service_target is 0.99, and a 16.44% stock-out risk is far from that. This is a material fact about the recommended option, and it is left out.
- **C-US10-40000055-WA-20261214, A none, v5, run 2**:
  - Rationale: The proposals are listed in purchasing's order. P-2 is first only because it misses one mandate field (arrival) and P-1 misses two (freight, surcharge). On the evaluated figures, P-2 is the weakest choice. It covers only on 2026-12-21, after the 2026-12-14 needed date, because 2 of the 3 units arrive on 2026-12-21. Its stock-out risk is 31.25% (5.33 expected days below zero) and its expected total cost is 25,799.84 USD, even though its own cost is -867.66 USD.

P-1 (E-02d1b662d842) covers on 2026-11-29. Its risk is 16.44% and its expected total cost is 13,671.39 USD, with cost 3,708.89 USD (surcharge 2,531.18, freight 120).

The engine's new order (E-edf78f73fc79) covers on 2026-11-18. Its risk is 12.64% (1.33 expected days below zero) and its expected total cost is 7,488.49 USD, with cost 835.99 USD and no surcharge or freight, so it fits the mandate. I recommend it: it covers earliest, has the lowest risk and costs least.

All three options show 0 days below zero and 0 days below safety in the deterministic projection, and none meets the 99% service target. Caveat: the supplier did not quote a standard order arriving 2026-11-18. Their only on-time offer was P-1, with an 8% surcharge. Purchasing should confirm the supplier can meet the planned delivery time. If not, P-1 is the better fallback over P-2. Both supplier offers are valid until 2026-11-08. The supplier message held no requests or instructions.
  - The engine's new order "covers earliest, has the lowest risk and costs least." This comes right after its cost of 835.99 USD.: On cost, P-2 (E-b15d12efdd0a) is lower at -867.66 USD, and P-1 is 3,708.89 USD. The new order is lowest only on expected total cost (7,488.49 USD). The sentence does not say which measure it means.
  - The new order "fits the mandate." The rationale recommends it as if purchasing could buy it.: The new order is a reference row, not a supplier proposal. The supplier quoted only P-1 and P-2, and both miss the mandate. No quote, price, validity or `within_mandate` result exists for a new order arriving 2026-11-18. The 0 surcharge and 0 freight are the engine's assumptions. The caveat later in the text only partly offsets this.
- **C-US10-40000055-WA-20261214, A none, v6, run 2**:
  - Rationale: Recommend the engine's new_order (E-edf78f73fc79) over both supplier proposals. Part is criticality A, so the late proposal is not priced by risk; the ranking puts P-2 first only because it misses fewer mandate fields.

Figures:
- P-2-7 (partial, E-b15d12efdd0a): coverage 2026-12-21, after the needed date 2026-12-14 (misses arrival). 0 days below zero, 0 below safety. Stock-out risk 31.25%. Total cost -867.66 USD; expected total cost 25,799.84 USD. Does not meet the service target.
- P-1-7 (new_order, E-02d1b662d842): coverage 2026-11-29. 0 days below zero, 0 below safety. Stock-out risk 16.44%. Misses freight (120 USD) and surcharge (8%). Total cost 3,708.89 USD; expected total cost 13,671.39 USD. Does not meet the service target.
- Engine new_order (E-edf78f73fc79): coverage 2026-11-18, the earliest of the three. 0 days below zero, 0 below safety. Stock-out risk 12.64%, the lowest of the three. No surcharge, no freight. Total cost 835.99 USD; expected total cost 7,488.49 USD, the lowest of the three.

Trade-off: the engine new_order has the earliest coverage, the lowest risk and the lowest expected total cost. Its total cost (835.99) is higher than P-2's (-867.66), because P-2's price is lower, but P-2 arrives late and has more than double the risk. P-1 costs more than the engine new_order on every cost figure and covers later.

Service target: the recommended new_order does NOT meet its service target (99%); risk stays at 12.64%. None of the evaluated options meets it. For reference, the engine's expedited_new_order (E-08dc1bae58f7) covers 2026-09-24 with the same 12.64% risk but costs 8,750.37 USD (expected 14,552.87 USD), so it does not lower the risk either.

Note: both supplier offers are valid until 2026-11-08. The supplier message held no requests to act on.
  - P-1 costs more than the engine new_order on every cost figure and covers later.: P-1's holding cost is 669.44 against 760.99 for the engine new_order, so P-1 is lower there. Fixed cost is 75 for both, so it is equal. P-1 is higher only on price difference, surcharge, freight, total cost and expected total cost.

## Review (2026-11-01)

Every auditor fail and uncertain verdict was read by hand against the evaluations and the case.

| | v5 (36 runs) | v6 (36 runs) |
|---|---|---|
| Missed service target left unsaid | 3 (014 runs 1, 2; 055 run 1) | 0 |
| One option's evaluation under the other's name | 1 (030 run 2) | 0 |
| A comparison overstated or wrong | 1 (055 run 2: "costs least", and an engine option said to "fit the mandate") | 3 (014 run 2: "lowers the risk slightly" against doing nothing at the same 29.79 %; 048 run 2: "better or equal on every figure" with a higher holding cost; 055 run 2: "more on every cost figure" with a lower holding cost) |
| Runs with a real defect | 5 | 3 |
| Auditor false alarms | 0 | 2 fails and 2 uncertain (below) |

- **False alarms come from the audit packet, not the planner.** v6 cited the engine's planned lead time (57 days in 005, 31 days in 048), which the case holds as `lever_detail.lead_days`; the packet (`caseFacts` in `harness/src/agent/verifier.ts`) leaves `lever_detail` out, so the auditor could not trace it. The same holds for a supplier message quoted in 548 (the packet carries the proposals, not the message). The verifier of arm D reads the same packet, so the gap matters in production too.
- **Rule order:** 36/36 in both. **Cost:** $0.045 per case against $0.048; turns 2.0 against 2.3.
- **Reading:** the first two sentences do what they were for: no missed target left unsaid (3 to 0) and no mix-up of labels (1 to 0). The third ("never call one better on every figure when any of its figures is worse") did not stop overstated comparisons: two of v6's three are the very "every figure" claim, worded as "better or equal" or "on every cost figure". At this size (one discordant direction each way) none of the differences is significant; together, real defects fall from 5 to 3 runs in 36, with no regression.
