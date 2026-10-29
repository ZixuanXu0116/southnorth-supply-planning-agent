from pathlib import Path

import pytest

from snm_data.contracts import ContractRegistry
from snm_evals.cases import ANSWERING, answers_path, load_answers, load_suite
from snm_tools.policy import ROLES, AccessPolicy
from snm_tools.sql_guard import SqlGuard

REPO = Path(__file__).resolve().parents[2]
SUITE_NAMES = ("seed", "bank_v1", "bank_v2", "bank_v3", "bank_v4", "bank_v5", "bank_v6")
SUITES = {name: load_suite(name) for name in SUITE_NAMES}
CASES = SUITES["bank_v6"].cases  # each bank includes the previous suites


@pytest.fixture(scope="module")
def registry() -> ContractRegistry:
    return ContractRegistry.load(REPO / "data_contracts")


def test_suite_shape() -> None:
    assert len(SUITES["seed"].cases) == 18
    assert len(SUITES["bank_v1"].cases) == 90
    assert len(SUITES["bank_v2"].cases) == 105
    assert len(SUITES["bank_v3"].cases) == 116
    assert len(SUITES["bank_v4"].cases) == 126
    assert len(SUITES["bank_v5"].cases) == 134
    assert len(SUITES["bank_v6"].cases) == 140
    assert all(case.role in ROLES for case in CASES)


def test_bank_split_is_stratified() -> None:
    test_share = sum(case.split == "test" for case in CASES) / len(CASES)
    assert 0.35 <= test_share <= 0.45
    for category in {case.category for case in CASES}:
        splits = {case.split for case in CASES if case.category == category}
        assert splits == {"dev", "test"}, category


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_required_kb_docs_exist(case) -> None:
    missing = [doc for doc in case.required_kb_docs if not (REPO / "kb" / doc).is_file()]
    assert not missing


@pytest.mark.parametrize("case", [c for c in CASES if c.reference_sql], ids=lambda case: case.id)
def test_reference_sql_is_allowed_for_the_case_role(case, registry) -> None:
    for name, sql, _spec in case.references():
        decision = SqlGuard(AccessPolicy(registry, case.role)).check(sql)
        assert decision.allowed, f"{name}: {decision.message}"
        assert set(decision.tables) <= set(case.required_tables), name


@pytest.mark.parametrize("name", SUITES)
def test_reference_answers_cover_every_answering_case(name: str) -> None:
    suite = SUITES[name]
    if not answers_path(suite).exists():
        pytest.skip("reference answers not computed yet (snm-evals refresh)")
    answers = load_answers(suite)
    assert answers["data_snapshot"] == suite.data_snapshot
    answering = {case.id for case in suite.cases if case.expected_behavior in ANSWERING}
    assert set(answers["results"]) == answering
    for case in suite.cases:
        if case.id in answering:
            references = answers["results"][case.id]
            assert set(references) == {name for name, _sql, _spec in case.references()}
            assert all(reference["rows"] for reference in references.values()), case.id


FOLLOWUP = load_suite("followup_v1")


@pytest.mark.parametrize("case", FOLLOWUP.cases, ids=lambda case: case.id)
def test_follow_up_threads_are_well_formed(case, registry) -> None:
    assert case.category == "followup" and case.turns
    assert all((REPO / "kb" / doc).is_file() for doc in case.required_kb_docs)
    for name, sql, _spec in case.references():
        decision = SqlGuard(AccessPolicy(registry, case.role)).check(sql)
        assert decision.allowed, f"{name}: {decision.message}"
        assert set(decision.tables) <= set(case.required_tables), name


def test_follow_up_reference_answers_cover_the_last_questions() -> None:
    answers = load_answers(FOLLOWUP)
    assert answers["data_snapshot"] == FOLLOWUP.data_snapshot
    answering = {case.id for case in FOLLOWUP.cases if case.expected_behavior in ANSWERING}
    assert set(answers["results"]) == answering
    assert all(answers["results"][case_id]["primary"]["rows"] for case_id in answering)


def test_only_follow_up_cases_have_turns() -> None:
    from snm_evals.cases import Case

    base = {"id": "x", "question": "q", "role": "ops_analyst_global", "expected_behavior": "decline"}
    with pytest.raises(ValueError, match="earlier turns"):
        Case(**base, category="followup")
    with pytest.raises(ValueError, match="earlier turns"):
        Case(**base, category="decline", turns=[{"question": "p", "expected_behavior": "answer"}])


def test_every_turn_of_a_thread_is_scored_on_behavior() -> None:
    from snm_evals.score import score_case

    case = FOLLOWUP.case("f03")

    def result(*behaviors: str | None) -> dict:
        def answer(behavior: str | None) -> dict:
            return {"answer": {"behavior": behavior, "answer": "ok", "values": []}} if behavior else {}

        *earlier, last = behaviors
        return {"arm": "main", "status": "answered", **answer(last), "earlier": [answer(b) for b in earlier]}

    assert score_case(case, None, result("answer", "answer_with_assumption", "policy_restricted")).passed
    refused_early = score_case(case, None, result("answer", "policy_restricted", "policy_restricted"))
    assert not refused_early.passed and refused_early.missing == [
        "turn 2 behavior policy_restricted, expected answer"
    ]
    leaked = score_case(case, None, result("answer", "answer", "answer"))
    assert not leaked.passed
    # A thread that stopped early has no answer for the missing turns.
    assert not score_case(case, None, result(None, "policy_restricted")).passed
