"""Persist evaluation batches to MLflow (PROPOSAL §9: JSONL traces are the source of truth; MLflow is for
browsing and keeps a durable copy).

A finished batch directory becomes:

- one MLflow run (tag ``snm.batch``) with parameters, per-arm metrics from ``scores.json``, the report and
  scores as artifacts, and every agent run's raw files (trace, result, prompt, SQL audit) under ``runs/``;
- one MLflow trace per agent run, rebuilt from its ``trace.jsonl`` with the original timestamps and linked to
  the batch run: an ``agent_run`` span with ``llm_call`` and tool spans; faithfulness checks and repairs are
  span events.

Uploads are idempotent: a batch whose upload completed is skipped unless ``replace`` is set, and an incomplete
upload is replaced. Credentials come from the environment of this Python process; the TypeScript harness never
sees them. The experiment holds evaluation results, so it must never be exposed to the agent.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

BATCH_TAG = "snm.batch"
UPLOAD_TAG = "snm.upload"
EXPERIMENT_ENV = "SNM_MLFLOW_EXPERIMENT"
DEFAULT_EXPERIMENT = "snm-agent-evals"
_BATCH_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
_REPORT_TITLE = re.compile(r"^# Evaluation report: (?P<suite>\S+) \((?P<batch>[^)]+)\)$")
_REPORT_SNAPSHOT = re.compile(r"^As-of date (?P<as_of>\S+); data snapshot (?P<snapshot>\{[^}]*\})\.")


@dataclass
class SpanSpec:
    """One span to log, with timestamps in nanoseconds since the epoch."""

    key: str
    name: str
    span_type: str
    start_ns: int
    end_ns: int
    parent: str | None = None
    inputs: Any = None
    outputs: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)
    error: bool = False
    events: list[tuple[str, int, dict[str, Any]]] = field(default_factory=list)


def timestamp_ns(value: str) -> int:
    """ISO-8601 timestamp (as the harness writes it) to integer nanoseconds."""
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    whole = moment.replace(microsecond=0)
    return int(whole.timestamp()) * 1_000_000_000 + moment.microsecond * 1_000


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _pick(event: dict[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {key: event[key] for key in keys if key in event}


_START_KEYS = ("run_id", "arm", "model", "effort", "prompt", "metadata", "tools", "budgets", "model_params")
_END_KEYS = (
    "status", "usage", "turns", "repairs", "requests", "sql_statements", "wall_clock_s", "tool_calls",
    "tool_errors", "faithfulness", "rejected_submissions", "task_card", "task_card_updates", "error",
)  # fmt: skip
_REQUEST_KEYS = ("model", "max_tokens", "thinking", "effort", "effort_source", "betas", "messages", "tools")


def trace_spans(events: list[dict[str, Any]]) -> list[SpanSpec]:
    """Rebuild the span tree of one agent run from its trace events (any trace format the harness wrote).

    An LLM call starts at its ``request`` event, or for traces written before requests were recorded, when the
    previous event ended. Tool events are written when the tool finishes, with its duration.
    """
    start = next(event for event in events if event["event"] == "run_start")
    end = next((event for event in reversed(events) if event["event"] == "run_end"), None)
    root = SpanSpec(
        key="run",
        name="agent_run",
        span_type="AGENT",
        start_ns=timestamp_ns(start["ts"]),
        end_ns=timestamp_ns((end or events[-1])["ts"]),
        inputs=_pick(start, ("question", "role", "as_of")),
        outputs={"status": end.get("status"), "answer": end.get("answer")} if end else None,
        attributes=_pick(start, (*_START_KEYS, "manifest")),
        error=end is None or end.get("status") == "error",
    )
    if end:
        root.attributes.update(_pick(end, _END_KEYS))
    spans = [root]
    calls: dict[str, dict[str, Any]] = {}
    request: dict[str, Any] | None = None
    last_ns = root.start_ns
    for event in events:
        kind = event["event"]
        if kind in ("run_start", "run_end"):
            continue
        at = timestamp_ns(event["ts"])
        if kind == "request":
            request = event
            continue
        if kind == "assistant":
            for call in event.get("tool_calls") or []:
                calls[call["id"]] = call
            usage = event.get("usage") or {}
            spans.append(
                SpanSpec(
                    key=f"llm-{event.get('seq')}",
                    name="llm_call",
                    span_type="CHAT_MODEL",
                    start_ns=timestamp_ns(request["ts"]) if request else last_ns,
                    end_ns=at,
                    parent="run",
                    inputs={"turn": event.get("turn"), **(_pick(request, _REQUEST_KEYS) if request else {})},
                    outputs=_pick(event, ("text", "thinking", "tool_calls")),
                    attributes={
                        "stop_reason": event.get("stop_reason"),
                        "usage": usage,
                        "cost_usd": (usage.get("cost") or {}).get("total"),
                        "thinking_chars": event.get("thinking_chars"),
                    },
                    error=event.get("stop_reason") == "error",
                )
            )
            request = None
        elif kind == "tool":
            duration_ns = int(event.get("duration_ms") or 0) * 1_000_000
            spans.append(
                SpanSpec(
                    key=f"tool-{event.get('seq')}",
                    name=event["name"],
                    span_type="TOOL",
                    start_ns=at - duration_ns,
                    end_ns=at,
                    parent="run",
                    inputs=(calls.get(event["id"]) or {}).get("arguments"),
                    outputs=event.get("output"),
                    attributes={"tool_call_id": event["id"], "duration_ms": event.get("duration_ms")},
                    error=bool(event.get("is_error")),
                )
            )
        else:  # point events: faithfulness checks, repairs, and any later additions
            details = {key: value for key, value in event.items() if key not in ("seq", "ts", "event")}
            root.events.append((kind, at, details))
        last_ns = max(last_ns, at)
    return spans


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def arm_metrics(scores: list[dict[str, Any]]) -> dict[str, float]:
    """Per-arm metrics named ``<arm>/<metric>``, as in the batch report (scores of any scorer version)."""
    by_arm: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for score in scores:
        by_arm[score["arm"]].append(score)
    metrics: dict[str, float] = {}
    for arm, rows in sorted(by_arm.items()):
        answering = [row for row in rows if row.get("values_ok") is not None]
        checked = sum(int(row.get("values_checked") or 0) for row in rows)
        headline = [row for row in rows if not row.get("discovery")]
        discovery = [row for row in rows if row.get("discovery")]
        values = {
            "runs": float(len(rows)),
            "pass_rate": _mean([float(bool(row["passed"])) for row in headline]),
            "behavior_correct": _mean([float(bool(row["behavior_ok"])) for row in rows]),
            "total_cost_usd": sum(float(row.get("cost_usd") or 0.0) for row in rows),
            "mean_cost_usd": _mean([float(row.get("cost_usd") or 0.0) for row in rows]),
            "mean_turns": _mean([float(row.get("turns") or 0) for row in rows]),
            "mean_sql_statements": _mean([float(row.get("sql_statements") or 0) for row in rows]),
            "mean_latency_s": _mean([float(row.get("wall_clock_s") or 0.0) for row in rows]),
        }
        if discovery:
            values["discovery_pass_rate"] = _mean([float(bool(row["passed"])) for row in discovery])
        if answering:
            values["answering_correct"] = _mean([float(bool(row["values_ok"])) for row in answering])
        if checked:
            values["traced_values"] = sum(int(row.get("values_traced") or 0) for row in rows) / checked
        if any("rejected_submissions" in row for row in rows):
            rejected = [int(row.get("rejected_submissions") or 0) > 0 for row in rows]
            values["rejected_runs"] = float(sum(rejected))
        metrics.update({f"{arm}/{name}": value for name, value in values.items()})
    return metrics


def read_report_header(report: str) -> dict[str, str]:
    """Suite, as-of date, and data snapshot from the first lines of a batch report."""
    lines = report.splitlines()
    title = _REPORT_TITLE.match(lines[0]) if lines else None
    snapshot = next((m for line in lines[1:4] if (m := _REPORT_SNAPSHOT.match(line))), None)
    if not title or not snapshot:
        raise ValueError("the report does not start with the batch header written by `snm-evals`")
    return {"suite": title["suite"], "as_of": snapshot["as_of"], "data_snapshot": snapshot["snapshot"]}


def _joined(values: Iterable[Any]) -> str:
    return ", ".join(sorted({str(value) for value in values if value not in (None, "")})) or "-"


def batch_params(header: dict[str, str], results: list[dict[str, Any]]) -> dict[str, str]:
    manifests = [result["manifest"] for result in results if result.get("manifest")]
    prompts = {
        f"prompt.{result['arm']}": f"{result['prompt'].get('name')} {result['prompt'].get('version')} "
        f"({result['prompt'].get('digest')})"
        for result in results
        if result.get("arm") and isinstance(result.get("prompt"), dict)
    }
    return {
        **header,
        "arms": _joined(result.get("arm") for result in results),
        "models": _joined(result.get("model") for result in results),
        "efforts": _joined(result.get("effort") for result in results),
        "cases": str(len({result.get("case_id") for result in results})),
        "repetitions": str(max((int(result.get("rep", 1)) for result in results), default=0)),
        "git_commits": _joined(manifest.get("git_commit") for manifest in manifests),
        "kb_digests": _joined(manifest.get("kb_digest") for manifest in manifests),
        **dict(sorted(prompts.items())),
    }


def default_experiment() -> str:
    """``SNM_MLFLOW_EXPERIMENT``, else ``snm-agent-evals`` in the Databricks user's home folder."""
    configured = os.environ.get(EXPERIMENT_ENV)
    if configured:
        return configured
    from databricks.sdk import WorkspaceClient

    return f"/Users/{WorkspaceClient().current_user.me().user_name}/{DEFAULT_EXPERIMENT}"


