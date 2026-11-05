"""The replenishment workflow's runs in MLflow, step by step, apart from the data agent's (2026-11-05).

Two experiments in one folder, apart from the data agent's ``snm-agent-evals``:

- ``/Shared/snm-workflow/evals``: one MLflow run per evaluation batch (its parameters, metrics, and rows
  file) and one trace per agent run, rebuilt from the run's ``trace.jsonl`` like the data agent's;
- ``/Shared/snm-workflow/app``: one trace per agent run of the Databricks App.

Both keep their trace data in the runs volume, which the app can reach (``tracing.py`` of ``apps/web``). Every
trace carries the same tags to filter on: ``agent``, ``task``, ``case_id``, ``status``, ``source`` (``eval``
or ``app``), ``batch``, ``prompt_version``, ``model``, plus ``run_id`` and ``cost_usd``. Uploads are
idempotent, and every trace is read back: one without its spans fails the upload.
"""

from __future__ import annotations

import json
import statistics
import tempfile
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .tracking import (
    _BATCH_NAME,
    BATCH_TAG,
    UPLOAD_TAG,
    _all_pages,
    _delete_batch,
    _log_trace,
    _trace_ids,
    ensure_experiment,
    read_events,
    trace_spans,
)

EVALS_EXPERIMENT = "/Shared/snm-workflow/evals"
APP_EXPERIMENT = "/Shared/snm-workflow/app"
TRACE_STORE = "dbfs:/Volumes/snm/cases/runs/mlflow-traces"
EVALS_ARTIFACTS = f"{TRACE_STORE}/workflow-evals"
APP_ARTIFACTS = f"{TRACE_STORE}/workflow-app"
RUNS_VOLUME = "/Volumes/snm/cases/runs"
# The batch's own rows: the planner suites write results.jsonl, the extractor suite rows.jsonl, the trace
# judge judgments.jsonl.
ROWS_FILES = ("results.jsonl", "rows.jsonl", "judgments.jsonl")
BATCH_FILES = (*ROWS_FILES, "spent.json")


def workflow_tags(events: list[dict[str, Any]], source: str, batch: str) -> dict[str, str]:
    """The filter tags of one agent run, from its trace events."""
    start = next((e for e in events if e.get("event") == "run_start"), {})
    end = next((e for e in reversed(events) if e.get("event") == "run_end"), {})
    prompt = start.get("prompt") if isinstance(start.get("prompt"), dict) else {}
    agent = str(prompt.get("name") or start.get("agent") or "unknown")
    usage = end.get("usage") or {}
    tags = {
        "agent": agent,
        "task": str(start.get("task") or ("extract" if agent == "extractor" else "-")),
        "case_id": str(start.get("case_id") or end.get("case_id") or "-"),
        "status": str(end.get("status") or "unfinished"),
        "source": source,
        "batch": batch,
        "prompt_version": str(prompt.get("version") or "-"),
        "model": str(start.get("model") or end.get("model") or "-"),
        "run_id": str(start.get("run_id") or end.get("run_id") or "-"),
        "cost_usd": f"{float(usage.get('cost_usd') or 0.0):.4f}",
    }
    document = start.get("document")
    if isinstance(document, dict) and document.get("file_name"):
        tags["document"] = str(document["file_name"])
    return tags


def trace_files(batch_dir: Path) -> list[Path]:
    return sorted(batch_dir.rglob("trace.jsonl"))


def _group(batch_dir: Path, trace: Path) -> str:
    """The folder of the run inside the batch (arm, prompt version, repetition), e.g. ``runs-none-v6-r2``."""
    parts = trace.relative_to(batch_dir).parts[:-2]
    return "/".join(parts) or "-"


def _rows(batch_dir: Path) -> list[dict[str, Any]]:
    for name in ROWS_FILES:
        path = batch_dir / name
        if path.is_file():
            return [
                json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
            ]
    return []


def _joined(values: Iterable[Any]) -> str:
    return ", ".join(sorted({str(v) for v in values if v not in (None, "", "-")})) or "-"


def _p95(values: list[float]) -> float:
    if len(values) < 2:
        return values[0] if values else 0.0
    return statistics.quantiles(values, n=20, method="inclusive")[-1]


