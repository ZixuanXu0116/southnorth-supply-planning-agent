import json
from pathlib import Path

import pytest

from snm_evals import tracking

T0 = "2026-10-06T20:08:11.000Z"


def at(seconds: float) -> str:
    whole, fraction = divmod(round(seconds * 1000), 1000)
    return f"2026-10-06T20:08:{11 + whole:02d}.{fraction:03d}Z"


def ns(seconds: float) -> int:
    return tracking.timestamp_ns(T0) + round(seconds * 1000) * 1_000_000


def new_trace(run_id: str = "c001-main-r1") -> list[dict]:
    """A trace as the current harness writes it (requests recorded, a rejected first submission)."""
    call = {"id": "t1", "name": "run_sql", "arguments": {"sql": "SELECT 1"}}
    submit = {"id": "t2", "name": "submit_answer", "arguments": {"behavior": "answer"}}
    events = [
        {
            "event": "run_start",
            "ts": at(0),
            "run_id": run_id,
            "arm": "main",
            "question": "Q?",
            "role": "r",
            "as_of": "2026-09-21",
            "model": "anthropic/m",
            "manifest": {"git_commit": "abc", "kb_digest": "kb1"},
        },
        {"event": "request", "ts": at(0.5), "call": 1, "model": "m", "effort": "medium", "messages": 1},
        {
            "event": "assistant",
            "ts": at(2),
            "turn": 1,
            "stop_reason": "toolUse",
            "text": "",
            "thinking": "plan",
            "usage": {"cost": {"total": 0.01}},
            "tool_calls": [call],
        },
        {
            "event": "tool",
            "ts": at(3),
            "id": "t1",
            "name": "run_sql",
            "is_error": False,
            "duration_ms": 800,
            "output": '{"result_id": "r0001"}',
        },
        {"event": "request", "ts": at(3.1), "call": 2, "model": "m", "effort": "medium", "messages": 3},
        {
            "event": "assistant",
            "ts": at(4),
            "turn": 2,
            "stop_reason": "toolUse",
            "text": "",
            "tool_calls": [submit],
            "usage": {"cost": {"total": 0.02}},
        },
        {
            "event": "faithfulness",
            "ts": at(4.1),
            "submission": 1,
            "checked": 1,
            "traced": 0,
            "rejected": True,
        },
        {
            "event": "tool",
            "ts": at(4.2),
            "id": "t2",
            "name": "submit_answer",
            "is_error": True,
            "duration_ms": 150,
            "output": "Numeric faithfulness check failed",
        },
        {
            "event": "run_end",
            "ts": at(5),
            "run_id": run_id,
            "status": "answered",
            "answer": {"behavior": "answer"},
            "turns": 2,
            "faithfulness": {"checked": 1, "traced": 1, "untraced": []},
        },
    ]
    return [{"seq": index + 1, **event} for index, event in enumerate(events)]


def old_trace(run_id: str = "c001-baseline-r1") -> list[dict]:
    """A trace written before requests, manifests, and faithfulness checks were recorded."""
    call = {"id": "t1", "name": "list_tables", "arguments": {}}
    events = [
        {
            "event": "run_start",
            "ts": at(0),
            "run_id": run_id,
            "arm": "baseline",
            "question": "Q?",
            "role": "r",
            "as_of": "2026-09-21",
        },
        {"event": "assistant", "ts": at(1), "turn": 1, "stop_reason": "toolUse", "tool_calls": [call]},
        {
            "event": "tool",
            "ts": at(1.5),
            "id": "t1",
            "name": "list_tables",
            "is_error": False,
            "duration_ms": 10,
            "output": "{}",
        },
        {"event": "assistant", "ts": at(3), "turn": 2, "stop_reason": "stop", "tool_calls": []},
        {"event": "repair", "ts": at(3.1), "reason": "no_submission"},
        {"event": "assistant", "ts": at(4), "turn": 3, "stop_reason": "toolUse", "tool_calls": []},
        {
            "event": "run_end",
            "ts": at(4.5),
            "run_id": run_id,
            "status": "answered",
            "answer": {"behavior": "decline"},
        },
    ]
    return [{"seq": index + 1, **event} for index, event in enumerate(events)]


def test_timestamps_keep_milliseconds() -> None:
    assert tracking.timestamp_ns("2026-10-06T20:08:11.929Z") - tracking.timestamp_ns(T0) == 929_000_000


