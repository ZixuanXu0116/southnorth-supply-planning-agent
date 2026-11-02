# Verifier evaluation: mutation testing

> **Note on this batch.** The originals are not the suite's own planner runs: they are the final recommendations of `verifier-form-v2`'s no-check arm, run 1 (planner **v6**, 12 cases), mutated with the two comparison defects only (`snm-evals verifier-mutations --from-store ... --only overclaim wrong_difference`); the originals were not re-verified here (their audit is in `verifier-form-v2`). Two cases had no proposal the recommended option is worse than on any figure, so `overclaim` applies to 10.


Cases and supplier answers of the planner-evaluate suite; one real recommendation per case from the planner (prompt v5, verifier off), then the verifier on it and on mutated copies with one false claim each (`evals/src/snm_evals/verifier_mutations.py`). A mutation is caught when the verdict is `fail`; a `fail` on an original is listed for review (a false alarm or a real defect of the planner's text).

| Variant | Runs | fail | uncertain | pass | no verdict |
|---|---|---|---|---|---|
| overclaim | 10 | 10 | 0 | 0 | 0 |
| wrong_difference | 12 | 12 | 0 | 0 | 0 |

Mutations caught: 22/22. Originals failed: 0/0. Spent: USD 0.58 (planner and verifier).
