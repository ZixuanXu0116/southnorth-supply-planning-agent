# Planner and extractor models: Sonnet 5.5 against Opus 5.5 (2026-11-02)

The owner's request (2026-11-02): the workflow on Claude Sonnet 5.5 at effort medium or high by the task's complexity instead of Claude Opus 5.5, and the supplier-document extractor on Claude Haiku 5.5 at effort medium. Same frozen data as the earlier planner reports (`out/planning/2026-09-21`, the case server's day pinned to it), planner prompt v6, no runtime check.

## Evaluate task (verifier-form suite, arm none)

12 cases in four proposal mixes, each evaluated twice from its own copy of the store; every final recommendation audited by Claude Sonnet 5.5 (the same auditor for every arm, as in `planner-evaluate-v6-v5`).

| Planner | Runs | Written | Rule order | Target unsaid | Fields unsaid | Untraced figures | Auditor fail | Mean cost | Mean latency |
|---|---|---|---|---|---|---|---|---|---|
| Opus 5.5, medium | 24 | 24 | 24/24 | 0 | 0 | 7 | 1/24 | $0.049 | 16 s |
| Sonnet 5.5, medium | 24 | 24 | 24/24 | 0 | 0 | 10 | 1/24 | $0.023 | 11 s |
| Sonnet 5.5, high | 24 | 24 | 24/24 | 1 | 0 | 13 | 1/24 | $0.029 | 14 s |

## Prepare task (planner suite)

| Planner | Cases passed | Cost per case |
|---|---|---|
| Opus 5.5, medium | 9/9 | $0.035 |
| Sonnet 5.5, medium | 9/9 | $0.014 |
| Sonnet 5.5, high | 9/9 | $0.012 |

## Extractor (supplier documents, 24 documents with known answers)

| Extractor | Fields correct | Left for purchasing | Dangerous | Cost per document | Seconds |
|---|---|---|---|---|---|
| Haiku 5.5, low | 296/300 | 4 | 0 | $0.0009 | 4.8 |
| Haiku 5.5, medium | 299/300 | 1 | 0 | $0.0010 | 7.1 |

## Reading

- Sonnet 5.5 at medium did both planner tasks as well as Opus 5.5 on every check that decides a case (one write, the server's order, the target and mandate said, the auditor's verdict), at less than half the cost (evaluate $0.023 against $0.049, prepare $0.014 against $0.035) and faster. It cites more figures that trace to no evaluation (10 against 7 in 24 runs): the auditor accepted them all but one, and the deterministic facts line under every recommendation is not affected.
- Effort high did not help on these tasks: on evaluate it cost more and left the service target unsaid once. Both tasks run at medium; the per-task setting (`SNM_EFFORT_MAIN_PREPARE`, `SNM_EFFORT_MAIN_EVALUATE`, `SNM_EFFORT_MAIN_HANDOFF`) is there for a task that measures better at another level.
- Haiku 5.5 at medium reads three more fields right than at low (the kind of an express offer) for a tenth of a cent more per document.
- Small samples (24 audited runs, 9 cases, 24 documents, one repetition each): the differences between the Sonnet arms are within noise.

Deployed 2026-11-02: planner and data agent on Sonnet 5.5 medium, verifier (off by default) on Sonnet 5.5 high, explorer Sonnet 5.5 low, extractor Haiku 5.5 medium. Batches `models-eval-*`, `models-prepare-*`, `documents-v1-haiku-medium`; about $4.10 in all, audits included.
