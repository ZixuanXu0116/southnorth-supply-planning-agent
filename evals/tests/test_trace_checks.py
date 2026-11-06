"""The code checks of a planner trace: one passing and one failing trace for each."""

from __future__ import annotations

import json

import pytest

from snm_evals import trace_checks as tc

CASE = "C-US10-40000054-SH-20261028"


class Trace:
    """A planner trace built turn by turn, in the harness's event format."""

    def __init__(self, task: str = "evaluate") -> None:
        self.events: list[dict] = [{"event": "run_start", "run_id": "r", "case_id": CASE, "task": task}]
        self.turn = 0
        self.ids = 0

    def turn_of(self, *calls: tuple, extra: list[dict] | None = None) -> Trace:
        """Calls of one turn: (name, arguments, output, refused?)."""
        self.turn += 1
        made = []
        for name, arguments, *rest in calls:
            self.ids += 1
            made.append({"id": f"t{self.ids}", "name": name, "arguments": arguments, "_rest": rest})
        self.events.append(
            {
                "event": "assistant",
                "turn": self.turn,
                "tool_calls": [{k: v for k, v in c.items() if k != "_rest"} for c in made],
            }
        )
        self.events += extra or []
        for call in made:
            output = call["_rest"][0] if call["_rest"] else "{}"
            refused = bool(call["_rest"][1]) if len(call["_rest"]) > 1 else False
            self.events.append(
                {
                    "event": "tool",
                    "id": call["id"],
                    "name": call["name"],
                    "output": output,
                    "is_error": refused,
                }
            )
        return self

    def end(self, status: str = "written") -> list[dict]:
        return [*self.events, {"event": "run_end", "status": status}]


def ranked(*pairs: tuple[str, str]) -> str:
    return json.dumps({"ranked": [{"evaluation_id": e, "case_id": CASE, "option": o} for e, o in pairs]})


def proposal(*pairs: tuple[str, str], text: str = "") -> dict:
    return {
        "case_id": CASE,
        "options": [{"option": o, "evaluation_id": e} for e, o in pairs],
        "recommended": pairs[0][1],
        "rationale_text": text,
    }


E1, E2 = "E-1da8941aeafd", "E-7aab542fa912"
GOOD = (E1, "P-2"), (E2, "P-3")


def sound_evaluate() -> Trace:
    return Trace().turn_of(("rank_options", {}, ranked(*GOOD)), ("get_case", {}, "{}"))


def result(events: list[dict], number: int) -> tc.Result:
    return next(r for r in tc.check_trace(events).results if r.check == number)


def test_a_sound_evaluate_run_passes_every_check() -> None:
    events = (
        sound_evaluate()
        .turn_of(("propose_recommendation", proposal(*GOOD, text=f"P-2 ({E1})"), "{}"),
                 extra=[{"event": "figure_check", "untraced_figures": []}])
        .end()
    )  # fmt: skip
    report = tc.check_trace(events)
    assert report.sound and {r.check: r.status for r in report.results} == {
        1: "pass", 2: "pass", 3: "pass", 4: "pass", 5: "na", 6: "na", 7: "pass", 9: "pass"
    }  # fmt: skip


def test_1_read_before_writing() -> None:
    late = Trace().turn_of(("rank_options", {}, ranked(*GOOD)), ("propose_recommendation", proposal(*GOOD)))
    assert result(late.end(), 1).status == "fail"
    failed_read = Trace().turn_of(("get_case", {}, "not found", True)).turn_of(("add_note", {"text": "x"}))
    assert result(failed_read.end(), 1).status == "fail"
    assert (
        result(sound_evaluate().turn_of(("propose_recommendation", proposal(*GOOD))).end(), 1).status
        == "pass"
    )


def test_2_rank_before_recommending() -> None:
    unranked = Trace().turn_of(("get_case", {}, "{}")).turn_of(("propose_recommendation", proposal(*GOOD)))
    assert result(unranked.end(), 2).status == "fail"
    assert (
        result(sound_evaluate().turn_of(("propose_recommendation", proposal(*GOOD))).end(), 2).status
        == "pass"
    )
    assert result(Trace("prepare").end(), 2).status == "na"


def test_3_cited_ids_traced() -> None:
    swapped = sound_evaluate().turn_of(("propose_recommendation", proposal((E2, "P-2"), (E1, "P-3"))))
    assert result(swapped.end(), 3).status == "fail"
    made_up = sound_evaluate().turn_of(
        ("propose_recommendation", proposal(*GOOD, text="E-000000000000 is best"))
    )
    assert result(made_up.end(), 3).status == "fail"
    assert (
        result(sound_evaluate().turn_of(("propose_recommendation", proposal(*GOOD))).end(), 3).status
        == "pass"
    )
    # A result cut short: an option the visible part does not show cannot be checked, so it does not fail.
    cut = ranked(*GOOD)[:-3] + "x" * tc.RESULT_LIMIT
    partial = Trace().turn_of(("rank_options", {}, cut), ("get_case", {}, "{}"))
    partial.turn_of(("propose_recommendation", proposal(*GOOD, ("E-0123456789ab", "P-9"))))
    assert result(partial.end(), 3).status == "pass"


