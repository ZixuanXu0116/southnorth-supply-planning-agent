"""Trace checks in code: the rules of a sound planner run, checked on every trace, the same every time.

    uv run snm-evals trace-checks --batch <batch> [--batch ...]

Each check reads the list of calls rebuilt from the run's ``trace.jsonl`` and gives ``pass``, ``fail``, or
``na`` (it does not apply, or the trace does not hold what it needs):

1. Read before writing: a successful ``get_case`` or ``explain_case`` in a turn before the first write.
2. Rank before recommending (evaluate): a successful ``rank_options`` in a turn before
   ``propose_recommendation``.
3. Cited ids traced (evaluate): every ``evaluation_id`` of the recommendation appeared, for the same option,
   in an earlier ``rank_options`` or ``evaluate_option`` result, and every id its text cites appeared there.
4. One write, last and alone: exactly one accepted write; it is the last call; its turn has no other call.
5. No identical retry: after a refused call, the next call to the same tool has other arguments.
6. The prepare write fits the case: transfer or move to the overflow hub -> ``prepare_transfer``; expedite of
   an open order -> an expedite request; new or expedited new order -> a quote request; no lever -> a note.
   The exception of the planner's prompt: a note when another lever covers on the same or an earlier date,
   with no more days below zero and a lower total cost, by the run's own evaluations.
7. Every figure traced (evaluate): the harness's figure check of the accepted recommendation, as the trace
   records it (``figure_check``, or the verifier's ``verification``); ``na`` for runs without one.
9. Ended within budget and without error.

The trace keeps the first 4,000 characters of each result, so ids and figures past that point cannot be
re-read: check 3 fails an id only when the visible results contradict it, and check 7 reads the check the
harness made on the full results instead of recomputing it. Rule 8 of the first plan (no repeated identical
reads) is left out: some inputs are read twice for good reason (decided 2026-11-05).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CHECKS = {
    1: "read before writing",
    2: "rank before recommending",
    3: "cited ids traced",
    4: "one write, last and alone",
    5: "no identical retry",
    6: "prepare write fits the case",
    7: "every figure traced",
    9: "ended within budget and without error",
}
WRITE_TOOLS = frozenset(
    {"draft_supplier_request", "prepare_transfer", "propose_recommendation", "draft_handoff", "add_note"}
)
CASE_READS = frozenset({"get_case", "explain_case"})
RESULT_LIMIT = 4_000  # what the harness keeps of a result
EVALUATION_ID = re.compile(r"\bE-[0-9a-f]{12}\b")
ID_AND_OPTION = re.compile(r'"evaluation_id":\s*"(E-[0-9a-f]{12})"[^{}]*?"option":\s*"([^"]+)"')
MOVE_LEVERS = frozenset({"transfer", "move_to_overflow"})
ORDER_LEVERS = frozenset({"expedite", "new_order", "expedited_new_order"})


@dataclass
class Call:
    turn: int
    index: int
    name: str
    arguments: dict[str, Any]
    ok: bool | None = None  # None: no result in the trace
    output: str = ""

    @property
    def truncated(self) -> bool:
        return len(self.output) >= RESULT_LIMIT - 1 or "more characters]" in self.output[-40:]

    def parsed(self) -> Any:
        try:
            return json.loads(self.output)
        except (json.JSONDecodeError, TypeError):
            return None


@dataclass(frozen=True)
class Result:
    check: int
    status: str  # pass | fail | na
    detail: str = ""


@dataclass
class TraceReport:
    path: str
    task: str
    case_id: str
    results: list[Result] = field(default_factory=list)

    @property
    def sound(self) -> bool:
        return all(r.status != "fail" for r in self.results)

    def failed(self) -> list[Result]:
        return [r for r in self.results if r.status == "fail"]


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def calls_of(events: list[dict[str, Any]]) -> list[Call]:
    """The run's tool calls in order, each with its result; tool events without an id match by name."""
    calls: list[Call] = []
    by_id: dict[str, Call] = {}
    for event in events:
        if event.get("event") == "assistant":
            for call in event.get("tool_calls") or []:
                item = Call(
                    int(event.get("turn") or 0), len(calls), call["name"], call.get("arguments") or {}
                )
                calls.append(item)
                if call.get("id"):
                    by_id[call["id"]] = item
        elif event.get("event") == "tool":
            target = by_id.get(event.get("id") or "") or next(
                (c for c in calls if c.name == event.get("name") and c.ok is None), None
            )
            if target is not None:
                target.ok = not event.get("is_error")
                target.output = (
                    event.get("output")
                    if isinstance(event.get("output"), str)
                    else json.dumps(event.get("output"))
                )
    return calls


def _first(calls: Iterable[Call], names: Iterable[str]) -> Call | None:
    wanted = set(names)
    return next((c for c in calls if c.name in wanted), None)


def check_read_before_write(calls: list[Call]) -> Result:
    first = _first(calls, WRITE_TOOLS)
    if first is None:
        return Result(1, "na", "no write")
    if any(c.name in CASE_READS and c.ok and c.turn < first.turn for c in calls):
        return Result(1, "pass")
    return Result(
        1, "fail", f"{first.name} in turn {first.turn} with no successful get_case or explain_case before it"
    )