def configure_tracking() -> None:
    """Use Databricks unless ``MLFLOW_TRACKING_URI`` names another tracking server."""
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")  # read when mlflow is imported
    import mlflow

    if not os.environ.get("MLFLOW_TRACKING_URI"):
        mlflow.set_tracking_uri("databricks")


def _all_pages(search: Callable[[str | None], Any]) -> list[Any]:
    """Follow page tokens: Databricks returns short pages even when more results match."""
    items: list[Any] = []
    token = None
    while True:
        page = search(token)
        items += list(page)
        token = page.token
        if not token:
            return items


def _trace_ids(client: Any, experiment_id: str, batch: str) -> list[str]:
    traces = _all_pages(
        lambda token: client.search_traces(
            locations=[experiment_id],
            filter_string=f"tags.batch = '{batch}'",
            max_results=100,
            page_token=token,
            include_spans=False,
        )
    )
    return [trace.info.trace_id for trace in traces]


def _delete_batch(client: Any, experiment_id: str, batch: str, runs: list[Any]) -> None:
    ids = _trace_ids(client, experiment_id, batch)
    for offset in range(0, len(ids), 100):
        client.delete_traces(experiment_id, trace_ids=ids[offset : offset + 100])
    for run in runs:
        client.delete_run(run.info.run_id)


