"""Trace soundness: a sample of planner runs graded by an LLM judge on how they reached their result.

    uv run snm-evals trace-judge --batches <batch> [...] --sample 12 --batch trace-judge-<date> [--rubric v2]
    uv run snm-evals trace-judge --rubric v2 --planted --batch trace-judge-v2-<date>

Planner traces (``trace.jsonl`` with a ``task``) are collected from the given batches under runs/evals/, a
seeded sample is drawn, and each is graded by the harness's judge (``harness/dist/cli/judge-trace.js``):
sound, unsound, or unclear, with every step or question it checked. Rubric v1 (``prompts/trace-judge/v1.md``)
checks every step of a sound run; rubric v2 (``prompts/trace-judge/v2.md``) only what needs judgment (the real
reason, omissions, comparisons, an actionable note), since the structural steps are checked in code
(``trace_checks``). ``--planted`` tests the judge itself: planted false comparisons, and sound runs judged
twice. The report lists the share of sound runs and every failed step, for a person to read.
"""

from __future__ import annotations

import json
import os
import random
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .run import RUNS_ROOT

REPO = Path(__file__).resolve().parents[3]
JUDGE_CLI = REPO / "harness" / "dist" / "cli" / "judge-trace.js"


def planner_traces(batches: list[str], root: Path = RUNS_ROOT) -> list[Path]:
    """The traces of planner runs (their run_start names a task) in the batches, in a stable order."""
    traces = []
    for batch in batches:
        for path in sorted((root / batch).rglob("trace.jsonl")):
            first = path.read_text(encoding="utf-8").split("\n", 1)[0]
            try:
                start = json.loads(first)
            except json.JSONDecodeError:
                continue
            if start.get("event") == "run_start" and start.get("task"):
                traces.append(path)
    return traces


def sample(traces: list[Path], size: int, seed: int) -> list[Path]:
    if size >= len(traces):
        return list(traces)
    return sorted(random.Random(seed).sample(traces, size))


def judge(trace: Path, rubric: str = "v1") -> dict[str, Any]:
    env = {**os.environ, "NODE_USE_ENV_PROXY": "1", "NODE_NO_WARNINGS": "1"}
    completed = subprocess.run(
        ["node", str(JUDGE_CLI), "--trace", str(trace), "--rubric", rubric, "--json"],
        capture_output=True, text=True, env=env, timeout=300, check=False,
    )  # fmt: skip
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"error": (completed.stderr or completed.stdout)[-400:], "usage": {"cost_usd": 0.0}}


def run_trace_judge(
    batches: list[str], size: int, seed: int, out_dir: Path, parallel: int = 4, rubric: str = "v1"
) -> list[dict]:
    chosen = sample(planner_traces(batches), size, seed)
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        results = list(pool.map(lambda trace: judge(trace, rubric), chosen))
    rows = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for trace, result in zip(chosen, results, strict=True):
        start = json.loads(trace.read_text(encoding="utf-8").split("\n", 1)[0])
        judgment = result.get("judgment") or {}
        rows.append({
            "trace": str(trace.relative_to(RUNS_ROOT)) if trace.is_relative_to(RUNS_ROOT) else str(trace),
            "task": start.get("task"),
            "case_id": start.get("case_id"),
            "verdict": judgment.get("verdict", "error"),
            "failed_steps": [s for s in judgment.get("steps", []) if not s.get("ok")],
            "summary": judgment.get("summary", result.get("error", "")),
            "cost_usd": float((result.get("usage") or {}).get("cost_usd") or 0.0),
            "judge": result.get("model"),
        })  # fmt: skip
    (out_dir / "judgments.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return rows


def render(rows: list[dict], batches: list[str], seed: int, rubric: str = "v1") -> str:
    count = {
        verdict: sum(1 for r in rows if r["verdict"] == verdict)
        for verdict in ("sound", "unsound", "unclear")
    }
    errors = sum(1 for r in rows if r["verdict"] == "error")
    lines = [
        "# Trace soundness",
        "",
        f"A sample of {len(rows)} planner runs (seed {seed}) from {', '.join(f'`{b}`' for b in batches)}, "
        f"each graded by an LLM judge against `prompts/trace-judge/{rubric}.md`; the answer itself is "
        "graded elsewhere.",
        "",
        "| Sound | Unsound | Unclear | Judge errors | Judge cost |",
        "|---|---|---|---|---|",
        f"| {count['sound']} | {count['unsound']} | {count['unclear']} | {errors} "
        f"| {sum(r['cost_usd'] for r in rows):.2f} USD |",
        "",
        "## Runs not judged sound (for a person to read)",
        "",
    ]
    for row in rows:
        if row["verdict"] == "sound":
            continue
        lines.append(
            f"- **{row['task']} {row['case_id']}** ({row['verdict']}), `{row['trace']}`: {row['summary']}"
        )
        for step in row["failed_steps"]:
            lines.append(f"  - {step.get('step')}: {step.get('evidence')}")
    if all(row["verdict"] == "sound" for row in rows):
        lines.append("None.")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------ rubric v2: the judge itself tested


def _verdict(result: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
    judgment = result.get("judgment") or {}
    return (
        judgment.get("verdict", "error"),
        judgment.get("steps", []),
        judgment.get("summary", result.get("error", "")),
    )


def run_judge_v2_tests(
    planted: dict[str, list[dict[str, Any]]], out_dir: Path, parallel: int = 4, sound: bool = True
) -> list[dict]:
    """Rubric v2 on the planted false comparisons (each once) and on their sound sources (each twice: the
    consistency test, and the false alarms on sound runs). One row per judgment, in ``judgments.jsonl``."""
    comparisons = planted["false_comparison"]
    jobs = [("false_comparison", Path(r["trace"]), r) for r in comparisons]
    if sound:
        jobs += [(f"sound_{n}", RUNS_ROOT / r["source"], r) for n in (1, 2) for r in comparisons]
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        results = list(pool.map(lambda job: judge(job[1], "v2"), jobs))
    rows = []
    for (kind, _trace, row), result in zip(jobs, results, strict=True):
        verdict, steps, summary = _verdict(result)
        rows.append({
            "kind": kind,
            "source": row["source"],
            "sentence": row.get("sentence") if kind == "false_comparison" else None,
            "verdict": verdict,
            "comparisons_ok": next((s.get("ok") for s in steps[2:3]), None),
            "failed_steps": [s for s in steps if not s.get("ok")],
            "summary": summary,
            "cost_usd": float((result.get("usage") or {}).get("cost_usd") or 0.0),
            "judge": result.get("model"),
        })  # fmt: skip
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "judgments.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return rows


def judge_v2_summary(rows: list[dict]) -> dict[str, Any]:
    planted = [r for r in rows if r["kind"] == "false_comparison"]
    first = {r["source"]: r["verdict"] for r in rows if r["kind"] == "sound_1"}
    second = {r["source"]: r["verdict"] for r in rows if r["kind"] == "sound_2"}
    both = [s for s in first if s in second]
    return {
        "planted": len(planted),
        "caught": sum(r["verdict"] == "unsound" for r in planted),
        "caught_on_comparisons": sum(
            r["verdict"] == "unsound" and r["comparisons_ok"] is False for r in planted
        ),
        "misses": [r for r in planted if r["verdict"] != "unsound"],
        "consistent": sum(first[s] == second[s] for s in both),
        "pairs": len(both),
        "false_alarms": sum(r["verdict"] == "unsound" for r in rows if r["kind"].startswith("sound_")),
        "sound_judgments": sum(r["kind"].startswith("sound_") for r in rows),
        "errors": sum(r["verdict"] == "error" for r in rows),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
    }