def test_4_one_write_last_and_alone() -> None:
    assert (
        result(sound_evaluate().turn_of(("propose_recommendation", proposal(*GOOD))).end(), 4).status
        == "pass"
    )
    shared = (
        Trace().turn_of(("get_case", {}, "{}")).turn_of(("get_case", {}, "{}"), ("add_note", {"text": "x"}))
    )
    assert "shares turn" in result(shared.end(), 4).detail
    followed = sound_evaluate().turn_of(("propose_recommendation", proposal(*GOOD))).turn_of(("get_case", {}))
    assert "followed by" in result(followed.end(), 4).detail
    two = (
        sound_evaluate()
        .turn_of(("propose_recommendation", proposal(*GOOD)))
        .turn_of(("add_note", {"text": "x"}))
    )
    assert result(two.end(), 4).detail == "2 accepted writes"


def test_5_no_identical_retry() -> None:
    arguments = proposal(*GOOD, text="2.6 days")
    blind = sound_evaluate().turn_of(("propose_recommendation", arguments, "UNTRACED_FIGURES", True))
    assert result(blind.turn_of(("propose_recommendation", arguments)).end(), 5).status == "fail"
    fixed = sound_evaluate().turn_of(("propose_recommendation", arguments, "UNTRACED_FIGURES", True))
    assert result(fixed.turn_of(("propose_recommendation", proposal(*GOOD))).end(), 5).status == "pass"
    assert result(sound_evaluate().end(), 5).status == "na"


def case_read(lever: str | None) -> str:
    engine = {"alternatives": [{"lever": "expedited_new_order"}], "condition": "warning", "lever": lever}
    return json.dumps({"case_id": CASE, "engine": engine, "events": []})


def prepare(lever: str | None, *write: tuple, evaluations: tuple = ()) -> list[dict]:
    trace = Trace("prepare").turn_of(("get_case", {}, case_read(lever)), *evaluations)
    return trace.turn_of(*write).end()


def test_6_the_prepare_write_fits_the_case() -> None:
    quote = ("draft_supplier_request", {"request_type": "quote", "message": "m"})
    assert result(prepare("new_order", quote), 6).status == "pass"
    assert result(prepare("expedited_new_order", quote), 6).status == "pass"
    assert "expected prepare_transfer" in result(prepare("transfer", quote), 6).detail
    assert result(prepare("move_to_overflow", ("prepare_transfer", {"note": "n"})), 6).status == "pass"
    expedite = ("draft_supplier_request", {"request_type": "expedite", "message": "m"})
    assert result(prepare("expedite", expedite), 6).status == "pass"
    assert result(prepare("expedite", quote), 6).status == "fail"
    note = ("add_note", {"text": "review"})
    assert result(prepare(None, note), 6).status == "pass"
    assert result(prepare("new_order", note), 6).status == "fail"


def test_6_a_note_is_right_when_another_lever_covers_as_early_and_cheaper() -> None:
    def evaluation(option: str, coverage: str, cost: float) -> tuple:
        out = {
            "option": option,
            "kind": option,
            "coverage_date": coverage,
            "days_below_zero": 0,
            "total_cost": cost,
        }
        return ("evaluate_option", {"option": option}, json.dumps(out))

    note = ("add_note", {"text": "E-1 against E-2"})
    cheaper = (
        evaluation("expedited_new_order", "2026-10-01", 900.0),
        evaluation("expedite", "2026-10-01", 300.0),
    )
    assert result(prepare("expedited_new_order", note, evaluations=cheaper), 6).status == "pass"
    later = (
        evaluation("expedited_new_order", "2026-10-01", 900.0),
        evaluation("new_order", "2026-10-09", 75.0),
    )
    assert result(prepare("expedited_new_order", note, evaluations=later), 6).status == "fail"


def test_7_every_figure_traced() -> None:
    def run(untraced: list[str] | None) -> list[dict]:
        extra = [] if untraced is None else [{"event": "figure_check", "untraced_figures": untraced}]
        return sound_evaluate().turn_of(("propose_recommendation", proposal(*GOOD)), extra=extra).end()

    assert result(run([]), 7).status == "pass"
    assert result(run(["2.6"]), 7).detail == "untraced figures: 2.6"
    assert result(run(None), 7).status == "na"


def test_9_ended_within_budget_and_without_error() -> None:
    assert result(sound_evaluate().end("written"), 9).status == "pass"
    assert result(sound_evaluate().end("budget_exhausted"), 9).status == "fail"
    assert result(sound_evaluate().end("error"), 9).status == "fail"
    assert result(sound_evaluate().events, 9).detail == "no run_end"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"engine":{"alternatives":[{"lever":"x"}],"lever":"new_order"}}', ("new_order", True)),
        ('{"engine":{"lever":null}}', (None, True)),
        ('{"engine":{"note":"a } and a \\"lever\\"","lever":"transfer"}}', ("transfer", True)),
        ('{"engine":{"alternatives":[{"lever":"x"}],"coverage', (None, False)),
        ('{"case_id":"C"}', (None, False)),
    ],
)
def test_the_engines_own_lever_even_in_a_result_cut_short(text, expected) -> None:
    assert tc.engine_lever(text) == expected
