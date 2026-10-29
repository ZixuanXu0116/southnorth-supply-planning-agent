# Record 002 phase 4: risk-aware rules replayed against the strict rule

Profile `small` (generator 0.8), plant `US10`, engine 1.1.0, deterministic, no model calls. Every order case the engine opened on the 21 Mondays of the backtest's held-out half (2026-02-02 to 2026-06-22; the calibration curve was fitted on the first half) is decided ex ante by each policy and priced ex post on the demand that followed (`snm-data oracle --start 2026-02-02 --weeks 21 --horizon 91`): the lever's cost plus 5,000 / 1,000 / 200 USD per day the pooled stock (main warehouse and overflow hub) is below zero within the engine's 91-day horizon, with no other order placed. The hindsight oracle picks the cheapest option knowing that demand; regret is a policy's cost above it.

Policies (`snm_data.planning.oracle`):

- **Strict rule** (engine 1.1, today): the first lever in the fixed order that covers by the trigger date (expedites only against a stock-out).
- **Risk target**: the cheapest order lever whose calibrated stock-out risk (up to the later of the trigger date and its arrival) is within the service target (A 1 %, B 3 %, C 10 %); else the lowest-risk lever.
- **Expected cost**: the option, doing nothing included, with the lowest lever cost plus expected line-down (the case's 2,000 simulated paths over the 91 days, the model's mean; the calibration curve only changes the risk percentages), the rule (c) of the failure card.

## Engine levers only

2,908 case decisions (1,753 shortage, 1,155 warning), all with a qualified demand model.

| Policy | Cost per case (USD) | Regret per case (USD) | Line-down days A / B / C | Choices |
|---|---|---|---|---|
| Strict rule | 1,060 | 644 | 122 / 306 / 1,410 | new order 2,818, expedited new order 87, expedite 3 |
| Risk target | 2,217 | 1,801 | 120 / 306 / 1,410 | new order 2,150, expedited new order 747, expedite 11 |
| **Expected cost** | **498** | **81** | 122 / 306 / 1,429 | new order 2,547, do nothing 342, expedited new order 19 |
| Do nothing | 2,291 | 1,875 | 889 / 1,037 / 5,897 | |
| Hindsight oracle | 416 | 0 | | |

| Segment | Cases | Strict rule regret | Risk target regret | Expected cost regret | Do nothing regret |
|---|---|---|---|---|---|
| Shortage | 1,753 | 1,020 | 2,347 | 92 | 2,903 |
| Warning | 1,155 | 73 | 971 | 66 | 314 |
| Class A | 231 | 1,147 | 6,201 | 291 | 16,615 |
| Class B | 439 | 282 | 2,999 | 65 | 1,660 |
| Class C | 2,238 | 663 | 1,111 | 63 | 395 |

Reading:

- **The expected-cost rule meets record 002's acceptance bar on this data**: its regret per case is an eighth of the strict rule's (81 against 644 USD), with the same A- and B-class line-down days (122 and 306) and 19 more C-class days (1,429 against 1,410, +1.3 %). Its savings are the expedited new orders the strict rule places when a normal order cannot arrive by the trigger date (87 against 19; they cost 6,600 USD on average on the current run) and, on the realized demand, mostly bought no line-down day back; and 342 cases where waiting was the cheaper choice.
- **The risk-target rule is the worst ordering policy**: to bring a lumpy C part within 10 % it buys an expedited order in a quarter of the cases (747), at almost three times the strict rule's regret, and saves two A-class days. A probability threshold alone ignores what the safety costs; the cost of the line-down is the better judge, as answer 1 of the record put it.
- The line-down days that no ordering policy avoids (about 120 A-class days under every ordering policy) are stock-outs before any lever can arrive: the risk is there on the case's first day, and only an earlier case, not a better choice, reaches it.

## With a later, cheaper offer on every case (the Blue Fen question)

