"""The workflow's runs in MLflow: tags, batch metrics, idempotent uploads, the app's runs."""

from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from snm_evals import workflow_mlflow as wm


def planner_trace(run_id: str, case_id: str, *, status: str = "written", cost: float = 0.02, seconds=10.0):
    refused = [{"tool": "propose_recommendation", "code": "UNRANKED"}] if status == "refused" else []
    return [
        {"seq": 1, "ts": "2026-11-02T10:00:00.000Z", "event": "run_start", "run_id": run_id,
         "case_id": case_id, "task": "evaluate", "role": "purchasing", "model": "anthropic/claude-sonnet-5-5",
         "prompt": {"name": "planner", "version": "v6", "digest": "d"}},
        {"seq": 2, "ts": "2026-11-02T10:00:02.000Z", "event": "assistant", "turn": 1,
         "stop_reason": "toolUse", "usage": {"cost": {"total": cost}},
         "tool_calls": [{"id": "t1", "name": "rank_options", "arguments": {}}]},
        {"seq": 3, "ts": "2026-11-02T10:00:03.000Z", "event": "tool", "name": "rank_options", "id": "t1",
         "duration_ms": 500, "output": "{}"},
        {"seq": 4, "ts": "2026-11-02T10:00:10.000Z", "event": "run_end", "run_id": run_id, "status": status,
         "written": {"tool": "propose_recommendation"} if status == "written" else None,
         "refused_writes": refused, "usage": {"cost_usd": cost}, "turns": 1, "wall_clock_s": seconds},
    ]  # fmt: skip


def extractor_trace(run_id: str):
    return [
        {"seq": 1, "ts": "2026-11-02T11:00:00.000Z", "event": "run_start", "run_id": run_id,
         "model": "anthropic/claude-haiku-5-5",
         "prompt": {"name": "extractor", "version": "v1", "digest": "e"},
         "document": {"kind": "pdf", "file_name": "s01-clean.pdf", "pages": 1}},
        {"seq": 2, "ts": "2026-11-02T11:00:03.000Z", "event": "assistant", "turn": 1,
         "stop_reason": "toolUse", "usage": {},
         "tool_calls": [{"id": "c1", "name": "record_extraction", "arguments": {"options": []}}]},
        # The extractor's tool events carry no call id.
        {"seq": 3, "ts": "2026-11-02T11:00:03.500Z", "event": "tool", "name": "record_extraction",
         "is_error": False, "output": "Recorded."},
        {"seq": 4, "ts": "2026-11-02T11:00:04.000Z", "event": "run_end", "run_id": run_id,
         "status": "extracted", "extraction": {"options": []}, "usage": {"cost_usd": 0.01}, "turns": 1,
         "wall_clock_s": 4.0},
    ]  # fmt: skip


