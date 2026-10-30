"""A failure card drafted from a failing run (``evals/failures/TEMPLATE.md``, every mechanical section
filled), and the proposer's input for it (decision record 009, loop steps 2 and 3)."""

from __future__ import annotations

from datetime import date
from typing import Any

from .collect import Failure
from .excerpt import excerpt, kb_pages_read, read_trace

MAX_REFERENCE_ROWS = 20


def _number(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{number:,.0f}" if number == int(number) and abs(number) >= 1000 else f"{number:g}"


def reference_summary(failure: Failure, answers: dict[str, Any]) -> str:
    """The expected behavior and, for answering cases, the reference values and mentions in one line."""
    case = failure.case
    behaviors = " or ".join(f"`{behavior}`" for behavior in case.acceptable_behaviors)
    reference = (answers.get("results") or {}).get(case.id, {}).get("primary")
    if case.answer is None or not reference:
        return behaviors
    columns, rows = reference["columns"], reference["rows"]
    keys, values = case.answer.keys, case.answer.values
    shown = []
    for row in rows[:5]:
        cells = [f"{key} {row[columns.index(key)]}" for key in keys if key in columns]
        tolerance = {value.column: value for value in values}
        for value in values:
            if value.column in columns:
                spec = tolerance[value.column]
                bound = f"abs {spec.abs:g}" if spec.abs is not None else f"rel {100 * (spec.rel or 0):g} %"
                cells.append(f"`{value.column}` = {_number(row[columns.index(value.column)])} ({bound})")
        shown.append(", ".join(cells))
    more = f"; {len(rows) - 5} more rows" if len(rows) > 5 else ""
    mentions = (
        "; must mention " + "; ".join(" | ".join(group) for group in case.answer.must_mention)
        if case.answer.must_mention
        else ""
    )
    return f"{behaviors} with " + "; ".join(shown) + more + mentions


def observed_summary(failure: Failure) -> str:
    score = failure.score
    answer = failure.result.get("answer") or {}
    parts = [f"`{score.behavior or 'no submission'}` ({failure.result.get('status')})"]
    if not score.behavior_ok:
        parts.append("behavior not acceptable")
    if score.missing:
        parts.append("the scorer reports missing: " + "; ".join(score.missing))
    if not score.policy_ok:
        parts.append("operator identity leaked")
    if failure.runs > 1:
        parts.append(f"{failure.failed_runs} of {failure.runs} runs failed")
    text = " ".join(str(answer.get("answer", "")).split())
    if text:
        parts.append(f'answer begins "{text[:300]}{"…" if len(text) > 300 else ""}"')
    return "; ".join(parts) + "."


def render_card(failure: Failure, suite_name: str, answers: dict[str, Any], today: date) -> str:
    case, result = failure.case, failure.result
    prompt = result.get("prompt") or {}
    model = str(result.get("model", "")).split("/")[-1]
    trace = failure.run_dir / "trace.jsonl"
    decisive = excerpt(read_trace(trace)) if trace.is_file() else "(trace not found)"
    traps = ", ".join(f"`{tag}`" for tag in case.trap_tags) or "none"
    earlier = (
        "\n- **Earlier cards for this case:** "
        + "; ".join(f"`{card.card_id}` ({card.status})" for card in failure.earlier_cards)
        if failure.earlier_cards
        else ""
    )
    return f"""# Failure card: {failure.batch} / {case.id}

- **Status:** open
- **Batch, arm, repetition:** `{failure.batch}`, `{failure.score.arm}`, rep {failure.score.rep}
- **Prompt version and digest:** `{prompt.get("name")}/{prompt.get("version")}`, `{prompt.get("digest")}`
- **Model and effort:** `{model}`, effort `{result.get("effort")}` (run manifest)
- **Trace:** `runs/evals/{failure.batch}/runs/{failure.score.run_id}/trace.jsonl`
- **Suite and category:** `{suite_name}`, `{case.category}`, traps: {traps}
- **Drafted by:** `snm-evals improve collect` on {today.isoformat()} (decision record 009){earlier}

## Expected

{reference_summary(failure, answers)}

## Observed

{observed_summary(failure)}

## Decisive part of the trace

{decisive}

## Root cause

To be proposed.

## Fix hypothesis

To be proposed.

## Affected categories

Chosen by the screen.

## Owner review

- Hypothesis review: pending
- Diff review: pending

## Measurement

Pending.

## Decision

Pending.
"""


def proposer_input(failure: Failure, answers: dict[str, Any], card: str) -> dict[str, Any]:
    """What the proposer reads (owner question 2 of record 009: the whole case, reference included)."""
    case, result = failure.case, failure.result
    trace = failure.run_dir / "trace.jsonl"
    events = read_trace(trace) if trace.is_file() else []
    reference = (answers.get("results") or {}).get(case.id, {}).get("primary")
    spec = case.answer
    return {
        "card_id": failure.card_id,
        "card": card,
        "case": {
            "question": case.question,
            "earlier_questions": [turn.question for turn in case.turns],
            "role": case.role,
            "as_of": result.get("as_of"),
            "category": case.category,
            "trap_tags": case.trap_tags,
            "expected_behavior": case.expected_behavior,
            "acceptable_behaviors": case.acceptable_behaviors,
            "required_kb_docs": case.required_kb_docs,
            "required_tables": case.required_tables,
            "notes": " ".join(case.notes.split()),
            "reference_sql": case.reference_sql,
            "reference": (
                {
                    "columns": reference["columns"],
                    "rows": reference["rows"][:MAX_REFERENCE_ROWS],
                    "rows_total": len(reference["rows"]),
                    "keys": spec.keys,
                    "values": [value.model_dump(exclude_none=True) for value in spec.values],
                    "must_mention": spec.must_mention,
                }
                if reference and spec
                else None
            ),
        },
        "observed": {
            "behavior": failure.score.behavior,
            "answer": (result.get("answer") or {}).get("answer", ""),
            "status": result.get("status"),
            "behavior_ok": failure.score.behavior_ok,
            "values_ok": failure.score.values_ok,
            "missing": failure.score.missing,
        },
        "kb_pages_read": kb_pages_read(events),
        "prompt": result.get("prompt"),
        "earlier_cards": [
            {"card": card_ref.card_id, "status": card_ref.status, "decision": card_ref.decision}
            for card_ref in failure.earlier_cards
        ],
    }