def test_spans_rebuild_the_run_with_its_original_timing() -> None:
    spans = tracking.trace_spans(new_trace())
    assert [(span.name, span.span_type, span.parent) for span in spans] == [
        ("agent_run", "AGENT", None),
        ("llm_call", "CHAT_MODEL", "run"),
        ("run_sql", "TOOL", "run"),
        ("llm_call", "CHAT_MODEL", "run"),
        ("submit_answer", "TOOL", "run"),
    ]
    root, first_call, sql, _, submit = spans
    assert (root.start_ns, root.end_ns) == (ns(0), ns(5))
    assert root.inputs == {"question": "Q?", "role": "r", "as_of": "2026-09-21"}
    assert root.outputs == {"status": "answered", "answer": {"behavior": "answer"}}
    assert root.attributes["manifest"] == {"git_commit": "abc", "kb_digest": "kb1"}
    assert root.attributes["faithfulness"]["traced"] == 1
    # An LLM call runs from its request to the assistant message.
    assert (first_call.start_ns, first_call.end_ns) == (ns(0.5), ns(2))
    assert first_call.inputs == {"turn": 1, "model": "m", "effort": "medium", "messages": 1}
    assert first_call.outputs["thinking"] == "plan"
    assert first_call.attributes["cost_usd"] == 0.01
    # A tool span ends when the tool event was written and carries the call's arguments.
    assert (sql.start_ns, sql.end_ns) == (ns(2.2), ns(3))
    assert sql.inputs == {"sql": "SELECT 1"}
    assert submit.error and not sql.error and not root.error
    assert [(name, when) for name, when, _ in root.events] == [("faithfulness", ns(4.1))]
    assert root.events[0][2]["rejected"] is True


def test_spans_of_traces_without_requests_start_after_the_previous_event() -> None:
    spans = tracking.trace_spans(old_trace())
    calls = [span for span in spans if span.name == "llm_call"]
    expected = [(ns(0), ns(1)), (ns(1.5), ns(3)), (ns(3.1), ns(4))]
    assert [(span.start_ns, span.end_ns) for span in calls] == expected
    assert "manifest" not in spans[0].attributes
    assert [name for name, _, _ in spans[0].events] == ["repair"]


def test_a_run_without_run_end_is_an_error_span() -> None:
    spans = tracking.trace_spans(old_trace()[:3])
    assert spans[0].error and spans[0].outputs is None and spans[0].end_ns == ns(1.5)


def test_arm_metrics_follow_the_report() -> None:
    scores = [
        {
            "arm": "main",
            "passed": True,
            "behavior_ok": True,
            "values_ok": True,
            "cost_usd": 0.02,
            "turns": 3,
            "sql_statements": 2,
            "wall_clock_s": 20,
            "values_checked": 4,
            "values_traced": 4,
            "rejected_submissions": 1,
        },
        {
            "arm": "main",
            "passed": False,
            "behavior_ok": True,
            "values_ok": False,
            "cost_usd": 0.04,
            "turns": 5,
            "sql_statements": 4,
            "wall_clock_s": 40,
            "values_checked": 2,
            "values_traced": 1,
            "rejected_submissions": 0,
        },
        {"arm": "baseline", "passed": True, "behavior_ok": True, "values_ok": None, "cost_usd": 0.01},
    ]
    metrics = tracking.arm_metrics(scores)
    assert metrics["main/pass_rate"] == 0.5
    assert metrics["main/answering_correct"] == 0.5
    assert metrics["main/traced_values"] == pytest.approx(5 / 6)
    assert metrics["main/rejected_runs"] == 1
    assert metrics["main/mean_cost_usd"] == pytest.approx(0.03)
    assert metrics["main/mean_sql_statements"] == 3
    assert metrics["baseline/runs"] == 1
    # Older scores have no faithfulness fields and no answering runs here.
    absent = {"baseline/traced_values", "baseline/rejected_runs", "baseline/answering_correct"}
    assert not absent & set(metrics)


REPORT = (
    "# Evaluation report: bank_v6 (b1)\n\nAs-of date 2026-09-21; data snapshot {'profile': 'small', "
    "'generator_version': '0.6.0', 'seed': 20260924}. Deterministic scoring.\n"
)


def test_report_header() -> None:
    assert tracking.read_report_header(REPORT) == {
        "suite": "bank_v6",
        "as_of": "2026-09-21",
        "data_snapshot": "{'profile': 'small', 'generator_version': '0.6.0', 'seed': 20260924}",
    }
    with pytest.raises(ValueError):
        tracking.read_report_header("# Something else\n")