def write_trace(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def write_batch(root: Path) -> Path:
    batch = root / "planner-b1"
    write_trace(
        batch / "runs-none-v6-r1" / "r1" / "trace.jsonl", planner_trace("r1", "C-1", cost=0.02, seconds=10)
    )
    write_trace(
        batch / "runs-none-v6-r2" / "r2" / "trace.jsonl",
        planner_trace("r2", "C-2", status="refused", cost=0.04, seconds=20),
    )
    rows = [{"case_id": "C-1", "arm": "none", "status": "written"}, {"case_id": "C-2", "arm": "none"}]
    (batch / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return batch


@pytest.fixture
def local_mlflow(monkeypatch, tmp_path):
    mlflow = pytest.importorskip("mlflow")
    monkeypatch.setenv("MLFLOW_TRACKING_URI", (tmp_path / "mlruns").as_uri())
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    mlflow.set_tracking_uri((tmp_path / "mlruns").as_uri())
    mlflow.tracing.reset()  # the trace exporter is set up once per process, for the store of that time
    return mlflow


def test_the_tags_of_a_planner_and_an_extractor_run() -> None:
    assert wm.workflow_tags(planner_trace("r1", "C-1"), "eval", "b1") == {
        "agent": "planner", "task": "evaluate", "case_id": "C-1", "status": "written", "source": "eval",
        "batch": "b1", "prompt_version": "v6", "model": "anthropic/claude-sonnet-5-5", "run_id": "r1",
        "cost_usd": "0.0200",
    }  # fmt: skip
    tags = wm.workflow_tags(extractor_trace("x1"), "app", "app-20261102T100000Z")
    assert (tags["agent"], tags["task"], tags["case_id"], tags["status"]) == (
        "extractor",
        "extract",
        "-",
        "extracted",
    )
    assert tags["document"] == "s01-clean.pdf" and tags["prompt_version"] == "v1"
    assert wm.workflow_tags([{"event": "run_start", "run_id": "r"}], "eval", "b")["status"] == "unfinished"


def test_batch_metrics_by_hand() -> None:
    runs = [
        planner_trace("r1", "C-1", cost=0.02, seconds=10),
        planner_trace("r2", "C-2", status="refused", cost=0.04, seconds=20),
    ]
    params, metrics = wm.batch_summary([{"arm": "none"}, {"arm": "none"}], runs)
    assert (
        params["agents"] == "planner"
        and params["prompt_versions"] == "planner v6"
        and params["arms"] == "none"
    )
    assert metrics["runs"] == 2 and metrics["status/written"] == 1 and metrics["status/refused"] == 1
    assert metrics["errors"] == 0 and metrics["refused_writes"] == 1
    assert metrics["cost_usd_total"] == pytest.approx(0.06) and metrics["cost_usd_mean"] == pytest.approx(
        0.03
    )
    assert metrics["latency_s_mean"] == 15.0 and metrics["latency_s_p95"] == pytest.approx(19.5)

    # A batch without traces is measured from its rows.
    rows = [{"status": "extracted", "cost_usd": 0.0, "seconds": 0.1}, {"status": "error", "seconds": 0.3}]
    _, metrics = wm.batch_summary(rows, [])
    assert metrics["runs"] == 0 and metrics["rows"] == 2 and metrics["errors"] == 1
    assert metrics["latency_s_mean"] == pytest.approx(0.2)


def test_upload_a_batch_once_with_its_traces_and_tags(local_mlflow, tmp_path) -> None:
    batch = write_batch(tmp_path / "batches")
    messages: list[str] = []
    run_id = wm.upload_workflow_batch(batch, "evals", artifact_location=None, log=messages.append)

    client = local_mlflow.MlflowClient()
    run = client.get_run(run_id)
    assert run.data.tags["snm.upload"] == "complete" and run.data.tags["source"] == "eval"
    assert run.data.metrics["runs"] == 2 and run.data.metrics["status/refused"] == 1
    assert run.data.params["models"] == "anthropic/claude-sonnet-5-5"
    assert {item.path for item in client.list_artifacts(run_id)} == {"results.jsonl"}
    traces = client.search_traces(locations=[run.info.experiment_id])
    by_run = {trace.info.tags["run_id"]: trace for trace in traces}
    assert set(by_run) == {"r1", "r2"}
    first = by_run["r1"].info.tags
    assert (first["agent"], first["task"], first["case_id"], first["source"], first["batch"]) == (
        "planner",
        "evaluate",
        "C-1",
        "eval",
        "planner-b1",
    )
    assert first["group"] == "runs-none-v6-r1" and by_run["r2"].info.tags["status"] == "refused"
    assert by_run["r1"].info.trace_metadata["mlflow.sourceRun"] == run_id
    root = next(span for span in by_run["r1"].data.spans if span.name == "agent_run")
    assert root.outputs["written"] == {"tool": "propose_recommendation"}
    assert wm.count_workflow_batch(batch, "evals") == (2, 2, True)

    # The same batch again adds nothing.
    assert wm.upload_workflow_batch(batch, "evals", artifact_location=None, log=messages.append) == run_id
    assert "already in MLflow" in messages[-1]
    assert len(client.search_traces(locations=[run.info.experiment_id])) == 2


def test_a_batch_without_traces_is_one_run_with_metrics(local_mlflow, tmp_path) -> None:
    batch = tmp_path / "documents-rules"
    batch.mkdir()
    (batch / "rows.jsonl").write_text(
        json.dumps({"status": "extracted", "seconds": 0.1}) + "\n", encoding="utf-8"
    )
    run_id = wm.upload_workflow_batch(batch, "evals", artifact_location=None, log=lambda _: None)
    run = local_mlflow.MlflowClient().get_run(run_id)
    assert run.data.metrics["runs"] == 0 and run.data.metrics["rows"] == 1
    assert wm.count_workflow_batch(batch, "evals") == (0, 0, True)
    with pytest.raises(ValueError, match="neither a rows file"):
        (tmp_path / "empty").mkdir()
        wm.upload_workflow_batch(tmp_path / "empty", "evals", artifact_location=None)


class FakeFiles:
    """The Files API over a dict of volume paths."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def list_directory_contents(self, path: str):
        prefix = path.rstrip("/") + "/"
        names = sorted({p[len(prefix) :].split("/")[0] for p in self.files if p.startswith(prefix)})
        if not names:
            raise FileNotFoundError(path)
        return [SimpleNamespace(name=name, path=prefix + name) for name in names]

    def download(self, path: str):
        return SimpleNamespace(contents=io.BytesIO(self.files[path]))


def test_the_apps_earlier_runs_once_each_with_their_first_app_start(local_mlflow) -> None:
    def trace(events):
        return "".join(json.dumps(e) + "\n" for e in events).encode()

    volume = "/Volumes/snm/cases/runs"
    files = FakeFiles(
        {
            f"{volume}/app-20261102T010659Z/agents/r1/trace.jsonl": trace(planner_trace("r1", "C-1")),
            # A later start's folder holds the earlier run again, a new run, and a probe of the trace store.
            f"{volume}/app-20261102T024930Z/agents/r1/trace.jsonl": trace(planner_trace("r1", "C-1")),
            f"{volume}/app-20261102T024930Z/agents/x1/trace.jsonl": trace(extractor_trace("x1")),
            f"{volume}/app-20261102T024930Z/agents/app-start-20261102T024929/trace.jsonl": b"{}\n",
            f"{volume}/eval-backup-20261105/a.tar.gz": b"",
        }
    )
    assert {run: start for run, (start, _) in wm.app_runs_in_volume(files, volume).items()} == {
        "r1": "app-20261102T010659Z",
        "x1": "app-20261102T024930Z",
    }
    added = wm.upload_app_runs(files, "app", artifact_location=None, volume=volume, log=lambda _: None)
    assert len(added) == 2
    client = local_mlflow.MlflowClient()
    experiment_id = client.get_experiment_by_name("app").experiment_id
    traces = {t.info.tags["run_id"]: t for t in client.search_traces(locations=[experiment_id])}
    tags = {run_id: trace.info.tags for run_id, trace in traces.items()}
    tool = next(span for span in traces["x1"].data.spans if span.name == "record_extraction")
    assert tool.inputs == {"options": []}
    assert tags["r1"]["batch"] == "app-20261102T010659Z" and tags["r1"]["app_start"] == "20261102T010659Z"
    assert tags["r1"]["source"] == "app" and tags["x1"]["agent"] == "extractor"
    assert wm.upload_app_runs(files, "app", artifact_location=None, volume=volume, log=lambda _: None) == []
