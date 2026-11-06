"""Planted defects: sound planner traces copied with one defect each, to measure what the checks catch.

    uv run snm-evals trace-checks --planted     # writes the copies and reports what the code checks catch

Structural defects, on 12 sound evaluate traces of ``figure-check-default-sonnet`` (each a check of
``trace_checks`` must fail) and on the 9 prepare traces of ``models-prepare-sonnet-5-5-medium``:

- ``no_rank``: the ``rank_options`` call and its result removed (check 2);
- ``read_after_write``: the case read moved to a turn after the write (check 1);
- ``repeated_refusal``: the write refused once and sent again unchanged (check 5);
- ``wrong_id``: the first option cites the second option's evaluation id (check 3);
- ``wrong_write`` (prepare): the other write type (check 6).

One defect in the text, for the LLM judge (rubric v2) only: ``false_comparison``, a sentence that compares two
options falsely by the run's own evaluations ("costs less than", "the cheapest", "better on every figure").
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .trace_checks import calls_of, check_trace, read_events

Events = list[dict[str, Any]]
EVALUATE_SOURCE = "figure-check-default-sonnet"
PREPARE_SOURCE = "models-prepare-sonnet-5-5-medium"
TARGET = {"no_rank": 2, "read_after_write": 1, "repeated_refusal": 5, "wrong_id": 3, "wrong_write": 6}


def sound_sources(batch_dir: Path, task: str, count: int) -> list[Path]:
    """The first ``count`` traces of the task that pass every check, with no refused call and, for evaluate,
    a ranking the trace holds in full (so that a planted id or comparison can be checked against it)."""
    chosen = []
    for path in sorted(batch_dir.rglob("trace.jsonl")):
        events = read_events(path)
        start = events[0] if events else {}
        if start.get("task") != task:
            continue
        calls = calls_of(events)
        if any(c.ok is False for c in calls) or not check_trace(events).sound:
            continue
        if task == "evaluate":
            rank = next((c for c in calls if c.name == "rank_options"), None)
            if rank is None or not isinstance(rank.parsed(), dict):
                continue
        chosen.append(path)
        if len(chosen) == count:
            break
    return chosen


def _tool_ids(events: Events, name: str) -> set[str]:
    return {
        call["id"]
        for e in events
        if e.get("event") == "assistant"
        for call in e.get("tool_calls") or []
        if call["name"] == name and call.get("id")
    }


def _without(events: Events, ids: set[str]) -> Events:
    kept = []
    for event in events:
        if event.get("event") == "assistant":
            event = {
                **event,
                "tool_calls": [c for c in event.get("tool_calls") or [] if c.get("id") not in ids],
            }
        elif event.get("event") == "tool" and event.get("id") in ids:
            continue
        kept.append(event)
    return kept


def _write_turn(events: Events) -> int:
    return max(e["turn"] for e in events if e.get("event") == "assistant")


def no_rank(events: Events) -> Events:
    return _without(events, _tool_ids(events, "rank_options"))


def read_after_write(events: Events) -> Events:
    """The case reads move to their own turn after the write (and before the run's end)."""
    ids = _tool_ids(events, "get_case") | _tool_ids(events, "explain_case")
    calls = [
        c
        for e in events
        if e.get("event") == "assistant"
        for c in e.get("tool_calls") or []
        if c.get("id") in ids
    ]
    results = [e for e in events if e.get("event") == "tool" and e.get("id") in ids]
    kept = _without(events, ids)
    end = next(i for i, e in enumerate(kept) if e.get("event") == "run_end")
    turn = {"event": "assistant", "turn": _write_turn(events) + 1, "tool_calls": calls, "text": ""}
    return [*kept[:end], turn, *results, *kept[end:]]


def repeated_refusal(events: Events) -> Events:
    """The write is refused once, then sent again with the same arguments."""
    out: Events = []
    turn = _write_turn(events)
    for event in events:
        if event.get("event") == "assistant" and event.get("turn") == turn:
            call = event["tool_calls"][0]
            refused = {**call, "id": f"{call['id']}-refused"}
            out.append({**event, "tool_calls": [refused]})
            out.append(
                {"event": "tool", "id": refused["id"], "name": call["name"], "is_error": True,
                 "output": "Refused: the case changed while the draft was written; read it again."}
            )  # fmt: skip
            event = {**event, "turn": turn + 1}
        elif event.get("event") == "assistant" and event.get("turn", 0) > turn:
            event = {**event, "turn": event["turn"] + 1}
        out.append(event)
    return out


def _proposal(events: Events) -> dict[str, Any]:
    return next(
        call["arguments"]
        for e in events
        if e.get("event") == "assistant"
        for call in e.get("tool_calls") or []
        if call["name"] == "propose_recommendation"
    )


def wrong_id(events: Events) -> Events:
    """The first option cites the second option's evaluation id."""
    out = copy.deepcopy(events)
    options = _proposal(out)["options"]
    options[0]["evaluation_id"] = options[1]["evaluation_id"]
    return out


def wrong_write(events: Events) -> Events:
    """The other write type: a quote request where a transfer was due, a transfer where a quote was due."""
    out = copy.deepcopy(events)
    for event in out:
        if event.get("event") != "assistant":
            continue
        for call in event.get("tool_calls") or []:
            args = call["arguments"]
            if call["name"] == "prepare_transfer":
                call["name"] = "draft_supplier_request"
                call["arguments"] = {"case_id": args.get("case_id"), "request_type": "quote",
                                     "message": args.get("note", "")}  # fmt: skip
            elif call["name"] == "draft_supplier_request":
                kind = "expedite" if args.get("request_type") == "quote" else "quote"
                call["arguments"] = {**args, "request_type": kind}
    ids = {
        call["id"]: call["name"]
        for e in out
        if e.get("event") == "assistant"
        for call in e.get("tool_calls") or []
    }
    for event in out:
        if event.get("event") == "tool" and event.get("id") in ids:
            event["name"] = ids[event["id"]]
    return out


def _ranked(events: Events) -> list[dict[str, Any]]:
    for call in calls_of(events):
        if call.name == "rank_options" and isinstance(parsed := call.parsed(), dict):
            return parsed.get("ranked") or []
    return []


def _short(option: str) -> str:
    """The name the rationale uses: ``P-2`` for ``P-2-7-C-...``, the kind for an engine lever."""
    parts = option.split("-")
    return "-".join(parts[:2]) if option.startswith("P-") and len(parts) > 1 else option


def _number(value: Any) -> float:
    return float(str(value).rstrip("%")) if value not in (None, "") else 0.0


def false_statement(ranked: list[dict[str, Any]], recommended: str) -> str:
    """A comparison of the recommended option that the evaluations make false, naming the figure it compares
    (an unnamed "costs less" can be true of the option cost and false of the expected total)."""
    by_option = {o["option"]: o for o in ranked}
    chosen = by_option.get(recommended) or ranked[0]
    others = [o for o in ranked if o is not chosen]
    cost = "expected_total_cost" if any("expected_total_cost" in o for o in ranked) else "total_cost"
    measure = "expected total cost" if cost == "expected_total_cost" else "total cost"
    cheapest = min(ranked, key=lambda o: _number(o.get(cost)))
    if cheapest is not chosen:
        return f"{_short(chosen['option'])} has the lowest {measure} of the options."
    for other in others:
        worse_somewhere = (
            _number(chosen.get("stockout_risk")) > _number(other.get("stockout_risk"))
            or (chosen.get("days_below_safety") or 0) > (other.get("days_below_safety") or 0)
            or str(chosen.get("coverage_date")) > str(other.get("coverage_date"))
        )
        if worse_somewhere:
            return f"{_short(chosen['option'])} is better than {_short(other['option'])} on every figure."
    other = others[0]
    return f"{_short(other['option'])} has a lower {measure} than {_short(chosen['option'])}."


def false_comparison(events: Events) -> tuple[Events, str]:
    out = copy.deepcopy(events)
    proposal = _proposal(out)
    sentence = false_statement(_ranked(out), proposal.get("recommended", ""))
    text = str(proposal.get("rationale_text") or "")
    first, sep, rest = text.partition("\n")
    proposal["rationale_text"] = f"{first} {sentence}{sep}{rest}"
    return out, sentence


STRUCTURAL: dict[str, Callable[[Events], Events]] = {
    "no_rank": no_rank,
    "read_after_write": read_after_write,
    "repeated_refusal": repeated_refusal,
    "wrong_id": wrong_id,
}


def write_trace(path: Path, events: Events) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def plant(runs_root: Path, out_dir: Path) -> dict[str, list[dict[str, Any]]]:
    """Write every planted copy under ``out_dir/<kind>/<run>/trace.jsonl``; for each kind, the rows of its
    copies (source, path, and for false comparisons the planted sentence)."""
    evaluate = sound_sources(runs_root / EVALUATE_SOURCE, "evaluate", 12)
    prepare = sound_sources(runs_root / PREPARE_SOURCE, "prepare", 9)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Marked, so that no upload or count takes the copies for runs (snm_evals.ops.is_synthetic).
    (out_dir / "PLANTED").write_text("Planted copies of sound runs, for measuring the checks; not runs.\n")
    planted: dict[str, list[dict[str, Any]]] = {}
    jobs: list[tuple[str, Path, Callable[[Events], Any]]] = [
        *((kind, source, fn) for kind, fn in STRUCTURAL.items() for source in evaluate),
        *(("wrong_write", source, wrong_write) for source in prepare),
        *(("false_comparison", source, false_comparison) for source in evaluate),
    ]
    for kind, source, plant_one in jobs:
        made = plant_one(read_events(source))
        events, sentence = made if isinstance(made, tuple) else (made, None)
        target = out_dir / kind / source.parent.name / "trace.jsonl"
        write_trace(target, events)
        row = {"kind": kind, "source": str(source.relative_to(runs_root)), "trace": str(target)}
        if sentence:
            row["sentence"] = sentence
        planted.setdefault(kind, []).append(row)
    return planted


def catch_rates(planted: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """For each structural kind: copies, how many fail the targeted check, and the checks that fired."""
    rates = {}
    for kind, check in TARGET.items():
        rows = planted.get(kind, [])
        caught, fired = 0, {}
        for row in rows:
            report = check_trace(read_events(Path(row["trace"])))
            failed = {r.check for r in report.failed()}
            caught += check in failed
            for number in failed:
                fired[number] = fired.get(number, 0) + 1
        rates[kind] = {
            "check": check,
            "copies": len(rows),
            "caught": caught,
            "fired": dict(sorted(fired.items())),
        }
    sound_text = planted.get("false_comparison", [])
    rates["false_comparison"] = {
        "check": None,
        "copies": len(sound_text),
        "caught": sum(not check_trace(read_events(Path(r["trace"]))).sound for r in sound_text),
        "fired": {},
    }
    return rates