def batch_summary(
    rows: list[dict[str, Any]], runs: list[list[dict[str, Any]]]
) -> tuple[dict[str, str], dict[str, float]]:
    """Parameters and metrics of one batch: from its traces, or from its rows when it has none."""
    tags = [workflow_tags(events, "eval", "") for events in runs]
    ends = [next((e for e in reversed(events) if e.get("event") == "run_end"), {}) for events in runs]
    params = {
        "agents": _joined(t["agent"] for t in tags),
        "tasks": _joined([*(t["task"] for t in tags), *(r.get("task") for r in rows)]),
        "arms": _joined(r.get("arm") for r in rows),
        "prompt_versions": _joined(f"{t['agent']} {t['prompt_version']}" for t in tags),
        "models": _joined(t["model"] for t in tags),
        "rows": str(len(rows)),
        "traces": str(len(runs)),
    }
    metrics: dict[str, float] = {"runs": float(len(runs)), "rows": float(len(rows))}
    if runs:
        statuses = Counter(t["status"] for t in tags)
        costs = [float(t["cost_usd"]) for t in tags]
        latencies = [float(e.get("wall_clock_s") or 0.0) for e in ends if e]
        refused = [len(e.get("refused_writes") or []) for e in ends]
    else:
        statuses = Counter(str(r.get("status") or r.get("verdict") or "-") for r in rows)
        costs = [float(r.get("cost_usd") or 0.0) for r in rows]
        latencies = [float(r.get("wall_clock_s") or r.get("seconds") or 0.0) for r in rows]
        refused = [
            int(r.get("refused_writes") or 0) for r in rows if isinstance(r.get("refused_writes"), int)
        ]
    metrics |= {f"status/{status}": float(count) for status, count in statuses.items()}
    metrics["errors"] = float(statuses.get("error", 0) + statuses.get("unfinished", 0))
    metrics["refused_writes"] = float(sum(refused))
    metrics["cost_usd_total"] = round(sum(costs), 6)
    metrics["cost_usd_mean"] = round(statistics.fmean(costs), 6) if costs else 0.0
    metrics["latency_s_mean"] = round(statistics.fmean(latencies), 3) if latencies else 0.0
    metrics["latency_s_p95"] = round(_p95(latencies), 3)
    return params, metrics


def _read_back(client: Any, trace_ids: list[str]) -> list[str]:
    """The trace ids whose spans did not reach MLflow."""
    missing = []
    for trace_id in trace_ids:
        try:
            stored = client.get_trace(trace_id)
            if not (stored and stored.data and stored.data.spans):
                missing.append(trace_id)
        except Exception:
            missing.append(trace_id)
    return missing


def upload_workflow_batch(
    batch_dir: Path,
    experiment: str = EVALS_EXPERIMENT,
    *,
    artifact_location: str | None = EVALS_ARTIFACTS,
    replace: bool = False,
    log: Callable[[str], None] = print,
) -> str:
    """One workflow evaluation batch as one MLflow run and one trace per agent run; the MLflow run id."""
    import mlflow
    from mlflow import MlflowClient
    from mlflow.entities import Metric, Param

    batch = batch_dir.name
    if not _BATCH_NAME.match(batch):
        raise ValueError(f"unsupported batch name {batch!r}")
    traces = trace_files(batch_dir)
    rows = _rows(batch_dir)
    if not traces and not rows:
        raise ValueError(f"{batch} has neither a rows file ({', '.join(ROWS_FILES)}) nor traces")

    client = MlflowClient()
    experiment_id = ensure_experiment(client, experiment, artifact_location)
    existing = _all_pages(
        lambda token: client.search_runs([experiment_id], f"tags.`{BATCH_TAG}` = '{batch}'", page_token=token)
    )
    complete = [run for run in existing if run.data.tags.get(UPLOAD_TAG) == "complete"]
    if complete and not replace:
        log(f"{batch}: already in MLflow (run {complete[0].info.run_id})")
        return complete[0].info.run_id
    if existing or _trace_ids(client, experiment_id, batch):
        log(f"{batch}: replacing the earlier upload")
        _delete_batch(client, experiment_id, batch, existing)

    runs = [read_events(path) for path in traces]
    spans = [trace_spans(events) for events in runs]
    params, metrics = batch_summary(rows, runs)
    if spans:
        start_ms = min(s[0].start_ns for s in spans) // 1_000_000
        end_ms = max(s[0].end_ns for s in spans) // 1_000_000
    else:
        rows_file = next(batch_dir / n for n in ROWS_FILES if (batch_dir / n).is_file())
        start_ms = end_ms = int(rows_file.stat().st_mtime * 1000)

    run = client.create_run(
        experiment_id,
        start_time=start_ms,
        run_name=batch,
        tags={BATCH_TAG: batch, UPLOAD_TAG: "started", "snm.kind": "workflow_batch", "source": "eval"},
    )
    run_id = run.info.run_id
    client.log_batch(
        run_id,
        metrics=[Metric(key, value, end_ms, 0) for key, value in sorted(metrics.items())],
        params=[Param(key, value) for key, value in params.items()],
    )

    trace_ids = []
    for path, events, run_spans in zip(traces, runs, spans, strict=True):
        tags = {**workflow_tags(events, "eval", batch), "group": _group(batch_dir, path)}
        trace_ids.append(_log_trace(run_spans, experiment_id, run_id, tags))
    mlflow.flush_trace_async_logging()
    logged = len(_trace_ids(client, experiment_id, batch))
    missing = _read_back(client, trace_ids)
    if logged != len(traces) or missing:
        raise RuntimeError(
            f"{batch}: {logged} of {len(traces)} traces in MLflow, {len(missing)} without spans; upload again"
        )

    for name in BATCH_FILES:
        if (batch_dir / name).is_file():
            client.log_artifact(run_id, str(batch_dir / name))
    client.set_tag(run_id, UPLOAD_TAG, "complete")
    client.set_terminated(run_id, end_time=end_ms)
    log(f"{batch}: one run and {len(traces)} traces with their spans (run {run_id})")
    return run_id


