"""Paired statistics for arm comparisons (pure Python, deterministic).

Runs are clustered by case (repetitions of one case are not independent), so confidence intervals come
from a case-level (cluster) bootstrap, and McNemar's exact test uses one outcome per case: the majority
over its repetitions.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from math import comb


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar p-value; b = cases only A passes, c = cases only B passes."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(comb(n, k) for k in range(min(b, c) + 1)) / 2**n
    return min(1.0, 2 * tail)


def cluster_bootstrap_diff(
    a: dict[str, float], b: dict[str, float], iterations: int = 10_000, seed: int = 20260925
) -> tuple[float, float, float]:
    """Mean of (a - b) over shared clusters, with a 95 % percentile interval from resampling clusters."""
    keys = sorted(set(a) & set(b))
    if not keys:
        return 0.0, 0.0, 0.0
    diffs = [a[key] - b[key] for key in keys]
    rng = random.Random(seed)
    samples = sorted(sum(rng.choices(diffs, k=len(diffs))) / len(diffs) for _ in range(iterations))
    return sum(diffs) / len(diffs), samples[int(0.025 * iterations)], samples[int(0.975 * iterations) - 1]


@dataclass(frozen=True)
class PairedComparison:
    arm_a: str
    arm_b: str
    cases: int
    pass_diff: float
    pass_ci: tuple[float, float]
    a_only: int
    b_only: int
    mcnemar_p: float
    cost_diff: float
    cost_ci: tuple[float, float]


def compare_arms(
    arm_a: str,
    arm_b: str,
    passes: dict[tuple[str, str], list[bool]],
    costs: dict[tuple[str, str], list[float]],
) -> PairedComparison | None:
    """Compare two arms on the cases both ran; passes/costs are keyed by (case_id, arm)."""
    cases = sorted(
        {case for case, arm in passes if arm == arm_a} & {case for case, arm in passes if arm == arm_b}
    )
    if not cases:
        return None

    def mean(values: list[float] | list[bool]) -> float:
        return sum(values) / len(values)

    rate_a = {case: mean(passes[(case, arm_a)]) for case in cases}
    rate_b = {case: mean(passes[(case, arm_b)]) for case in cases}
    majority_a = {case: rate_a[case] > 0.5 for case in cases}
    majority_b = {case: rate_b[case] > 0.5 for case in cases}
    a_only = sum(majority_a[case] and not majority_b[case] for case in cases)
    b_only = sum(majority_b[case] and not majority_a[case] for case in cases)
    pass_diff, pass_lo, pass_hi = cluster_bootstrap_diff(rate_a, rate_b)
    cost_a = {case: mean(costs[(case, arm_a)]) for case in cases}
    cost_b = {case: mean(costs[(case, arm_b)]) for case in cases}
    cost_diff, cost_lo, cost_hi = cluster_bootstrap_diff(cost_a, cost_b)
    return PairedComparison(
        arm_a=arm_a,
        arm_b=arm_b,
        cases=len(cases),
        pass_diff=pass_diff,
        pass_ci=(pass_lo, pass_hi),
        a_only=a_only,
        b_only=b_only,
        mcnemar_p=mcnemar_exact(a_only, b_only),
        cost_diff=cost_diff,
        cost_ci=(cost_lo, cost_hi),
    )


def consistency(passes: dict[tuple[str, str], list[bool]], arm: str) -> tuple[int, int]:
    """(cases whose repetitions all agree, cases with at least two repetitions) for one arm."""
    repeated = [runs for (case, name), runs in passes.items() if name == arm and len(runs) >= 2]
    return sum(len(set(runs)) == 1 for runs in repeated), len(repeated)