def write_batch(root: Path) -> Path:
    batch = root / "b1"
    traces = {"c001-main-r1": new_trace(), "c001-baseline-r1": old_trace()}
    results, scores = [], []
    for run_id, events in traces.items():
        arm = events[0]["arm"]
        run_dir = batch / "runs" / run_id
        (run_dir / "audit").mkdir(parents=True)
        (run_dir / "trace.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        (run_dir / "result.json").write_text("{}", encoding="utf-8")
        (run_dir / "prompt.md").write_text("prompt", encoding="utf-8")
        (run_dir / "audit" / "audit-1.jsonl").write_text("{}\n", encoding="utf-8")
        results.append(
            {
                "run_id": run_id,
                "case_id": "c001",
                "arm": arm,
                "rep": 1,
                "model": "anthropic/m",
                "status": "answered",
                "usage": {"cost_usd": 0.03},
                "answer": {"behavior": "answer"},
                "prompt": {"name": arm, "version": "v1", "digest": "d"},
                **({"manifest": events[0]["manifest"]} if "manifest" in events[0] else {}),
            }
        )
        scores.append({"run_id": run_id, "arm": arm, "passed": arm == "main", "behavior_ok": True})
    (batch / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in results), encoding="utf-8")
    (batch / "scores.json").write_text(json.dumps(scores), encoding="utf-8")
    (batch / "report.md").write_text(REPORT, encoding="utf-8")
    return batch


def test_upload_batch_to_a_local_tracking_store(monkeypatch, tmp_path) -> None:
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", (tmp_path / "mlruns").as_uri())
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracking.configure_tracking()
    batch = write_batch(tmp_path / "batches")
    messages: list[str] = []

    run_id = tracking.upload_batch(batch, "evals", log=messages.append)
    client = mlflow.MlflowClient()
    run = client.get_run(run_id)
    assert run.data.tags[tracking.UPLOAD_TAG] == "complete"
    assert run.data.tags["snm.suite"] == "bank_v6"
    assert run.data.params["arms"] == "baseline, main"
    assert run.data.params["git_commits"] == "abc"
    assert run.data.params["prompt.main"] == "main v1 (d)"
    assert run.data.metrics["main/pass_rate"] == 1.0 and run.data.metrics["baseline/pass_rate"] == 0.0
    assert (run.info.start_time, run.info.end_time) == (ns(0) // 1_000_000, ns(5) // 1_000_000)
    experiment_id = run.info.experiment_id
    traces = client.search_traces(locations=[experiment_id])
    assert sorted(trace.info.tags["run_id"] for trace in traces) == ["c001-baseline-r1", "c001-main-r1"]
    main = next(trace for trace in traces if trace.info.tags["arm"] == "main")
    assert main.info.tags["passed"] == "true" and main.info.tags["batch"] == "b1"
    assert main.info.trace_metadata["mlflow.sourceRun"] == run_id
    assert [span.name for span in main.data.spans].count("llm_call") == 2
    files = {item.path for item in client.list_artifacts(run_id)}
    assert {"report.md", "scores.json", "results.jsonl", "runs"} <= files
    run_files = {item.path for item in client.list_artifacts(run_id, "runs/c001-main-r1")}
    assert "runs/c001-main-r1/trace.jsonl" in run_files and "runs/c001-main-r1/audit" in run_files

    # Uploading again skips the batch; replacing it leaves exactly one copy.
    assert tracking.upload_batch(batch, "evals", log=messages.append) == run_id
    assert "already in MLflow" in messages[-1]
    replaced = tracking.upload_batch(batch, "evals", replace=True, log=messages.append)
    assert replaced != run_id and client.get_run(run_id).info.lifecycle_stage == "deleted"
    assert len(client.search_traces(locations=[experiment_id])) == 2


def test_upload_refuses_an_unscored_batch(tmp_path) -> None:
    batch = tmp_path / "b2"
    batch.mkdir()
    with pytest.raises(ValueError, match="score it first"):
        tracking.upload_batch(batch, "evals")


def test_discovery_tasks_have_their_own_pass_rate() -> None:
    rows = [
        {"arm": "main", "passed": True, "behavior_ok": True},
        {"arm": "main", "passed": False, "behavior_ok": True, "discovery": True},
    ]
    metrics = tracking.arm_metrics(rows)
    assert metrics["main/pass_rate"] == 1.0
    assert metrics["main/discovery_pass_rate"] == 0.0
    assert metrics["main/runs"] == 2
