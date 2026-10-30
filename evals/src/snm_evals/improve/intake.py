"""Cards from answers the owner flagged on the question page (``apps/ask``; decision record 009, loop step 1).

A flagged answer has no reference answer, so its card waits in ``needs case`` until a regression case for it
is written and reviewed (``EVAL_GUIDE.md`` §8); then a batch that runs the case feeds the loop like any
failure.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from ..run import REPO_ROOT
from .excerpt import excerpt, read_trace
from .loop import card_dir

ASK_RUNS = REPO_ROOT / "runs" / "ask"


def flagged_turns(path: Path) -> list[dict[str, Any]]:
    """Flag entries from ``flags.jsonl`` or from one downloaded thread (its flagged turns)."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    thread = json.loads(text)
    entries = []
    for index, turn in enumerate(thread.get("turns", [])):
        if not turn.get("flag"):
            continue
        entries.append(
            {
                "at": turn["flag"].get("at"),
                "thread": thread["id"],
                "turn": index,
                "role": thread.get("role"),
                "as_of": thread.get("as_of"),
                "question": turn.get("question"),
                "earlier": [earlier.get("question") for earlier in thread["turns"][:index]],
                "run_id": (turn.get("result") or {}).get("run_id"),
                "note": turn["flag"].get("note", ""),
                "result": turn.get("result"),
            }
        )
    return entries


def render_flag_card(entry: dict[str, Any], today: date, runs_dir: Path = ASK_RUNS) -> str:
    run_id = entry.get("run_id") or ""
    trace = runs_dir / run_id / "trace.jsonl"
    decisive = (
        excerpt(read_trace(trace))
        if run_id and trace.is_file()
        else f"The trace is on the machine that asked: `runs/ask/{run_id}/trace.jsonl`."
    )
    answer = ((entry.get("result") or {}).get("answer") or {}).get("answer", "")
    earlier = "".join(f"\n- **Earlier question:** {question}" for question in entry.get("earlier") or [])
    observed = " ".join(answer.split())[:600] or "See the trace."
    return f"""# Failure card: ask / {entry["thread"]}-t{int(entry["turn"]) + 1}

- **Status:** needs case
- **Source:** flagged on the question page on {entry.get("at")}; drafted by `snm-evals improve intake`
  on {today}
- **Run:** `runs/ask/{run_id}/` (role `{entry.get("role")}`, as of {entry.get("as_of")})
- **Question:** {entry.get("question")}{earlier}

## Expected

The owner's note: "{entry.get("note") or "(no note)"}". A regression case (question, role, expected behavior,
reference SQL) is written and reviewed before the loop can screen a fix (`EVAL_GUIDE.md` §8).

## Observed

{observed}

## Decisive part of the trace

{decisive}

## Root cause

Pending the regression case.

## Fix hypothesis

Pending the regression case.

## Affected categories

Pending.

## Owner review

- Case review: pending

## Measurement

Pending.

## Decision

Pending.
"""


def intake_flags(path: Path, today: date | None = None, runs_dir: Path = ASK_RUNS) -> list[str]:
    today = today or date.today()
    ids = []
    for entry in flagged_turns(path):
        card_id = f"ask-{entry['thread']}-t{int(entry['turn']) + 1}"
        folder = card_dir(card_id)
        if (folder / "card.md").exists():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "card.md").write_text(render_flag_card(entry, today, runs_dir), encoding="utf-8")
        (folder / "flag.json").write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
        ids.append(card_id)
    return ids
