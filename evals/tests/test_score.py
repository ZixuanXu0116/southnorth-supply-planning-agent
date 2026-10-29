from snm_evals.cases import AnswerSpec, load_suite
from snm_evals.run import render_report
from snm_evals.score import match_values, score_case

SUITE = load_suite("seed")


def _answer(behavior="answer", values=(), text="ok"):
    return {
        "arm": "main",
        "status": "answered",
        "answer": {"behavior": behavior, "answer": text, "values": list(values)},
    }


REFS_01 = {"primary": {"columns": ["vstel", "on_time_pgi_rate"],
                       "rows": [["C100", 0.9661], ["C300", 0.9627], ["U100", 0.9633]]}}  # fmt: skip


def test_rates_match_as_fraction_or_percent_with_site_aliases() -> None:
    values = [
        {"label": "Longgang on-time PGI", "value": 96.61, "unit": "%"},
        {"label": "Dongguan (C300)", "value": 0.9627},
        {"label": "Round Rock", "value": 96.3, "unit": "%"},
    ]
    score = score_case(SUITE.case("seed_01"), REFS_01, _answer(values=values))
    assert score.passed and score.values_ok


def test_wrong_site_label_or_value_fails() -> None:
    swapped = [
        {"label": "Longgang", "value": 0.9627},
        {"label": "Dongguan", "value": 0.9661},
        {"label": "Round Rock", "value": 0.9633},
    ]
    score = score_case(SUITE.case("seed_01"), REFS_01, _answer(values=swapped))
    assert not score.passed and score.missing


def test_behavior_cases_and_operator_leaks() -> None:
    case = SUITE.case("seed_17")
    assert score_case(case, None, _answer("policy_restricted")).passed
    assert not score_case(case, None, _answer("answer")).passed
    leak = score_case(case, None, _answer("policy_restricted", text="Top picker: RRU0007"))
    assert not leak.passed and not leak.policy_ok


def test_variants_and_order() -> None:
    case = SUITE.case("seed_13")
    refs = {
        "primary": {"columns": ["werks", "waers", "net_gi_value"],
                    "rows": [["CN10", "CNY", "100.0"], ["US10", "USD", "5.0"]]},
        "per_site": {"columns": ["vstel", "waers", "net_gi_value"],
                     "rows": [["C100", "CNY", "60.0"], ["C300", "CNY", "40.0"], ["U100", "USD", "5.0"]]},
    }  # fmt: skip
    per_site = [
        {"label": "Longgang net value", "value": 60.0, "unit": "CNY"},
        {"label": "Dongguan net value", "value": 40.0, "unit": "CNY"},
        {"label": "Round Rock net value", "value": 5.0, "unit": "USD"},
    ]
    score = score_case(case, refs, _answer(values=per_site))
    assert score.passed and score.matched_reference == "per_site"

    spec = SUITE.case("seed_07").answer
    ref = {"columns": ["material", "shipped_qty_ea"], "rows": [["40001065", "10"], ["40000507", "5"]]}
    in_order = [{"label": "40001065", "value": 10}, {"label": "40000507", "value": 5}]
    assert match_values(spec, ref, in_order) == []
    assert match_values(spec, ref, list(reversed(in_order))) == ["order"]


def test_durations_are_converted_between_time_units() -> None:
    case = SUITE.case("seed_04")
    refs = {"primary": {"columns": ["median_minutes"], "rows": [[110.6]]}}
    hours = [{"label": "DG01 median wave release to GI", "value": 1.8433, "unit": "hours"}]
    assert score_case(case, refs, _answer(values=hours)).passed
    wrong = [{"label": "DG01 median wave release to GI", "value": 1.8433, "unit": "minutes"}]
    assert not score_case(case, refs, _answer(values=wrong)).passed


def test_single_row_keys_may_be_named_in_the_answer_text() -> None:
    spec = AnswerSpec(keys=["name1"], values=[{"column": "open_lines", "abs": 0}])
    ref = {"columns": ["name1", "open_lines"], "rows": [["Hoshino RF Devices", 29]]}
    reported = [{"label": "Open schedule lines", "value": 29}]
    assert match_values(spec, ref, reported) == ["open_lines{'name1': 'Hoshino RF Devices'}=29"]
    assert match_values(spec, ref, reported, "Hoshino RF Devices has 29 open lines.") == []
    two_rows = {"columns": ["name1", "open_lines"], "rows": [["A Corp", 29], ["B Corp", 3]]}
    assert match_values(spec, two_rows, reported, "A Corp and B Corp") != []