def _log_trace(spans: list[SpanSpec], experiment_id: str, batch_run_id: str, tags: dict[str, str]) -> None:
    import mlflow
    from mlflow.entities import SpanEvent, SpanStatusCode
    from mlflow.tracing.constant import TraceMetadataKey

    live: dict[str, Any] = {}
    for spec in spans:
        options: dict[str, Any] = {}
        if spec.parent is None:
            options = {
                "experiment_id": experiment_id,
                "tags": tags,
                "metadata": {TraceMetadataKey.SOURCE_RUN: batch_run_id},
            }
        span = mlflow.start_span_no_context(
            spec.name,
            span_type=spec.span_type,
            parent_span=live.get(spec.parent) if spec.parent else None,
            inputs=spec.inputs,
            attributes=spec.attributes,
            start_time_ns=spec.start_ns,
            **options,
        )
        for name, at, attributes in spec.events:
            span.add_event(SpanEvent(name, at, {key: json.dumps(value) for key, value in attributes.items()}))
        live[spec.key] = span
    # Children first, so that every span ends before its parent.
    for spec in reversed(spans):
        status = SpanStatusCode.ERROR if spec.error else SpanStatusCode.OK
        live[spec.key].end(outputs=spec.outputs, status=status, end_time_ns=max(spec.end_ns, spec.start_ns))