def check_rank_before_recommend(calls: list[Call], task: str) -> Result:
    if task != "evaluate":
        return Result(2, "na", "not an evaluate task")
    proposal = _first(calls, ["propose_recommendation"])
    if proposal is None:
        return Result(2, "na", "no recommendation")
    if any(c.name == "rank_options" and c.ok and c.turn < proposal.turn for c in calls):
        return Result(2, "pass")
    return Result(
        2, "fail", f"propose_recommendation in turn {proposal.turn} with no successful rank_options before it"
    )


def _write(calls: list[Call], names: Iterable[str]) -> Call | None:
    """The accepted write among ``names``, else the last attempt."""
    attempts = [c for c in calls if c.name in set(names)]
    return next((c for c in attempts if c.ok), attempts[-1] if attempts else None)


def check_cited_ids(calls: list[Call], task: str) -> Result:
    if task != "evaluate":
        return Result(3, "na", "not an evaluate task")
    proposal = _write(calls, ["propose_recommendation"])
    if proposal is None:
        return Result(3, "na", "no recommendation")
    sources = [
        c
        for c in calls
        if c.name in ("rank_options", "evaluate_option") and c.ok and c.index < proposal.index
    ]
    pairs = {(eid, option) for c in sources for eid, option in ID_AND_OPTION.findall(c.output)}
    seen_ids = {eid for c in sources for eid in EVALUATION_ID.findall(c.output)}
    seen_options = {option for _, option in pairs}
    partial = any(c.truncated for c in sources)
    problems = []
    for item in proposal.arguments.get("options") or []:
        eid, option = item.get("evaluation_id"), item.get("option")
        if (eid, option) in pairs:
            continue
        if eid in seen_ids or option in seen_options or not partial:
            problems.append(f"{option} cites {eid}")
    text = str(proposal.arguments.get("rationale_text") or "")
    for eid in sorted(set(EVALUATION_ID.findall(text)) - seen_ids):
        if not partial:
            problems.append(f"the text cites {eid}")
    if problems:
        return Result(3, "fail", "; ".join(problems) + " (not in the earlier results)")
    return Result(3, "pass", "results partly cut in the trace" if partial else "")


def check_one_write(calls: list[Call]) -> Result:
    accepted = [c for c in calls if c.name in WRITE_TOOLS and c.ok]
    if len(accepted) != 1:
        return Result(4, "fail", f"{len(accepted)} accepted writes")
    write = accepted[0]
    if write is not calls[-1]:
        return Result(4, "fail", f"{write.name} is followed by {calls[-1].name}")
    others = [c.name for c in calls if c.turn == write.turn and c is not write]
    if others:
        return Result(4, "fail", f"{write.name} shares turn {write.turn} with {', '.join(others)}")
    return Result(4, "pass")


def check_no_identical_retry(calls: list[Call]) -> Result:
    refused = [c for c in calls if c.ok is False]
    if not refused:
        return Result(5, "na", "no refused call")
    for call in refused:
        retry = next((c for c in calls if c.index > call.index and c.name == call.name), None)
        if retry is not None and retry.arguments == call.arguments:
            return Result(5, "fail", f"{call.name} repeated unchanged in turn {retry.turn} after its refusal")
    return Result(5, "pass")


def engine_lever(text: str) -> tuple[str | None, bool]:
    """The engine's own ``lever`` in a case read (not an alternative's), and whether the text shows it: a
    scan of the ``engine`` object at its first level, which works on a result cut short."""
    opening = re.search(r'"engine":\s*\{', text)
    if opening is None:
        return None, False
    depth, in_string, escaped = 1, False, False
    i = opening.end()
    while i < len(text):
        char = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            if depth == 1 and (match := re.match(r'"lever":\s*(?:"([a-z_]+)"|null)', text[i:])):
                return match.group(1), True
            in_string = True
        elif char in "{[":
            depth += 1
        elif char in "}]":
            depth -= 1
            if depth == 0:
                return None, True
        i += 1
    return None, False


def _expected_write(lever: str | None) -> tuple[str, str | None]:
    if lever in MOVE_LEVERS:
        return "prepare_transfer", None
    if lever == "expedite":
        return "draft_supplier_request", "expedite"
    if lever in ORDER_LEVERS:
        return "draft_supplier_request", "quote"
    return "add_note", None


def _evaluations(calls: list[Call]) -> list[dict[str, Any]]:
    found = []
    for call in calls:
        if call.name == "evaluate_option" and call.ok and isinstance(parsed := call.parsed(), dict):
            found.append(parsed)
    return found


