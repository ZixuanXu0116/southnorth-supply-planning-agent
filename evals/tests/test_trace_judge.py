"""Trace soundness: collecting and sampling planner traces, and the report."""

from __future__ import annotations

import json

from snm_evals.trace_judge import planner_traces, render, sample


def test_planner_traces_are_collected_and_sampled_reproducibly(tmp_path):
    for index in range(5):
        run = tmp_path / "batch" / f"run-{index}"
        run.mkdir(parents=True)
        (run / "trace.jsonl").write_text(json.dumps({"event": "run_start", "task": "evaluate"}) + "\n")
    other = tmp_path / "batch" / "data-agent"
    other.mkdir()
    (other / "trace.jsonl").write_text(json.dumps({"event": "run_start", "question": "q"}) + "\n")
    traces = planner_traces(["batch"], root=tmp_path)
    assert len(traces) == 5  # the data agent's run has no task
    assert sample(traces, 3, seed=1) == sample(traces, 3, seed=1)
    assert len(sample(traces, 3, seed=1)) == 3 and sample(traces, 9, seed=1) == traces


def test_the_report_lists_every_run_not_judged_sound():
    failed = [{"step": "reads the case first", "evidence": "turn 1 draft_supplier_request"}]
    rows = [
        {"trace": "a", "task": "evaluate", "case_id": "C-1", "verdict": "sound", "failed_steps": [],
         "summary": "", "cost_usd": 0.01},
        {"trace": "b", "task": "prepare", "case_id": "C-2", "verdict": "unsound", "failed_steps": failed,
         "summary": "Wrote before reading.", "cost_usd": 0.01},
    ]  # fmt: skip
    text = render(rows, ["batch"], seed=1)
    assert "| 1 | 1 | 0 | 0 | 0.02 USD |" in text
    assert "prepare C-2" in text and "reads the case first: turn 1 draft_supplier_request" in text