def upload_batch(
    batch_dir: Path,
    experiment: str,
    *,
    replace: bool = False,
    log: Callable[[str], None] = print,
) -> str:
    """Log a scored batch to MLflow (see the module docstring); return the MLflow run id."""
    import mlflow
    from mlflow import MlflowClient
    from mlflow.entities import Metric, Param

    batch = batch_dir.name
    if not _BATCH_NAME.match(batch):
        raise ValueError(f"unsupported batch name {batch!r}")
    report_path, scores_path = batch_dir / "report.md", batch_dir / "scores.json"
    if not report_path.is_file() or not scores_path.is_file():
        raise ValueError(f"{batch} has no report.md and scores.json; score it first (`snm-evals score`)")
    header = read_report_header(report_path.read_text(encoding="utf-8"))
    results = [
        json.loads(line)
        for line in (batch_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    scores = {score["run_id"]: score for score in json.loads(scores_path.read_text(encoding="utf-8"))}

    client = MlflowClient()
    found = client.get_experiment_by_name(experiment)
    experiment_id = found.experiment_id if found else client.create_experiment(experiment)
    existing = _all_pages(
        lambda token: client.search_runs([experiment_id], f"tags.`{BATCH_TAG}` = '{batch}'", page_token=token)
    )
    complete = [run for run in existing if run.data.tags.get(UPLOAD_TAG) == "complete"]
    if complete and not replace:
        log(f"{batch}: already in MLflow (run {complete[0].info.run_id}); pass --replace to upload again")
        return complete[0].info.run_id
    if existing or _trace_ids(client, experiment_id, batch):
        log(f"{batch}: replacing the earlier upload")
        _delete_batch(client, experiment_id, batch, existing)

    traces = []
    for result in results:
        path = batch_dir / "runs" / result["run_id"] / "trace.jsonl"
        if path.is_file():
            traces.append((result, trace_spans(read_events(path))))
    if not traces:
        raise ValueError(f"{batch} has no traces under runs/")
    start_ms = min(spans[0].start_ns for _, spans in traces) // 1_000_000
    end_ms = max(spans[0].end_ns for _, spans in traces) // 1_000_000

    run = client.create_run(
        experiment_id,
        start_time=start_ms,
        run_name=batch,
        tags={
            BATCH_TAG: batch,
            UPLOAD_TAG: "started",
            "snm.kind": "eval_batch",
            "snm.suite": header["suite"],
        },
    )
    run_id = run.info.run_id
    params = [Param(key, value) for key, value in batch_params(header, results).items()]
    metrics = [Metric(key, value, end_ms, 0) for key, value in arm_metrics(list(scores.values())).items()]
    for offset in range(0, max(len(params), len(metrics)), 100):
        client.log_batch(run_id, metrics=metrics[offset : offset + 100], params=params[offset : offset + 100])

    for result, spans in traces:
        score = scores.get(result["run_id"], {})
        tags = {
            "batch": batch,
            "run_id": result["run_id"],
            "case_id": str(result.get("case_id")),
            "arm": str(result.get("arm")),
            "rep": str(result.get("rep", 1)),
            "model": str(result.get("model")),
            "status": str(result.get("status")),
            "behavior": str((result.get("answer") or {}).get("behavior")),
            "passed": str(score.get("passed")).lower(),
            "cost_usd": f"{float((result.get('usage') or {}).get('cost_usd') or 0.0):.4f}",
        }
        _log_trace(spans, experiment_id, run_id, tags)
    mlflow.flush_trace_async_logging()
    logged = len(_trace_ids(client, experiment_id, batch))
    if logged != len(traces):
        raise RuntimeError(f"{batch}: {logged} of {len(traces)} traces reached MLflow; upload again")

    for name in ("report.md", "scores.json", "results.jsonl"):
        client.log_artifact(run_id, str(batch_dir / name))
    client.log_artifacts(run_id, str(batch_dir / "runs"), "runs")

    client.set_tag(run_id, UPLOAD_TAG, "complete")
    client.set_terminated(run_id, end_time=end_ms)
    log(f"{batch}: logged {len(traces)} traces, {len(metrics)} metrics, and the run files (run {run_id})")
    return run_id


def download_batch(batch: str, target: Path, experiment: str) -> Path:
    """Fetch the files of an uploaded batch (results, scores, report, every run's files) into ``target``.

    The improvement loop reads a batch from here when it was run on another machine (decision record 009).
    """
    from mlflow import MlflowClient

    if not _BATCH_NAME.match(batch):
        raise ValueError(f"unsupported batch name {batch!r}")
    client = MlflowClient()
    found = client.get_experiment_by_name(experiment)
    if found is None:
        raise ValueError(f"no MLflow experiment {experiment}")
    runs = _all_pages(
        lambda token: client.search_runs(
            [found.experiment_id], f"tags.`{BATCH_TAG}` = '{batch}'", page_token=token
        )
    )
    complete = [run for run in runs if run.data.tags.get(UPLOAD_TAG) == "complete"]
    if not complete:
        raise ValueError(f"{batch}: no complete upload in {experiment}")
    target.mkdir(parents=True, exist_ok=True)
    client.download_artifacts(complete[0].info.run_id, "", str(target))
    return target