def _note_is_the_exception(calls: list[Call], lever: str) -> bool:
    """Another lever covers on the same or an earlier date, with no more days below zero and a lower total
    cost, by the run's own evaluations (the planner prompt's one exception)."""
    evaluations = _evaluations(calls)
    engine = next((e for e in evaluations if lever in (e.get("option"), e.get("kind"))), None)
    if engine is None or not engine.get("coverage_date"):
        return False
    for other in evaluations:
        if other is engine or not other.get("coverage_date") or not other.get("covers_gap", True):
            continue
        if (
            other["coverage_date"] <= engine["coverage_date"]
            and (other.get("days_below_zero") or 0) <= (engine.get("days_below_zero") or 0)
            and float(other.get("total_cost") or 0) < float(engine.get("total_cost") or 0)
        ):
            return True
    return False


def check_prepare_write(calls: list[Call], task: str) -> Result:
    if task != "prepare":
        return Result(6, "na", "not a prepare task")
    write = _write(calls, WRITE_TOOLS)
    if write is None:
        return Result(6, "na", "no write")
    shown = [engine_lever(c.output) for c in calls if c.name in CASE_READS and c.ok]
    found = next((lever for lever, seen in shown if seen), "missing")
    if found == "missing":
        return Result(6, "na", "the engine's lever is not in the trace")
    lever = found
    tool, request_type = _expected_write(lever)
    wrote_type = write.arguments.get("request_type")
    if write.name == tool and (request_type is None or wrote_type == request_type):
        return Result(6, "pass")
    if write.name == "add_note" and lever in ORDER_LEVERS and _note_is_the_exception(calls, lever):
        return Result(6, "pass", "a note: another lever covers as early, cheaper")
    wrote = f"{write.name}" + (f" ({wrote_type})" if wrote_type else "")
    wanted = f"{tool}" + (f" ({request_type})" if request_type else "")
    return Result(6, "fail", f"the engine's lever is {lever or 'none'}: expected {wanted}, wrote {wrote}")


def check_figures(events: list[dict[str, Any]], calls: list[Call], task: str) -> Result:
    if task != "evaluate":
        return Result(7, "na", "not an evaluate task")
    checks = [e for e in events if e.get("event") == "figure_check" and "untraced_figures" in e]
    if not checks:
        return Result(7, "na", "no figure check recorded in this run")
    proposal = _write(calls, ["propose_recommendation"])
    if proposal is None or not proposal.ok:
        return Result(7, "na", "no accepted recommendation")
    untraced = checks[-1].get("untraced_figures") or []
    if untraced:
        return Result(7, "fail", f"untraced figures: {', '.join(map(str, untraced))}")
    return Result(7, "pass")


def check_ended(events: list[dict[str, Any]]) -> Result:
    end = next((e for e in reversed(events) if e.get("event") == "run_end"), None)
    if end is None:
        return Result(9, "fail", "no run_end")
    status = end.get("status")
    if status in ("error", "budget_exhausted"):
        return Result(9, "fail", f"status {status}: {str(end.get('error') or '')[:120]}")
    return Result(9, "pass", str(status))


def check_trace(events: list[dict[str, Any]], path: str = "") -> TraceReport:
    start = next((e for e in events if e.get("event") == "run_start"), {})
    task = str(start.get("task") or "")
    calls = calls_of(events)
    report = TraceReport(path, task, str(start.get("case_id") or ""))
    report.results = [
        check_read_before_write(calls),
        check_rank_before_recommend(calls, task),
        check_cited_ids(calls, task),
        check_one_write(calls),
        check_no_identical_retry(calls),
        check_prepare_write(calls, task),
        check_figures(events, calls, task),
        check_ended(events),
    ]
    return report


def planner_traces(batch_dir: Path) -> list[Path]:
    """The planner runs of a batch: traces whose run_start names a task."""
    found = []
    for path in sorted(batch_dir.rglob("trace.jsonl")):
        first = path.read_text(encoding="utf-8").split("\n", 1)[0]
        try:
            if json.loads(first).get("task"):
                found.append(path)
        except json.JSONDecodeError:
            continue
    return found


def check_batch(batch_dir: Path, root: Path | None = None) -> list[TraceReport]:
    base = root or batch_dir.parent
    return [check_trace(read_events(path), str(path.relative_to(base))) for path in planner_traces(batch_dir)]


def summary(reports: list[TraceReport]) -> dict[int, Counter]:
    counts: dict[int, Counter] = {number: Counter() for number in CHECKS}
    for report in reports:
        for result in report.results:
            counts[result.check][result.status] += 1
    return counts


def render(reports: list[TraceReport], title: str) -> str:
    counts = summary(reports)
    sound = sum(r.sound for r in reports)
    lines = [
        f"## {title}",
        "",
        f"{len(reports)} traces; {sound} pass every check that applies.",
        "",
        "| Check | Pass | Fail | Does not apply |",
        "|---|---|---|---|",
    ]
    for number, name in CHECKS.items():
        c = counts[number]
        lines.append(f"| {number}. {name} | {c['pass']} | {c['fail']} | {c['na']} |")
    failures = [(r, f) for r in reports for f in r.failed()]
    lines += ["", "Failures:" if failures else "No failure.", ""]
    for report, result in failures:
        lines.append(
            f"- `{report.path}` ({report.task} {report.case_id}), check {result.check}: {result.detail}"
        )
    return "\n".join(lines) + "\n"