def test_must_mention_terms_are_checked_in_the_answer_text() -> None:
    spec = AnswerSpec(must_mention=[["SP-FST"], ["0010", "HRCK", "high rack"]])
    ref = {"columns": [], "rows": []}
    assert match_values(spec, ref, [], "Fasteners (SP-FST) now go to the High Rack.") == []
    assert match_values(spec, ref, [], "Fasteners now go to shelving.") == [
        "mention SP-FST",
        "mention 0010 | HRCK | high rack",
    ]


def test_country_codes_match_country_names() -> None:
    spec = AnswerSpec(keys=["land1"], values=[{"column": "deliveries", "abs": 0}])
    ref = {"columns": ["land1", "deliveries"], "rows": [["IL", 70], ["SG", 8], ["KR", 8]]}
    reported = [
        {"label": "Israel", "value": 70},
        {"label": "Singapore deliveries", "value": 8},
        {"label": "South Korea deliveries", "value": 8},
    ]
    assert match_values(spec, ref, reported) == []


def test_numeric_codes_match_with_a_letter_prefix_but_short_ones_do_not() -> None:
    spec = AnswerSpec(keys=["bukrs"], values=[{"column": "on_time_rate", "abs": 0.005}])
    ref = {"columns": ["bukrs", "on_time_rate"], "rows": [["1000", 0.7852], ["2000", 0.8016]]}
    reported = [
        {"label": "CC1000 on-time rate FY2026 Q3", "value": 78.5, "unit": "%"},
        {"label": "CC2000 on-time rate FY2026 Q3", "value": 80.2, "unit": "%"},
    ]
    assert match_values(spec, ref, reported) == []
    # Longer digit runs and short codes stay distinct: 10001 is not 1000, and US10 is not period 10.
    assert match_values(spec, ref, [{"label": "CC10001 rate", "value": 78.5, "unit": "%"}])
    periods = AnswerSpec(keys=["poper"], values=[{"column": "n", "abs": 0}])
    period_ref = {"columns": ["poper", "n"], "rows": [["10", 5]]}
    assert match_values(periods, period_ref, [{"label": "US10 tasks", "value": 5}])
    assert match_values(periods, period_ref, [{"label": "period 10 tasks", "value": 5}]) == []


def test_faithfulness_is_carried_into_scores_and_reports() -> None:
    case = SUITE.case("seed_17")
    result = {**_answer("policy_restricted"), "faithfulness": {"checked": 2, "traced": 1, "untraced": [{}]}}
    result["rejected_submissions"] = 1
    score = score_case(case, None, result)
    assert (score.values_checked, score.values_traced, score.rejected_submissions) == (2, 1, 1)
    old = score_case(case, None, _answer("policy_restricted"))  # batches from before the check
    assert (old.values_checked, old.values_traced) == (0, 0)
    report = render_report(SUITE, [score, old], "b")
    assert "Traced values" in report and "| 1/2 (50%) |" in report


def test_fiscal_period_keys_accept_the_period_prefix() -> None:
    from snm_evals.score import _key_matches

    assert _key_matches("P10 SP-GAS", "10", "fiscal_period")
    assert _key_matches("RR01 waves per day, FY2026 P08", "8", "fiscal_period")
    assert _key_matches("period 11", "11", "fiscal_period")
    assert not _key_matches("P10 SP-GAS", "10", "plant_code")  # only period columns take the prefix
    assert not _key_matches("US10 deliveries", "10", "fiscal_period")


def test_reports_show_the_cache_read_share_of_prompt_tokens() -> None:
    case = SUITE.case("seed_17")
    result = _answer("policy_restricted")
    result["usage"] = {"input": 1000, "cache_read": 8000, "cache_write": 1000, "cost_usd": 0.01}
    score = score_case(case, None, result)
    assert (score.input_tokens, score.cache_read_tokens, score.cache_write_tokens) == (1000, 8000, 1000)
    assert "| Cache read |" in render_report(SUITE, [score], "b")
    assert "| 80% |" in render_report(SUITE, [score], "b")
    old = score_case(case, None, _answer("policy_restricted"))  # batches without token counts
    assert "| - |" in render_report(SUITE, [old], "b")


def test_rate_changes_in_percentage_points_match_fractions() -> None:
    spec = AnswerSpec(keys=["vstel"], values=[{"column": "rate_change", "abs": 0.002}])
    ref = {
        "columns": ["vstel", "rate_change"],
        "rows": [["C100", -0.0064], ["C300", 0.0016], ["U100", 0.1364]],
    }
    reported = [
        {"label": "Longgang change", "value": -0.64, "unit": "percentage points"},
        {"label": "Dongguan change", "value": 0.16, "unit": "pp"},
        {"label": "Round Rock change", "value": 13.64, "unit": "points"},
    ]
    assert match_values(spec, ref, reported) == []
    # Without a percent unit a small number is read as a fraction: 0.16 is not 0.0016.
    assert match_values(spec, ref, [{**reported[1], "unit": None}, reported[0], reported[2]])
