"""``snm-web``: serve the purchasing and supplier pages (decision record 005).

    uv run snm-web serve                      # local role play on out/web/store, the latest planning run
    uv run snm-web serve --databricks         # the lakehouse store (planner: SNM_CASE_BACKEND=databricks)

In local mode the store is synced with the planning run when it is empty, so the queue has cases.
"""

from __future__ import annotations

import argparse
import atexit
import os
from pathlib import Path

from snm_cases.cli import _databricks_service
from snm_cases.planning import LocalPlanningData
from snm_cases.server import REPO_ROOT, default_planning_dir
from snm_cases.service import CaseService
from snm_cases.store import LocalCaseStore

from .agents import ENVIRONMENT_HINT, missing_environment, run_extractor, run_planner
from .app import Audit, WebSettings, create_app
from .tracing import RunTracer, app_experiment, app_start_label, traced_extractor, traced_planner

DEFAULT_STORE = REPO_ROOT / "out" / "web" / "store"
DEFAULT_RUNS = REPO_ROOT / "runs" / "web"
HARNESS_CLI = REPO_ROOT / "harness" / "dist" / "cli" / "case.js"
COST_PARAMETER = Path(
    os.environ.get("SNM_COST_PARAMETER", REPO_ROOT / "out" / "small" / "raw_ext" / "cost_parameter.parquet")
)


def _tracer(settings: WebSettings, default_on: bool, started: str) -> RunTracer | None:
    """Agent runs traced in MLflow (tracing.py): on in a deployed app, on request in local mode."""
    experiment = app_experiment(default_on)
    if experiment is None:
        return None
    record = Audit(settings.runs_dir / "access.jsonl").write
    tracer = RunTracer(experiment, settings.mode, record, batch=started).start(settings.runs_dir)
    print(f"agent runs traced to the MLflow experiment {experiment}")
    return tracer


def build(args: argparse.Namespace):
    started = app_start_label()  # the runs mirror's folder and the batch tag of this start's traces
    if args.databricks:
        settings = WebSettings(mode="lakehouse", runs_dir=Path(args.runs), harness_cli=HARNESS_CLI)
        planner, extractor = run_planner, run_extractor
        tracer = _tracer(settings, default_on=True, started=started)
        if tracer is not None:
            planner, extractor = traced_planner(planner, tracer), traced_extractor(extractor, tracer)
        if args.case_server_port:
            # A deployed app: one resident case server instead of one per planner run (resident.py).
            from .resident import ResidentCaseServer

            resident = ResidentCaseServer(args.case_server_port).start()
            atexit.register(resident.stop)

            def planner(*arguments, run=planner):
                resident.ensure()
                return run(*arguments)

        if args.runs_volume:
            # A deployed app: the access log and the agent traces copied to a volume (mirror.py).
            planner = _mirrored(settings.runs_dir, args.runs_volume, planner, started)
        return create_app(_databricks_service(), settings, planner=planner, extractor=extractor), settings
    planning_dir = Path(args.planning) if args.planning else default_planning_dir()
    store_dir = Path(args.store)
    service = CaseService(LocalCaseStore(store_dir), LocalPlanningData(planning_dir, COST_PARAMETER))
    if not service.store.events():
        counts = service.sync_engine_run(f"web-seed-{planning_dir.name}")
        print(f"seeded the local store from {planning_dir}: {counts}")
    settings = WebSettings(
        mode="local", runs_dir=Path(args.runs), store_dir=store_dir, planning_dir=planning_dir,
        harness_cli=HARNESS_CLI,
    )  # fmt: skip
    planner, extractor = run_planner, run_extractor
    tracer = _tracer(settings, default_on=args.mlflow, started=started)
    if tracer is not None:
        planner, extractor = traced_planner(planner, tracer), traced_extractor(extractor, tracer)
    return create_app(service, settings, planner=planner, extractor=extractor), settings


def _mirrored(runs_dir: Path, volume: str, planner, started: str):
    """Start copying the runs directory to the volume (one folder per app start) and copy right after each
    planner run."""
    from snm_data.databricks import DatabricksSettings, workspace_client

    from .mirror import RunsMirror

    client = []

    def files():
        if not client:
            client.append(workspace_client(DatabricksSettings.from_env()))
        return client[0]

    mirror = RunsMirror(runs_dir, f"{volume.rstrip('/')}/{started}", files).start()
    atexit.register(mirror.stop)
    print(f"runs mirrored to {mirror.target}")

    def run(*arguments):
        try:
            return planner(*arguments)
        finally:
            mirror.kick()

    return run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snm-web", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve", help="serve the pages")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--store", default=str(DEFAULT_STORE), help="local case store directory")
    serve.add_argument("--planning", help="local planning run directory (default: latest out/planning/*)")
    serve.add_argument("--runs", default=str(DEFAULT_RUNS), help="audit log and agent runs directory")
    serve.add_argument("--databricks", action="store_true", help="use the lakehouse case store")
    serve.add_argument(
        "--mlflow",
        action="store_true",
        help="local mode: trace agent runs in MLflow as a deployed app does (SNM_MLFLOW_APP_EXPERIMENT)",
    )
    serve.add_argument(
        "--case-server-port",
        type=int,
        help="with --databricks: start the planner's case server once on this localhost port",
    )
    serve.add_argument(
        "--runs-volume",
        help="with --databricks: copy the audit log and agent traces to this volume, e.g. "
        "/Volumes/snm/cases/runs",
    )
    args = parser.parse_args(argv)
    if args.command == "serve":
        import uvicorn

        app, settings = build(args)
        print(
            f"{settings.mode} mode; audit log {settings.runs_dir / 'access.jsonl'}; http://{args.host}:{args.port}"
        )
        missing = missing_environment()
        if settings.planner_available and missing:
            print(f"planner buttons need {', '.join(missing)}: {ENVIRONMENT_HINT}")
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
