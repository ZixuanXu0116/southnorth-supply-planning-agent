# Verifier evaluation: mutation testing

Cases and supplier answers of the planner-evaluate suite; one real recommendation per case from the planner (prompt v5, verifier off), then the verifier on it and on mutated copies with one false claim each (`evals/src/snm_evals/verifier_mutations.py`). A mutation is caught when the verdict is `fail`; a `fail` on an original is listed for review (a false alarm or a real defect of the planner's text).

| Variant | Runs | fail | uncertain | pass | no verdict |
|---|---|---|---|---|---|
| original | 12 | 6 | 0 | 6 | 0 |
| figure | 12 | 12 | 0 | 0 | 0 |
| mandate_claim | 9 | 9 | 0 | 0 | 0 |
| cheapest_claim | 12 | 12 | 0 | 0 | 0 |
| target_claim | 6 | 6 | 0 | 0 | 0 |
| omission | 9 | 9 | 0 | 0 | 0 |

Mutations caught: 48/48. Originals failed: 6/12. Spent: USD 2.29 (planner and verifier).

## Originals the verifier failed (to review)

- C-US10-40000005-SH-20261214: The rationale gives P-2's stock-out risk as 11.04% but never says it misses the service target.: E-51ddf1ec62ea: meets_service_target=false. The case's service_target is 0.97, and an 11.04% stock-out risk is about 88.96% service. The rationale leaves out that the recommended option misses the service target, which is a material fact.
- C-US10-40000014-WA-20261221: Engine new order "does better than P-1 on every figure": E-fd6f9aaef488 vs E-8ec2c3b232ef: stock-out risk is the same (22.12% for both), min projected qty is the same (18), and the engine's holding cost is higher (379.88 vs 354.12).; The rationale does not say whether the recommended engine new order meets the service target: E-fd6f9aaef488 has meets_service_target=false. Its stock-out risk is 22.12%, so it does not reach the 0.9 service target. This is a material fact about the recommended option and it is left out.
- C-US10-40000030-SH-20261110: "P-2 (E-fa5fdd6671b0, new order, 7 pcs, arrival 2026-10-26) covers 2026-10-26 … total cost 709.98, expected total cost 764.58": E-fa5fdd6671b0 is P-1-7-C-US10-40000030-SH-20261110 (new_order, arrival 2026-10-26). P-2 is the recommended partial (E-4441530787fc). The rationale gives the second option the recommended option's label, so it describes P-2 two ways that contradict each other.; "the target is a reference value, not a decision rule" (about the 10% / 0.9 service target): The case facts give service_target 0.9 as the service level the part must reach. P-2's evaluation shows meets_service_target false (risk 13.04%). Calling the target only a reference value downplays the fact that the recommended option misses it.
- C-US10-40000047-SH-20261026: The engine's new_order (E-3c13f60b765f) is "better on every figure" than the proposals: Its holding cost is 2.91 USD, higher than P-2's 0.63 USD (E-9bf8ff26175f) and P-1's 0.79 USD (E-f2fdda285e0c). Days below zero (0) and fixed cost (75) are the same for all three. So it is not better on every figure. Its total cost of 77.91 is only 0.72 USD below P-2's 78.63.
- C-US10-40000048-WA-20261116: Recommends P-3 and gives its stock-out risk as 15.77%, but never says whether it meets the service target.: E-12104e0f5f62 shows meets_service_target: false. The service target is 0.97, and a 15.77% stock-out risk falls short of it. Leaving this out is a material omission about the recommended option.
- C-US10-40000055-WA-20261214: For P-1 the rationale gives a 16.44% stock-out risk and says it 'about halves the stock-out risk', but it never says whether P-1 meets the part's service target.: E-02d1b662d842 shows meets_service_target: false. The service target is 0.99 (at most 1% risk), and P-1's risk is 16.44%. The rationale leaves this out for the recommended option.

## Review of the failed originals (2026-10-30)

Each of the six was read against its evaluations; every one is a real defect of the planner's text, none a false alarm:

- **A missed service target left unsaid** (C-US10-40000005, -40000014, -40000048, -40000055): the recommended option's risk is above its target (for example 16.44% where the target allows 1%), and the rationale gives the risk without saying it misses. The same omission in four of twelve cases points at the planner's instructions, not at single runs.
- **An overclaim** (C-US10-40000014, -40000047): "better on every figure" for an engine option whose holding cost is higher, and whose risk is equal.
- **A label mix-up** (C-US10-40000030): one proposal's evaluation cited under the other proposal's name.

Cost: USD 1.54 for the 60 verifier checks (about USD 0.026 each) and USD 0.75 for the 12 planner runs.
