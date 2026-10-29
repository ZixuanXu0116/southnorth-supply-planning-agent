# Forecast accuracy backtest: forecast-accuracy-small

Decision record 002, phases 1 and 2. Profile `small`, plant US10; 52 weekly as-of days from 2025-08-04 to 2026-07-27; 74,101 active windows (forecast or actual above zero) of 87,863 per forecaster. Deterministic, no model calls (`uv run snm-data forecast-backtest`).

On each as-of day the engine's demand forecast is rebuilt as that day's extraction would have shown it and compared with the staging to production that followed. Columns: **Bias** = total forecast / total actual - 1 (negative: the forecast is too low); **WAPE** = total absolute error / total actual; **Under** = share of windows whose actual exceeded the forecast; **Missed** = share with demand but no forecast; **Explicit** = share of the forecast that is released orders' reservations and backlog rather than the trailing rate; **Band (P90)** = the 90th percentile of actual / forecast on the first half of the days; **Coverage** = share of the second half's windows within band x forecast (calibrated near 90 %). **Verdict** (record 002): *calibrated* when coverage is at least 85% (the segment qualifies; the band's width enters the cost comparison), *not calibrated* below, *too few windows* below 30 test windows.

# Phase 1: the engine's forecast

## By horizon

| horizon | Windows | Materials | Bias | WAPE | Under | Missed | Explicit | Band (P90) | Coverage | Verdict | Model holds at P90 / P95 / P97 / P98 / P99 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 7 | 12,648 | 338 | +49% | 91% | 9% | 4% | 62% | 1.00 | 91% | calibrated | n/a |
| 14 | 13,766 | 338 | +23% | 92% | 29% | 9% | 44% | 2.15 | 84% | not calibrated | n/a |
| 28 | 15,300 | 338 | +5% | 87% | 47% | 19% | 26% | 2.89 | 76% | not calibrated | n/a |
| 56 | 17,037 | 338 | -5% | 86% | 58% | 27% | 14% | 3.56 | 73% | not calibrated | n/a |
| lead time | 15,350 | 338 | -3% | 85% | 45% | 19% | 22% | 2.88 | 76% | not calibrated | n/a |

## Over the planned delivery time (the window an order decision protects)

### By criticality

| criticality | Windows | Materials | Bias | WAPE | Under | Missed | Explicit | Band (P90) | Coverage | Verdict | Model holds at P90 / P95 / P97 / P98 / P99 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 1,102 | 23 | -3% | 61% | 49% | 13% | 25% | 3.35 | 85% | not calibrated | n/a |
| B | 2,201 | 49 | +1% | 89% | 43% | 18% | 26% | 2.50 | 73% | not calibrated | n/a |
| C | 12,047 | 266 | -3% | 85% | 45% | 20% | 22% | 2.90 | 76% | not calibrated | n/a |

### By trailing demand pattern (13 weekly buckets)

| pattern | Windows | Materials | Bias | WAPE | Under | Missed | Explicit | Band (P90) | Coverage | Verdict | Model holds at P90 / P95 / P97 / P98 / P99 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| smooth | 104 | 11 | +9% | 48% | 39% | 0% | 16% | 2.09 | 98% | calibrated | n/a |
| erratic | 79 | 5 | +22% | 59% | 48% | 0% | 17% | 2.32 | 98% | calibrated | n/a |
| intermittent | 14,104 | 337 | +2% | 85% | 44% | 18% | 22% | 2.87 | 76% | not calibrated | n/a |
| lumpy | 600 | 88 | -16% | 89% | 38% | 18% | 14% | 1.59 | 58% | not calibrated | n/a |
| none | 463 | 141 | -88% | 88% | 90% | 70% | 100% | 6.00 | 0% | not calibrated | n/a |

### By trailing demand frequency (weeks with outflow in the last 13)

| frequency | Windows | Materials | Bias | WAPE | Under | Missed | Explicit | Band (P90) | Coverage | Verdict | Model holds at P90 / P95 / P97 / P98 / P99 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| none in 13 weeks | 463 | 141 | -88% | 88% | 90% | 70% | 100% | 6.00 | 0% | not calibrated | n/a |
| 1-3 weeks | 9,490 | 328 | -21% | 80% | 50% | 24% | 28% | 2.90 | 68% | not calibrated | n/a |
| 4-6 weeks | 4,660 | 336 | +19% | 92% | 33% | 8% | 18% | 2.72 | 87% | calibrated | n/a |
| 7-9 weeks | 554 | 53 | +37% | 91% | 29% | 1% | 16% | 2.39 | 92% | calibrated | n/a |
| 10-13 weeks | 183 | 11 | +14% | 52% | 43% | 0% | 17% | 2.27 | 99% | calibrated | n/a |

### By forecast source

| forecast_source | Windows | Materials | Bias | WAPE | Under | Missed | Explicit | Band (P90) | Coverage | Verdict | Model holds at P90 / P95 / P97 / P98 / P99 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| mostly explicit | 1,887 | 336 | -40% | 47% | 49% | 0% | 96% | 4.50 | 91% | calibrated | n/a |
| mixed | 2,503 | 321 | +13% | 60% | 30% | 0% | 41% | 2.18 | 98% | calibrated | n/a |
| mostly trailing rate | 10,960 | 338 | -1% | 103% | 47% | 27% | 3% | 2.95 | 71% | not calibrated | n/a |

### By criticality and demand frequency (the candidate segments)

| criticality | frequency | Windows | Materials | Bias | WAPE | Under | Missed | Explicit | Band (P90) | Coverage | Verdict | Model holds at P90 / P95 / P97 / P98 / P99 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | none in 13 weeks | 27 | 9 | -86% | 86% | 93% | 67% | 100% | 4.40 | 0% | too few windows | n/a |
| A | 1-3 weeks | 380 | 16 | -24% | 79% | 56% | 26% | 29% | 3.74 | 71% | not calibrated | n/a |
| A | 4-6 weeks | 416 | 21 | +6% | 82% | 43% | 7% | 23% | 3.88 | 89% | calibrated | n/a |
| A | 7-9 weeks | 131 | 10 | +5% | 55% | 38% | 0% | 19% | 2.34 | 96% | calibrated | n/a |
| A | 10-13 weeks | 148 | 5 | +3% | 40% | 46% | 0% | 27% | 2.37 | 100% | calibrated | n/a |
| B | none in 13 weeks | 53 | 16 | -80% | 80% | 85% | 70% | 100% | 2.75 | 0% | too few windows | n/a |
| B | 1-3 weeks | 1,377 | 48 | -17% | 84% | 48% | 23% | 31% | 2.50 | 64% | not calibrated | n/a |
| B | 4-6 weeks | 668 | 49 | +40% | 102% | 29% | 6% | 19% | 2.30 | 84% | not calibrated | n/a |
| B | 7-9 weeks | 96 | 8 | +23% | 78% | 35% | 0% | 25% | 2.85 | 92% | calibrated | n/a |
| B | 10-13 weeks | 7 | 3 | +275% | 275% | 0% | 0% | 18% | n/a | n/a | too few windows | n/a |
| C | none in 13 weeks | 383 | 116 | -89% | 89% | 91% | 70% | 100% | 6.00 | 0% | not calibrated | n/a |
| C | 1-3 weeks | 7,733 | 264 | -21% | 80% | 50% | 24% | 28% | 2.96 | 69% | not calibrated | n/a |
| C | 4-6 weeks | 3,576 | 266 | +19% | 92% | 32% | 8% | 18% | 2.63 | 86% | calibrated | n/a |
| C | 7-9 weeks | 327 | 35 | +38% | 93% | 23% | 1% | 16% | 2.17 | 91% | calibrated | n/a |
| C | 10-13 weeks | 28 | 3 | +17% | 55% | 39% | 0% | 14% | 1.74 | 90% | too few windows | n/a |

# Phase 2: forecasters compared over the planned delivery time

- `engine`: the engine today: trailing 28-day staging rate, netted weekly against the released orders.
- `rate_91`: trailing 91-day staging rate, netted the same way (option 2).
- `requirements_91`: trailing 91-day rate of production requirements, uncensored by stock-outs (option 4).
- `sba`: Croston-SBA on 26 weekly buckets, with its own 90th percentile (option 3).

*Model holds at P90 / P95 / P97 / P98 / P99* is the share of windows whose actual stayed within the forecaster's own percentile (Croston-SBA only). Each should reach its level for the model's probabilities to be used as they are; P90, P97, and P99 are the service targets of record 002 (amended 2026-10-19) for C, B, and A parts.

## All materials

| forecaster | Windows | Materials | Bias | WAPE | Under | Missed | Explicit | Band (P90) | Coverage | Verdict | Model holds at P90 / P95 / P97 / P98 / P99 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| engine | 17,554 | 338 | -3% | 85% | 39% | 17% | 22% | 2.88 | 78% | not calibrated | n/a |
| rate_91 | 17,554 | 338 | +1% | 69% | 37% | 2% | 21% | 2.83 | 91% | calibrated | n/a |
| requirements_91 | 17,554 | 338 | +1% | 69% | 37% | 2% | 21% | 2.79 | 91% | calibrated | n/a |
| sba | 17,554 | 338 | +5% | 68% | 33% | 0% | 20% | 2.90 | 93% | calibrated | 88% / 91% / 93% / 94% / 96% |

## By demand frequency

| frequency | forecaster | Windows | Materials | Bias | WAPE | Under | Missed | Explicit | Band (P90) | Coverage | Verdict | Model holds at P90 / P95 / P97 / P98 / P99 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none in 13 weeks | engine | 544 | 141 | -88% | 88% | 77% | 59% | 100% | 6.00 | 10% | not calibrated | n/a |
| none in 13 weeks | rate_91 | 544 | 141 | -88% | 88% | 77% | 59% | 100% | 6.00 | 10% | not calibrated | n/a |
| none in 13 weeks | requirements_91 | 544 | 141 | -88% | 88% | 77% | 57% | 98% | 4.00 | 10% | not calibrated | n/a |
| none in 13 weeks | sba | 544 | 141 | -16% | 55% | 44% | 0% | 14% | 3.95 | 10% | not calibrated | 76% / 80% / 82% / 83% / 85% |
| 1-3 weeks | engine | 11,347 | 328 | -21% | 80% | 42% | 20% | 28% | 2.90 | 72% | not calibrated | n/a |
| 1-3 weeks | rate_91 | 11,347 | 328 | -18% | 65% | 42% | 0% | 27% | 3.23 | 88% | calibrated | n/a |
| 1-3 weeks | requirements_91 | 11,347 | 328 | -18% | 65% | 42% | 0% | 27% | 3.14 | 88% | calibrated | n/a |
| 1-3 weeks | sba | 11,347 | 328 | -1% | 72% | 36% | 0% | 22% | 3.26 | 93% | calibrated | 87% / 90% / 92% / 94% / 95% |
| 4-6 weeks | engine | 4,926 | 336 | +19% | 92% | 31% | 7% | 18% | 2.72 | 87% | calibrated | n/a |
| 4-6 weeks | rate_91 | 4,926 | 336 | +26% | 73% | 23% | 0% | 17% | 1.73 | 94% | calibrated | n/a |
| 4-6 weeks | requirements_91 | 4,926 | 336 | +26% | 73% | 22% | 0% | 17% | 1.71 | 94% | calibrated | n/a |
| 4-6 weeks | sba | 4,926 | 336 | +12% | 66% | 27% | 0% | 19% | 2.24 | 96% | calibrated | 93% / 95% / 96% / 96% / 97% |
| 7-9 weeks | engine | 554 | 53 | +37% | 91% | 29% | 1% | 16% | 2.39 | 92% | calibrated | n/a |
| 7-9 weeks | rate_91 | 554 | 53 | +37% | 78% | 27% | 0% | 16% | 1.65 | 95% | calibrated | n/a |
| 7-9 weeks | requirements_91 | 554 | 53 | +36% | 78% | 29% | 0% | 16% | 1.66 | 95% | calibrated | n/a |
| 7-9 weeks | sba | 554 | 53 | +20% | 72% | 32% | 0% | 19% | 1.93 | 96% | calibrated | 89% / 92% / 94% / 95% / 97% |
| 10-13 weeks | engine | 183 | 11 | +14% | 52% | 43% | 0% | 17% | 2.27 | 99% | calibrated | n/a |
| 10-13 weeks | rate_91 | 183 | 11 | +18% | 49% | 35% | 0% | 16% | 1.57 | 95% | calibrated | n/a |
| 10-13 weeks | requirements_91 | 183 | 11 | +17% | 49% | 36% | 0% | 16% | 1.57 | 96% | calibrated | n/a |
| 10-13 weeks | sba | 183 | 11 | +6% | 48% | 42% | 0% | 18% | 1.67 | 96% | calibrated | 85% / 89% / 90% / 92% / 96% |

## By criticality

| criticality | forecaster | Windows | Materials | Bias | WAPE | Under | Missed | Explicit | Band (P90) | Coverage | Verdict | Model holds at P90 / P95 / P97 / P98 / P99 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | engine | 1,193 | 23 | -3% | 61% | 45% | 12% | 25% | 3.35 | 86% | calibrated | n/a |
| A | rate_91 | 1,193 | 23 | +3% | 50% | 39% | 2% | 24% | 2.74 | 95% | calibrated | n/a |
| A | requirements_91 | 1,193 | 23 | +3% | 50% | 39% | 2% | 24% | 2.74 | 95% | calibrated | n/a |
| A | sba | 1,193 | 23 | +2% | 47% | 38% | 0% | 24% | 2.73 | 96% | calibrated | 86% / 89% / 91% / 92% / 95% |
| B | engine | 2,547 | 49 | +1% | 89% | 37% | 16% | 26% | 2.50 | 75% | not calibrated | n/a |
| B | rate_91 | 2,547 | 49 | +8% | 76% | 35% | 1% | 24% | 2.62 | 89% | calibrated | n/a |
| B | requirements_91 | 2,547 | 49 | +9% | 76% | 35% | 1% | 24% | 2.60 | 89% | calibrated | n/a |
| B | sba | 2,547 | 49 | +17% | 79% | 32% | 0% | 23% | 2.41 | 89% | calibrated | 89% / 92% / 94% / 95% / 96% |
| C | engine | 13,814 | 266 | -3% | 85% | 39% | 17% | 22% | 2.90 | 78% | not calibrated | n/a |
| C | rate_91 | 13,814 | 266 | +1% | 70% | 37% | 2% | 21% | 2.86 | 90% | calibrated | n/a |
| C | requirements_91 | 13,814 | 266 | +1% | 70% | 37% | 2% | 21% | 2.84 | 91% | calibrated | n/a |
| C | sba | 13,814 | 266 | +5% | 69% | 33% | 0% | 20% | 3.00 | 94% | calibrated | 88% / 91% / 93% / 94% / 96% |

# Phase 3: the model's probabilities calibrated per segment

The model's stock-out probabilities are too low in the tail. The engine maps each one through a calibration curve per demand-frequency segment: at each of the model's percentiles (90th to 99th), the model's own exceedance probability against the share of windows whose demand exceeded that percentile on the first half of the days; the second half checks it. Between the points the curve is linear, through 0 and 1 at the ends; the calibrated probability is what the case pages show and what the service targets (A 1 %, B 3 %, C 10 %) are checked against. In every segment with outflow in the last 13 weeks the second half ran out at most as often as the curve says, so the curve is conservative there; materials without outflow in 13 weeks stay unqualified (the second half ran out in 62 % of windows whatever the model said).

## By demand frequency

| frequency | Model's risk | Calibrated (first half) | Observed (second half) | Test windows |
|---|---|---|---|---|
| none in 13 weeks | 1% | 10% | 62% | 48 |
| none in 13 weeks | 2% | 12% | 62% | 48 |
| none in 13 weeks | 3% | 14% | 62% | 48 |
| none in 13 weeks | 5% | 16% | 62% | 48 |
| none in 13 weeks | 10% | 20% | 62% | 48 |
| 1-3 weeks | 1% | 5% | 4% | 5,219 |
| 1-3 weeks | 2% | 7% | 5% | 5,219 |
| 1-3 weeks | 3% | 9% | 7% | 5,219 |
| 1-3 weeks | 5% | 11% | 9% | 5,219 |
| 1-3 weeks | 10% | 14% | 13% | 5,219 |
| 4-6 weeks | 1% | 6% | 1% | 3,071 |
| 4-6 weeks | 2% | 7% | 1% | 3,071 |
| 4-6 weeks | 3% | 8% | 2% | 3,071 |
| 4-6 weeks | 5% | 9% | 2% | 3,071 |
| 4-6 weeks | 10% | 13% | 4% | 3,071 |
| 7-9 weeks | 1% | 5% | 1% | 330 |
| 7-9 weeks | 2% | 8% | 2% | 330 |
| 7-9 weeks | 3% | 10% | 3% | 330 |
| 7-9 weeks | 5% | 15% | 3% | 330 |
| 7-9 weeks | 10% | 21% | 5% | 330 |
| 10-13 weeks | 1% | 7% | 1% | 100 |
| 10-13 weeks | 2% | 12% | 4% | 100 |
| 10-13 weeks | 3% | 16% | 6% | 100 |
| 10-13 weeks | 5% | 16% | 8% | 100 |
| 10-13 weeks | 10% | 22% | 9% | 100 |

## By criticality

| criticality | Model's risk | Calibrated (first half) | Observed (second half) | Test windows |
|---|---|---|---|---|
| A | 1% | 8% | 2% | 595 |
| A | 2% | 12% | 4% | 595 |
| A | 3% | 14% | 5% | 595 |
| A | 5% | 16% | 7% | 595 |
| A | 10% | 19% | 8% | 595 |
| B | 1% | 4% | 3% | 1,273 |
| B | 2% | 5% | 4% | 1,273 |
| B | 3% | 7% | 5% | 1,273 |
| B | 5% | 8% | 7% | 1,273 |
| B | 10% | 11% | 11% | 1,273 |
| C | 1% | 6% | 3% | 6,900 |
| C | 2% | 7% | 4% | 6,900 |
| C | 3% | 9% | 5% | 6,900 |
| C | 5% | 11% | 7% | 6,900 |
| C | 10% | 14% | 9% | 6,900 |

## Step 1, superseded: a multiplier on the model's percentile

The first approach multiplied the model's percentile per segment until it held at the target. It holds, but demand comes in whole lumps, so the multiplier overshoots (it lands on ratios such as 12 / 8) and, applied to the simulated lumps, turns a model risk of 1-3 % into about 13 % where the backtest observes 4-7 %. Kept for the record; the engine uses the curve above.

### By demand frequency

| frequency | Target | Test windows | Multiplier | Raw percentile holds | Recalibrated holds |
|---|---|---|---|---|---|
| none in 13 weeks | P90 | 48 | 1.75 | 38% | 38% |
| none in 13 weeks | P97 | 48 | 1.50 | 38% | 42% |
| none in 13 weeks | P99 | 48 | 1.50 | 38% | 46% |
| 1-3 weeks | P90 | 5,219 | 1.50 | 87% | 95% |
| 1-3 weeks | P97 | 5,219 | 1.50 | 93% | 98% |
| 1-3 weeks | P99 | 5,219 | 1.50 | 96% | 99% |
| 4-6 weeks | P90 | 3,071 | 1.17 | 96% | 98% |
| 4-6 weeks | P97 | 3,071 | 1.60 | 98% | 100% |
| 4-6 weeks | P99 | 3,071 | 1.51 | 99% | 100% |
| 7-9 weeks | P90 | 330 | 1.26 | 95% | 98% |
| 7-9 weeks | P97 | 330 | 1.28 | 97% | 99% |
| 7-9 weeks | P99 | 330 | 1.33 | 99% | 99% |
| 10-13 weeks | P90 | 100 | 1.28 | 91% | 98% |
| 10-13 weeks | P97 | 100 | 1.24 | 94% | 99% |
| 10-13 weeks | P99 | 100 | 1.22 | 99% | 100% |

### By criticality, at its service target (A 99 %, B 97 %, C 90 %)

| criticality | Target | Test windows | Multiplier | Raw percentile holds | Recalibrated holds |
|---|---|---|---|---|---|
| A | P99 | 595 | 1.50 | 98% | 99% |
| B | P97 | 1,273 | 1.45 | 95% | 98% |
| C | P90 | 6,900 | 1.44 | 91% | 95% |

## Approximations

- Every forecaster is netted against the released orders' requirements week by week, as the engine nets its rate; the SBA percentile is the larger of the window's explicit requirements and the model's 90th percentile of demand over the window (an approximation of the netting).
- Past days have no planned orders (they exist only at the extraction), so the past forecast is the released orders' reservations plus the trailing rate; the live engine adds the planned orders' requirements for weeks 2 to 13. In this synthetic data the planned orders are drawn around each product's trailing rate, so they carry little information beyond it; a real MRP run would.
- Reservations are due at the order start; staging happens two days earlier, which moves demand across a window's edge by up to two days.
- Realized outflow is staging as far as stock allowed: a stock-out hides demand until it is staged.
- Windows of consecutive weeks overlap, so the counts overstate the independent evidence.
