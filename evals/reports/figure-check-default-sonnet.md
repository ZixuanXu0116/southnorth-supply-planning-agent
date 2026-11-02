> Batch `figure-check-default-sonnet` (2026-11-02): the same suite, planner (Sonnet 5.5, medium) and auditor as `models-eval-sonnet-medium` in [`models-planner-2026-11-02`](models-planner-2026-11-02.md), with the planner's figure check on by default, so arm "A none" here is the new default (no verifier, the figure check, two attempts). Change card: [`change-20261102-planner-write-checks`](../failures/change-20261102-planner-write-checks.md).

# Planner check forms: none, deterministic, self-check, sub-agent

Change card [`change-20261101-verifier-form`](../failures/change-20261101-verifier-form.md). The planner-evaluate cases and scripted supplier answers (the sampling of `verifier-mutations-v1`); the planner (prompt v6) evaluates each case once per arm from the same store state, every form on every recommendation (`--verify always`). The final recommendation of each arm is audited by the verifier's checks on another model tier than arm D's verifier (`claude-sonnet-5-5`), next to the deterministic findings (a missed service target or mandate field left unsaid; figures found in no evaluation).

| Arm | Runs | Written | Rule order | Target unsaid | Fields unsaid | Untraced figures | Auditor fail | Checks / fails | Refused writes | Mean cost | Mean turns | Mean latency |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A none | 24 | 24 | 24/24 | 0 | 0 | 0 | 2/24 | 0 / 0 | 1 | $0.028 | 2.0 | 10s |

Cost is the planner run with its check (arm D includes the verifier); the audit is not in it. Spent in all, audit included: $1.02.

## Per case

Auditor passes over runs, then the deterministic findings counted over runs.

| Case | Mix | A none |
|---|---|---|
| C-US10-40000005-SH-20261214 | all_inside | audit pass 2/2 |
| C-US10-40000014-WA-20261221 | outside_only | audit pass 2/2 |
| C-US10-40000018-SH-20261023 | all_inside | audit pass 2/2 |
| C-US10-40000030-SH-20261110 | outside_only | audit pass 2/2 |
| C-US10-40000047-SH-20261026 | all_inside | audit pass 2/2 |
| C-US10-40000048-WA-20261116 | with_decline | audit pass 2/2 |
| C-US10-40000054-SH-20261028 | cheaper_late_split | audit pass 2/2 |
| C-US10-40000055-WA-20261214 | outside_only | audit pass 2/2 |
| C-US10-40000475-SH-20261029 | with_decline | audit pass 1/2 |
| C-US10-40000548-SH-20261029 | cheaper_late_split | audit pass 0/2 |
| C-US10-40001231-SH-20261028 | with_decline | audit pass 2/2 |
| C-US10-40001379-SH-20261029 | cheaper_late_split | audit pass 2/2 |

## Auditor fails and notes (for a person to read)

- **C-US10-40000548-SH-20261029, A none, run 1**:
  - Rationale: Recommend P-3-7 (evaluation E-2f2fc3a19741). It is the only proposal that misses no mandate field. Coverage date 2026-10-24, 0 days below zero, 14 days below safety, stock-out risk 2.10%. It meets the service target. Total cost 32,263.38 USD; expected total cost 32,281.88 USD.

P-2-7 (E-3de8d8a613ce): the cost is lower (total -12,729.49 USD; expected -12,710.99 USD) and the surcharge is 0. But it misses the arrival field: half arrives 2026-10-24 and half 2026-11-05, so coverage is 2026-11-05. Days below zero (0), days below safety (14) and risk (2.10%) are the same as P-3-7. It also meets the service target. Purchasing could choose it if the later arrival is acceptable.

P-1-7 (E-e4a4bc312f28): the earliest coverage, 2026-10-14. It has 0 days below zero, 10 days below safety and 0.00% risk, and it meets the target. But it misses the freight and surcharge fields, and it costs more: total 83,841.68 USD; expected total 83,841.68 USD. That is about 51,578 USD more than P-3-7 in expected total cost for 4 fewer days below safety and 2.10 points less risk.

All three offers are valid until 2026-09-28.
  - P-1-7 costs about 51,578 USD more than P-3-7 in expected total cost.: Expected total cost is 83,841.68 for P-1-7 and 32,281.88 for P-3-7, a difference of 51,559.80. The figure 51,578.30 is the difference in total_cost (83,841.68 − 32,263.38), not in expected total cost.
- **C-US10-40000548-SH-20261029, A none, run 2**:
  - Rationale: Recommend P-3-7 (evaluation E-2f2fc3a19741). It is the only proposal inside the mandate. It arrives 2026-10-24, before the 2026-10-29 limit. Days below zero: 0. Days below safety stock: 14. Stock-out risk: 2.10%. It meets the service target. Total cost is 32,263.38 USD and expected total cost is 32,281.88 USD (surcharge 26,463.17, freight 105). P-2-7 (E-3de8d8a613ce) is much cheaper: total cost -12,729.49 USD, expected total cost -12,710.99 USD. Its coverage date is 2026-11-05, and it misses the arrival field because half of the quantity arrives on 2026-11-05. Its days below zero (0), days below safety (14) and stock-out risk (2.10%) match P-3-7, and it also meets the target. Purchasing may weigh that saving against the late arrival. P-1-7 (E-e4a4bc312f28) is the fastest: coverage 2026-10-14, days below safety 10, stock-out risk 0.00%, and it meets the target. It misses the freight and surcharge fields (26% surcharge, 330 freight). Its total cost is 83,841.68 USD, which equals its expected total cost. That is 51,578.30 USD more than P-3-7 in expected total cost. It is better on days below safety and risk but worse on cost. Offers are valid until 2026-09-28.
  - P-1-7 is 51,578.30 USD more than P-3-7 in expected total cost.: Expected total costs are 83,841.68 for P-1-7 and 32,281.88 for P-3-7, a difference of 51,559.80. The figure 51,578.30 is the difference in total cost (83,841.68 − 32,263.38), not expected total cost.