def count_workflow_batch(batch_dir: Path, experiment: str = EVALS_EXPERIMENT) -> tuple[int, int, bool]:
    """Local ``trace.jsonl`` files, traces in MLflow, and whether the batch run is complete."""
    from mlflow import MlflowClient

    client = MlflowClient()
    found = client.get_experiment_by_name(experiment)
    local = len(trace_files(batch_dir))
    if found is None:
        return local, 0, False
    runs = client.search_runs([found.experiment_id], f"tags.`{BATCH_TAG}` = '{batch_dir.name}'")
    complete = any(run.data.tags.get(UPLOAD_TAG) == "complete" for run in runs)
    return local, len(_trace_ids(client, found.experiment_id, batch_dir.name)), complete


# --------------------------------------------------------------------------------------------- app runs


def _uploaded_run_ids(client: Any, experiment_id: str) -> set[str]:
    traces = _all_pages(
        lambda token: client.search_traces(
            locations=[experiment_id], max_results=100, page_token=token, include_spans=False
        )
    )
    return {trace.info.tags.get("run_id", "") for trace in traces}


def app_runs_in_volume(files: Any, volume: str = RUNS_VOLUME) -> dict[str, tuple[str, str]]:
    """Every agent run the app's runs mirror copied, by run id: (the first app start that holds it, its
    trace path). Each app start's folder holds the runs of the earlier starts too, so a run belongs to the
    first; the ``app-start-*`` probes are checks of the trace store, not agent runs."""
    found: dict[str, tuple[str, str]] = {}
    starts = sorted(e.name for e in files.list_directory_contents(volume) if e.name.startswith("app-"))
    for start in starts:
        try:
            entries = list(files.list_directory_contents(f"{volume}/{start}/agents"))
        except Exception:
            continue
        for entry in entries:
            if entry.name.startswith("app-start-") or entry.name in found:
                continue
            found[entry.name] = (start, f"{volume}/{start}/agents/{entry.name}/trace.jsonl")
    return found


def upload_app_runs(
    files: Any,
    experiment: str = APP_EXPERIMENT,
    *,
    artifact_location: str | None = APP_ARTIFACTS,
    volume: str = RUNS_VOLUME,
    log: Callable[[str], None] = print,
) -> list[str]:
    """The app's earlier agent runs from the runs volume, one trace each; the trace ids added."""
    import mlflow
    from mlflow import MlflowClient

    client = MlflowClient()
    experiment_id = ensure_experiment(client, experiment, artifact_location)
    done = _uploaded_run_ids(client, experiment_id)
    added = []
    with tempfile.TemporaryDirectory() as tmp:
        for run_id, (start, path) in sorted(app_runs_in_volume(files, volume).items()):
            if run_id in done:
                log(f"{run_id}: already in MLflow")
                continue
            try:
                content = files.download(path).contents.read()
            except Exception as error:
                log(f"{run_id}: no trace in {start} ({type(error).__name__})")
                continue
            local = Path(tmp) / run_id / "trace.jsonl"
            local.parent.mkdir(parents=True)
            local.write_bytes(content)
            events = read_events(local)
            tags = {**workflow_tags(events, "app", start), "app_start": start.removeprefix("app-")}
            added.append(_log_trace(trace_spans(events), experiment_id, None, tags))
            log(f"{run_id}: from {start}")
    mlflow.flush_trace_async_logging()
    missing = _read_back(client, added)
    if missing:
        raise RuntimeError(f"{len(missing)} of {len(added)} app traces without spans; upload again")
    return added
