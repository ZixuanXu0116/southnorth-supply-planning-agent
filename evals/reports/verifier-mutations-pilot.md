# Verifier evaluation: mutation testing

> Pilot (4 cases, 2026-10-30), run before the verifier's packet carried the suppliers' proposals as quoted and the part's service target. Two of the three failed originals were false alarms from that gap (a quoted validity date, a quoted surcharge, and a service level of 97% that the packet did not hold). After the packet change the verifier was rerun on the same 20 recommendations: mutations 16/16 caught; originals 2 pass and 2 fail, both real defects of the planner's text (a missed service target left unsaid; a reference row's total cost called its expected total cost). USD 0.56 for the rerun.

Cases and supplier answers of the planner-evaluate suite; one real recommendation per case from the planner (prompt v5, verifier off), then the verifier on it and on mutated copies with one false claim each (`evals/src/snm_evals/verifier_mutations.py`). A mutation is caught when the verdict is `fail`; a `fail` on an original is listed for review (a false alarm or a real defect of the planner's text).

| Variant | Runs | fail | uncertain | pass | no verdict |
|---|---|---|---|---|---|
| original | 4 | 3 | 0 | 1 | 0 |
| figure | 4 | 4 | 0 | 0 | 0 |
| mandate_claim | 3 | 3 | 0 | 0 | 0 |
| cheapest_claim | 4 | 4 | 0 | 0 | 0 |
| target_claim | 2 | 2 | 0 | 0 | 0 |
| omission | 3 | 3 | 0 | 0 | 0 |

Mutations caught: 16/16. Originals failed: 3/4. Spent: USD 0.79 (planner and verifier).

## Originals the verifier failed (to review)

- C-US10-40000054-SH-20261028: Doing nothing (E-dadce40ee645) gives ... expected total cost 11,003.02 USD.: In E-dadce40ee645, 11,003.02 is total_cost, not expected total cost. That reference row has no expected_total_cost. It does show expected_line_down_cost 2,349.5. In the ranked rows, expected total cost differs from total cost whenever expected line-down cost is above zero, so calling 11,003.02 the expected total cost is wrong.
- C-US10-40000005-SH-20261214: "Neither meets the 97% service target.": The evaluations show meets_service_target=false for both offers, but the 97% target appears in no evaluation or case fact. It is untraced and cannot be derived from evaluated figures.; "Both offers are valid until 2026-11-06.": No evaluation or case fact gives a validity date. 2026-11-06 is untraced and is not a difference, sum, or rounding of evaluated figures.
- C-US10-40000014-WA-20261221: The rationale for P-2 gives its stock-out risk as 29.79% but never says it misses the service target.: E-1ccc7665c572 has meets_service_target: false. This is a material fact about the recommended option, and the rationale leaves it out.; P-1 has an "8% surcharge 474.72 USD".: "8%" does not appear in any evaluation, and it is not a difference, sum, or rounding of evaluated figures. It can only be reached as a ratio: 474.72 / (5875.22 + 58.76) ≈ 8%.
