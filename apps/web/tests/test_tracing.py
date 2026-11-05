"""App agent runs queued for MLflow (decided 2026-11-02): in the background, never failing the run."""

from __future__ import annotations

from pathlib import Path

from snm_web.tracing import RunTracer, app_experiment, traced_extractor, traced_planner


class Settings:
    def __init__(self, runs_dir: Path) -> None:
        self.runs_dir = runs_dir


def test_runs_are_queued_with_their_tags_and_a_failed_upload_is_only_recorded(tmp_path):
    uploaded, records = [], []

    def log(path, experiment, tags):
        if tags.get("run_id") == "bad":
            raise RuntimeError("PERMISSION_DENIED")
        uploaded.append((path.name, experiment, tags))

    record = lambda **entry: records.append(entry)  # noqa: E731
    tracer = RunTracer("/Shared/snm-workflow/app", "lakehouse", record, log, batch="app-20261102T100000Z")
    tracer.start(tmp_path)
    trace = tmp_path / "agents" / "r1" / "trace.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text('{"event": "run_start", "ts": "2026-11-02T10:00:00.000Z"}\n', encoding="utf-8")

    def planner(settings, case_id, task, role, as_of):
        return {"status": "written", "run_id": "r1", "trace_path": str(trace)}

    def failing_planner(settings, case_id, task, role, as_of):
        return {"status": "written", "run_id": "bad", "trace_path": str(trace)}

    assert (
        traced_planner(planner, tracer)(None, "C-1", "evaluate", "purchasing", "2026-11-02")["run_id"] == "r1"
    )
    assert traced_planner(failing_planner, tracer)(None, "C-1", "evaluate", "purchasing", "2026-11-02")[
        "status"
    ]
    extracted = traced_extractor(lambda s, c, f, r: {"run_id": "r1", "method": "claude-haiku-5-5"}, tracer)
    extracted(Settings(tmp_path), b"%PDF", "quote.pdf", {})
    traced_extractor(lambda s, c, f, r: {"run_id": None, "method": "rules"}, tracer)(
        Settings(tmp_path), b"", "", {}
    )
    tracer.drain()

    kinds = [tags["snm.kind"] for _, _, tags in uploaded]
    assert kinds == ["app_start", "planner", "extractor"]
    assert all(
        experiment == "/Shared/snm-workflow/app"
        and tags["snm.source"] == "lakehouse"
        and tags["source"] == "app"
        for _, experiment, tags in uploaded
    )
    expected = {"snm.source": "lakehouse", "source": "app", "batch": "app-20261102T100000Z",
                "app_start": "20261102T100000Z", "snm.kind": "planner", "run_id": "r1", "case_id": "C-1",
                "task": "evaluate", "status": "written"}  # fmt: skip
    assert uploaded[1][2] == expected
    assert [r["outcome"] for r in records] == ["ok", "ok", "error", "ok"]
    assert "PERMISSION_DENIED" in records[2]["error"]


def test_tracing_is_on_in_the_app_and_off_locally_unless_asked(monkeypatch):
    monkeypatch.delenv("SNM_MLFLOW_APP_EXPERIMENT", raising=False)
    assert app_experiment(default_on=True) == "/Shared/snm-workflow/app"
    assert app_experiment(default_on=False) is None
    monkeypatch.setenv("SNM_MLFLOW_APP_EXPERIMENT", "off")
    assert app_experiment(default_on=True) is None