The same decisions with one more option each, like the role play's split: the engine's new order in two halves, the first on its date and the rest a week after the later of that date and the trigger date (outside the mandate on arrival), priced 3 % or 8 % of the case's value below the new order (`--late-split-discount 0.03` and `0.08`). The strict rule never takes it.

| Discount | Policy | Cost per case (USD) | Regret per case (USD) | Line-down days A / B / C | Late split taken |
|---|---|---|---|---|---|
| 3 % | Strict rule | 1,060 | 1,462 | 122 / 306 / 1,410 | 0 |
| 3 % | Risk target | 1,817 | 2,220 | 120 / 306 / 1,411 | 887 |
| 3 % | Expected cost | −277 | 125 | 122 / 308 / 1,476 | 1,584 |
| 3 % | Expected cost, late offers for C parts only | 4 | 406 | 122 / 306 / 1,476 | 1,234 |
| 8 % | Strict rule | 1,060 | 2,916 | 122 / 306 / 1,410 | 0 |
| 8 % | Risk target | 1,151 | 3,008 | 120 / 306 / 1,411 | 887 |
| 8 % | Expected cost | −1,720 | 136 | 122 / 308 / 1,532 | 1,971 |
| 8 % | Expected cost, late offers for C parts only | −870 | 986 | 122 / 306 / 1,532 | 1,541 |

(A negative cost is a saving: the discount exceeds the order cost.)

Reading:

- **Taking the later, cheaper offer when its expected line-down costs less than it saves pays off on the demand that followed**: at 3 % the expected-cost rule takes it in 54 % of the cases and ends 1,337 USD per case below the strict rule; at 8 % in 68 % and 2,780 USD below. The price is line-down on C parts (+66 and +122 days, +5 % and +9 %) and two B-class days, which the record's bar ("no more A- or B-class line-down days") does not allow.
- **The variant that opens late offers to C parts only** keeps exactly the A- and B-class days of the strict rule (122 and 306) at both discounts, at about a third of its regret (406 against 1,462 USD per case at 3 %, 986 against 2,916 at 8 %): it meets the record's bar. A and B parts then use the expected-cost rule among the engine's levers, which alone lowered their regret from 1,147 to 291 (A) and from 282 to 65 (B) without a line-down day more.
- The risk-target rule takes the split as often at both discounts (it ignores the price) and stays the most expensive ordering policy.

## Result against record 002's acceptance

Phase 4 asked for a risk-aware rule whose regret per case is below the strict rule's with no more A- or B-class line-down days, on the qualified segments. On the held-out half:

- **Expected cost, late offers for C parts only: passes** in every variant measured (levers only; late split at 3 % and 8 %).
- Expected cost for every part: passes with the engine's levers; with late offers it adds two B-class days in 439 B decisions (+0.7 %), so it misses the bar narrowly.
- Risk target: fails (regret above the strict rule's in every variant).

Which rule the case pages and the planner use is the owner's decision (record 002: "no material uses it before the replay shows it better"); nothing has changed in the ranking yet.

## Reproduce

```
uv run snm-data oracle --start 2026-02-02 --weeks 21 --horizon 91
uv run snm-data oracle --start 2026-02-02 --weeks 21 --horizon 91 --late-split-discount 0.03
uv run snm-data oracle --start 2026-02-02 --weeks 21 --horizon 91 --late-split-discount 0.08
```

About ten minutes each. The JSON summary splits every policy by condition and criticality.

## Caveats

- The oracle prices the engine's levers only (and, below, a synthetic offer); real supplier proposals vary more.
- Consecutive Mondays reopen the same shortage, so the 2,908 decisions are not independent; the counts overstate the evidence.
- "No other order placed" holds for the realized path and for the expected line-down alike; in practice the next run would react, which lowers the cost of waiting and of late offers for every policy.
- The calibration curve was fitted on the first half of the backtest's days; this replay is on the second half only.
