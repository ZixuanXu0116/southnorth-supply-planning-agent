# Planner agent evaluation: evaluate task

Failure card `evals/failures/roleplay-20261019-C-US10-40001795-SH-20261122.md`. Each case gets a request under the default mandate and a scripted supplier answer from one mix; every prompt version evaluates the same cases from the same store state. Passed = one recommendation event, no refused write, and the proposals submitted in the purchasing rule's order (inside the mandate first, then fewest mandate fields missed, then total cost).

| Version | Passed | Rule order | Recommended the rule's first | Cost | Cost per case |
|---|---|---|---|---|---|
| v3 | 8/12 | 8/12 | 8/12 | $0.85 | $0.071 |
| v4 | 12/12 | 12/12 | 11/12 | $0.68 | $0.056 |

| Case | Mix | v3: rule order / first | v4: rule order / first |
|---|---|---|---|
| C-US10-40000054-SH-20261028 | cheaper_late_split | yes / yes | yes / yes |
| C-US10-40000005-SH-20261214 | all_inside | yes / yes | yes / yes |
| C-US10-40000475-SH-20261029 | with_decline | no / yes | yes / yes |
| C-US10-40000014-WA-20261221 | outside_only | no / no | yes / no |
| C-US10-40000548-SH-20261029 | cheaper_late_split | yes / yes | yes / yes |
| C-US10-40000018-SH-20261023 | all_inside | yes / yes | yes / yes |
| C-US10-40001231-SH-20261028 | with_decline | no / yes | yes / yes |
| C-US10-40000030-SH-20261110 | outside_only | yes / no | yes / yes |
| C-US10-40001379-SH-20261029 | cheaper_late_split | yes / yes | yes / yes |
| C-US10-40000047-SH-20261026 | all_inside | yes / no | yes / yes |
| C-US10-40000048-WA-20261116 | with_decline | no / yes | yes / yes |
| C-US10-40000055-WA-20261214 | outside_only | yes / no | yes / yes |

## Three repetitions (screen plus two, `PROMPT_CHANGES.md` §5)

| Version | Rule order | Recommended the rule's first | Passed | Cost per case |
|---|---|---|---|---|
| v3 | 20/36 | 26/36 | 20/36 | $0.065 |
| v4 | 36/36 | 34/36 | 36/36 | $0.050 |

Paired on rule order: v4 right where v3 was wrong in 16 of 36 case runs, the reverse in 0 (exact McNemar p < 0.001 when the reverse is 0 and the first is at least 11).
v3's misses by mix: cheaper_late_split 0/9, all_inside 0/9, with_decline 9/9, outside_only 7/9.
Repetitions 2 and 3 fixed a defect of the scripted supplier found by v4 in repetition 1 (an offer whose arrival date preceded its ship date); both versions saw the same answers in every repetition.
Regression check: the prepare suite with v4 (`planner-prepare-v4`) passed 9/9 in two runs ($0.034 and $0.017 per case), as v3 did on generator 0.8 (`planner-prepare-v3-generator080`). v3's misses come from the mixes with a decline or without any proposal inside the mandate; the role-play mix (a cheaper late split and an expensive offer outside on two fields) was ordered by the rule in all 9 v3 runs here, so the role-play failure is intermittent rather than systematic, and v4 removes the variance. Spend of the measurement: about USD 4.6.
