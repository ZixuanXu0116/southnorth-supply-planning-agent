from snm_evals.stats import cluster_bootstrap_diff, compare_arms, consistency, mcnemar_exact


def test_mcnemar_exact_matches_known_values() -> None:
    assert mcnemar_exact(0, 0) == 1.0
    assert abs(mcnemar_exact(0, 6) - 0.03125) < 1e-12  # 2 * 0.5**6
    assert abs(mcnemar_exact(2, 8) - 0.109375) < 1e-9
    assert mcnemar_exact(5, 5) == 1.0


def test_cluster_bootstrap_is_deterministic_and_brackets_the_mean() -> None:
    a = {f"c{i}": 1.0 for i in range(10)}
    b = {f"c{i}": (0.0 if i < 4 else 1.0) for i in range(10)}
    diff, lo, hi = cluster_bootstrap_diff(a, b)
    assert abs(diff - 0.4) < 1e-12 and lo <= diff <= hi
    assert cluster_bootstrap_diff(a, b) == (diff, lo, hi)


def test_compare_arms_uses_majority_per_case_and_consistency() -> None:
    passes = {
        ("c1", "main"): [True, True, False], ("c1", "base"): [False, False, True],
        ("c2", "main"): [True, True, True], ("c2", "base"): [True, True, True],
        ("c3", "main"): [True, False, True], ("c3", "base"): [False, False, False],
    }  # fmt: skip
    costs = {key: [0.1] * len(value) for key, value in passes.items()}
    result = compare_arms("main", "base", passes, costs)
    assert result is not None
    assert (result.cases, result.a_only, result.b_only) == (3, 2, 0)
    assert result.cost_diff == 0.0
    assert consistency(passes, "main") == (1, 3)
