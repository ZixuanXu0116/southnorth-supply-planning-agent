"""App agent runs as MLflow traces (decided 2026-11-02): evaluation and production traces in one place.

After each agent run of the pages (the planner, the supplier-document extractor), its ``trace.jsonl`` is
rebuilt into one MLflow trace exactly as an evaluation batch's runs are (``snm_evals.tracking``), in a
background thread so that no page waits on MLflow. App runs go to their own experiment,
``SNM_MLFLOW_APP_EXPERIMENT`` (default ``/Shared/snm-workflow/app``, next to the workflow's evaluation
batches since 2026-11-05; ``off`` turns it off), never to an evaluation experiment, which holds evaluation
results.
Each trace carries the workflow's filter tags (``snm_evals.workflow_mlflow.workflow_tags``) with ``source`` =
``app`` and ``batch`` = the app start (``app-<UTC time>``, the runs mirror's folder). The experiment keeps its
trace data in the runs volume (``SNM_MLFLOW_APP_ARTIFACTS``), written through the workspace's Files API: a
Databricks App cannot reach the storage host that MLflow's default trace store uploads to (connection
refused, 2026-11-02). When the pages start, one ``app_start`` trace checks that this identity can write
there. Every upload, and every failure to upload, is a line in the access log; a failure never fails the run.
"""

from __future__ import annotations

import json
import os
import queue
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

APP_EXPERIMENT_ENV = "SNM_MLFLOW_APP_EXPERIMENT"
DEFAULT_APP_EXPERIMENT = "/Shared/snm-workflow/app"
APP_ARTIFACTS_ENV = "SNM_MLFLOW_APP_ARTIFACTS"
DEFAULT_APP_ARTIFACTS = "dbfs:/Volumes/snm/cases/runs/mlflow-traces/workflow-app"

Record = Callable[..., None]  # Audit.write
LogRun = Callable[[Path, str, dict[str, str]], Any]  # snm_evals.tracking.log_run


def app_experiment(default_on: bool) -> str | None:
    """The experiment app runs go to, or None when tracing is off (in local mode unless asked for)."""
    value = os.environ.get(APP_EXPERIMENT_ENV, DEFAULT_APP_EXPERIMENT if default_on else "off").strip()
    return None if value.lower() in ("", "off") else value


def _log_run(path: Path, experiment: str, tags: dict[str, str]) -> Any:
    from snm_evals.tracking import configure_tracking, log_run, read_events
    from snm_evals.workflow_mlflow import workflow_tags

    configure_tracking()
    artifacts = os.environ.get(APP_ARTIFACTS_ENV, DEFAULT_APP_ARTIFACTS).strip() or None
    tags = {**workflow_tags(read_events(path), "app", tags.get("batch", "-")), **tags}
    return log_run(path, experiment, tags, artifacts)


def app_start_label(now: datetime | None = None) -> str:
    """The name of one app start: the runs mirror's folder and the ``batch`` tag of its traces."""
    return (now or datetime.now(UTC)).strftime("app-%Y%m%dT%H%M%SZ")


class RunTracer:
    """Uploads agent runs to MLflow one at a time, in the background."""

    def __init__(
        self, experiment: str, source: str, record: Record, log: LogRun = _log_run, batch: str = ""
    ) -> None:
        self.experiment, self.source, self.record, self.log = experiment, source, record, log
        self.batch = batch or app_start_label()
        self._queue: queue.Queue[tuple[Path, dict[str, str]] | None] = queue.Queue()
        self._thread = threading.Thread(target=self._work, name="mlflow-traces", daemon=True)

    def start(self, runs_dir: Path) -> RunTracer:
        """Start the uploader and queue the ``app_start`` check."""
        self._thread.start()
        now = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        run_id = f"app-start-{now[:19].replace(':', '').replace('-', '')}"
        probe = runs_dir / "agents" / run_id / "trace.jsonl"
        probe.parent.mkdir(parents=True, exist_ok=True)
        events = [
            {"seq": 1, "ts": now, "event": "run_start", "run_id": run_id, "agent": "app"},
            {"seq": 2, "ts": now, "event": "run_end", "status": "started"},
        ]
        probe.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
        self.submit(probe, {"snm.kind": "app_start", "run_id": run_id})
        return self

    def submit(self, trace_path: Path, tags: dict[str, str]) -> None:
        start = {"batch": self.batch, "app_start": self.batch.removeprefix("app-")}
        self._queue.put((trace_path, {"snm.source": self.source, "source": "app", **start, **tags}))

    def drain(self) -> None:
        """Wait until every queued run is uploaded (tests, shutdown)."""
        self._queue.join()

    def _work(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                path, tags = item
                try:
                    self.log(path, self.experiment, tags)
                    self.record(role="system", action="mlflow_trace", path=None, case_id=tags.get("case_id"),
                                outcome="ok", run=tags.get("run_id"), experiment=self.experiment)  # fmt: skip
                except Exception as error:  # an upload failure never fails the run
                    self.record(role="system", action="mlflow_trace", path=None, case_id=tags.get("case_id"),
                                outcome="error", run=tags.get("run_id"), experiment=self.experiment,
                                error=f"{type(error).__name__}: {error}"[:2000])  # fmt: skip
            finally:
                self._queue.task_done()


def traced_planner(
    planner: Callable[..., dict[str, Any]], tracer: RunTracer
) -> Callable[..., dict[str, Any]]:
    """The planner runner, with each run's trace queued for MLflow."""

    def run(settings: Any, case_id: str, task: str, role: str, as_of: str) -> dict[str, Any]:
        result = planner(settings, case_id, task, role, as_of)
        trace = result.get("trace_path")
        if trace and Path(trace).is_file():
            tags = {"snm.kind": "planner", "run_id": str(result.get("run_id")), "case_id": case_id,
                    "task": task, "status": str(result.get("status"))}  # fmt: skip
            tracer.submit(Path(trace), tags)
        return result

    return run


def traced_extractor(
    extractor: Callable[..., dict[str, Any]], tracer: RunTracer
) -> Callable[..., dict[str, Any]]:
    """The supplier-document extractor, with each model run's trace queued for MLflow."""

    def run(settings: Any, content: bytes, file_name: str, request: dict[str, Any]) -> dict[str, Any]:
        result = extractor(settings, content, file_name, request)
        run_id = result.get("run_id")
        trace = Path(settings.runs_dir) / "agents" / str(run_id) / "trace.jsonl"
        if run_id and trace.is_file():
            tags = {"snm.kind": "extractor", "run_id": str(run_id), "method": str(result.get("method"))}
            tracer.submit(trace, tags)
        return result

    return run
