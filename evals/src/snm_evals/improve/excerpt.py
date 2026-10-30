"""The decisive part of a run's trace, cut out deterministically for a failure card.

Per turn: the first lines of the thinking and text, every tool call with short arguments, and the shape of
each result (columns, row count, first rows; or the first characters of a page). The submission is kept
whole.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

THINKING_CHARS = 400
ARGUMENT_CHARS = 200
OUTPUT_CHARS = 160
PREVIEW_ROWS = 3
ANSWER_CHARS = 3000


def read_trace(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _clip(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _arguments(name: str, arguments: Any) -> str:
    if name == "submit_answer":
        return f"behavior={(arguments or {}).get('behavior')}"
    if not isinstance(arguments, dict):
        return _clip(json.dumps(arguments), ARGUMENT_CHARS)
    parts = [f"{key}={json.dumps(value, separators=(',', ':'))}" for key, value in arguments.items()]
    return _clip(", ".join(parts), ARGUMENT_CHARS)


def result_shape(output: str) -> str:
    """A tool result in one line: a result handle's columns, rows, and preview; else the first characters."""
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        lines = [line for line in str(output).splitlines() if line.strip() and line.strip() != "---"]
        first = next((line for line in lines if line.startswith(("id:", "#"))), lines[0] if lines else "")
        return f"{len(str(output)):,} chars, {_clip(first, OUTPUT_CHARS)}"
    if isinstance(payload, dict) and "result_id" in payload:
        preview = payload.get("preview") or []
        rows = "; ".join(json.dumps(row, separators=(",", ":")) for row in preview[:PREVIEW_ROWS])
        more = f" (+{len(preview) - PREVIEW_ROWS} more previewed)" if len(preview) > PREVIEW_ROWS else ""
        return (
            f"{payload['result_id']}: columns {payload.get('columns')}, {payload.get('row_count')} rows"
            f"{', truncated' if payload.get('truncated') else ''}; {_clip(rows, 400)}{more}"
        )
    return _clip(json.dumps(payload, separators=(",", ":")), OUTPUT_CHARS)


def kb_pages_read(events: list[dict[str, Any]]) -> list[str]:
    """Knowledge-base pages the run read, in order, without repeats (paths relative to ``kb/``)."""
    pages: list[str] = []
    for event in events:
        if event.get("event") != "assistant":
            continue
        for call in event.get("tool_calls") or []:
            if call.get("name") == "kb_read":
                path = str((call.get("arguments") or {}).get("path", "")).strip().removeprefix("kb/")
                if path and path not in pages:
                    pages.append(path)
    return pages


def excerpt(events: list[dict[str, Any]]) -> str:
    """Markdown bullets, one per turn, ending with the submission(s)."""
    outputs = {event.get("id"): event for event in events if event.get("event") == "tool"}
    lines: list[str] = []
    submissions: list[str] = []
    for event in events:
        kind = event.get("event")
        if kind == "assistant":
            parts = []
            if event.get("thinking"):
                parts.append(f'thinking "{_clip(event["thinking"], THINKING_CHARS)}"')
            if event.get("text"):
                parts.append(f'text "{_clip(event["text"], THINKING_CHARS)}"')
            for call in event.get("tool_calls") or []:
                name = call.get("name", "?")
                arguments = call.get("arguments") or {}
                result = outputs.get(call.get("id"))
                shown = f"`{name}`({_arguments(name, arguments)})"
                if result is not None and name != "submit_answer":
                    if result.get("is_error"):
                        shown += f" → error: {_clip(result.get('output', ''), OUTPUT_CHARS)}"
                    else:
                        shown += f" → {result_shape(result.get('output', ''))}"
                parts.append(shown)
                if name == "submit_answer":
                    answer = _clip(arguments.get("answer", ""), ANSWER_CHARS)
                    submissions.append(f"- Submission ({arguments.get('behavior')}): {answer}")
            lines.append(f"- Turn {event.get('turn')}: " + "; ".join(parts))
        elif kind == "faithfulness" and event.get("rejected"):
            lines.append(
                f"- Faithfulness gate rejected submission {event.get('submission')}: "
                f"untraced {event.get('untraced')}"
            )
        elif kind == "repair":
            lines.append(f"- Repair prompt ({event.get('reason')})")
        elif kind == "question_end":
            lines.append(f"- End of question {int(event.get('index', 0)) + 1} ({event.get('status')})")
    return "\n".join(lines + submissions)
