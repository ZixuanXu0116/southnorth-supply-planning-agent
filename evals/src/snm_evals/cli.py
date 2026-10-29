"""Command-line entry point: ``snm-evals``."""

from __future__ import annotations

import argparse
from pathlib import Path

from snm_data.cli import REPO_ROOT
from snm_data.databricks import DatabricksSettings
from snm_data.env import load_env_file
from snm_tools.backend import DatabricksBackend

from .cases import EVALS_ROOT, load_suite
from .refresh import compute_answers, write_answers
from .run import ARMS, RUNS_ROOT, build_harness, render_report, run_batch, score_batch
from .snapshot import verify_loaded_snapshot
from .tracking import configure_tracking, default_experiment, upload_batch


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="snm-evals", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    refresh = commands.add_parser("refresh", help="execute reference SQL and store reference answers")
    refresh.add_argument("--suite", default="seed")
    run = commands.add_parser("run", help="run cases through the agent, then score and report")
    run.add_argument("--suite", default="seed")
    run.add_argument("--arm", action="append", choices=ARMS, help="repeatable; default main")
    run.add_argument("--case", action="append", help="case id (repeatable); default all cases")
    run.add_argument("--split", choices=["dev", "test"], help="only cases of this split")
    run.add_argument("--main-model", help="main-role model ID for this batch (overrides SNM_MODEL_MAIN)")
    run.add_argument("--main-effort", help="main-role effort for this batch (overrides SNM_EFFORT_MAIN)")
    run.add_argument("--parallel", type=int, default=3)
    run.add_argument("--batch", help="batch directory name under runs/evals/ (existing batches are resumed)")
    run.add_argument("--repeat", type=int, default=1, help="repetitions per case and arm")
    run.add_argument(
        "--max-usd", type=float, help="stop launching new runs once this invocation spent this much"
    )
    run.add_argument("--report", action="store_true", help="also write the report to evals/reports/")
    run.add_argument("--no-mlflow", action="store_true", help="do not log the batch to MLflow")
    run.add_argument(
        "--retry-errors", action="store_true", help="run the batch's errored runs again (resumed batch)"
    )
    score = commands.add_parser("score", help="score an existing batch and print the report")
    score.add_argument("--suite", default="seed")
    score.add_argument("--batch", required=True)
    score.add_argument("--report", action="store_true", help="also write the report to evals/reports/")
    upload = commands.add_parser(
        "mlflow-upload", help="log scored batches to MLflow (batch run, one trace per agent run, run files)"
    )
    upload.add_argument("batch", nargs="+", help="batch directory name under runs/evals/")
    upload.add_argument("--replace", action="store_true", help="upload again batches already in MLflow")
    upload.add_argument(
        "--experiment", help="MLflow experiment path (default: SNM_MLFLOW_EXPERIMENT, else ~/snm-agent-evals)"
    )
    planner = commands.add_parser(
        "planner", help="evaluate the planner agent's prepare task on a local engine run"
    )
    planner.add_argument("--planning", help="local planning run (default: latest out/planning/*)")
    planner.add_argument("--per-lever", type=int, default=3)
    planner.add_argument("--parallel", type=int, default=3)
    planner.add_argument("--batch", default="planner-prepare")
    planner.add_argument("--prompt-version", default="v5")
    planner.add_argument("--report", action="store_true", help="also write the report to evals/reports/")
    evaluate = commands.add_parser(
        "planner-evaluate", help="evaluate the planner's evaluate task per prompt version (paired)"
    )
    evaluate.add_argument("--planning", help="local planning run (default: latest out/planning/*)")
    evaluate.add_argument("--versions", nargs="+", default=["v5"])
    evaluate.add_argument("--per-mix", type=int, default=3)
    evaluate.add_argument("--parallel", type=int, default=3)
    evaluate.add_argument("--batch", default="planner-evaluate")
    evaluate.add_argument("--report", action="store_true", help="also write the report to evals/reports/")
    args = parser.parse_args(argv)

    load_env_file(REPO_ROOT / ".env")
    if args.command == "planner-evaluate":
        from .planner_evaluate import render as render_evaluate
        from .planner_evaluate import run_evaluate_suite

        planning = (
            Path(args.planning) if args.planning else sorted((REPO_ROOT / "out" / "planning").glob("*"))[-1]
        )
        cost = REPO_ROOT / "out" / "small" / "raw_ext" / "cost_parameter.parquet"
        rows = run_evaluate_suite(
            planning, cost, RUNS_ROOT / args.batch, args.versions, args.per_mix, args.parallel
        )
        report = render_evaluate(rows)
        print(report)
        if args.report:
            (EVALS_ROOT / "reports" / f"{args.batch}.md").write_text(report, encoding="utf-8")
        return
    if args.command == "planner":
        from .planner import render, run_prepare_suite

        planning = (
            Path(args.planning) if args.planning else sorted((REPO_ROOT / "out" / "planning").glob("*"))[-1]
        )
        cost = REPO_ROOT / "out" / "small" / "raw_ext" / "cost_parameter.parquet"
        rows = run_prepare_suite(
            planning,
            cost,
            RUNS_ROOT / args.batch,
            args.per_lever,
            args.parallel,
            prompt_version=args.prompt_version,
        )
        report = render(rows)
        print(report)
        if args.report:
            (EVALS_ROOT / "reports" / f"{args.batch}.md").write_text(report, encoding="utf-8")
        return
    if args.command == "refresh":
        suite = load_suite(args.suite)
        verify_loaded_snapshot(suite.data_snapshot, DatabricksSettings.from_env())
        backend = DatabricksBackend(DatabricksSettings.from_env())
        try:
            answers = compute_answers(suite, backend)
        finally:
            backend.close()
        path = write_answers(suite, answers)
        print(f"wrote {len(answers['results'])} reference answers to {path}")
    elif args.command in ("run", "score"):
        suite = load_suite(args.suite)
        if args.command == "run":
            snapshot = verify_loaded_snapshot(suite.data_snapshot, DatabricksSettings.from_env())
            build_harness()
            case_ids = [
                case.id
                for case in suite.cases
                if (not args.case or case.id in args.case) and (not args.split or case.split == args.split)
            ]
            requested = {"SNM_MODEL_MAIN": args.main_model, "SNM_EFFORT_MAIN": args.main_effort}
            overrides = {name: value for name, value in requested.items() if value}
            batch_dir = run_batch(
                suite, args.arm or ["main"], case_ids, args.parallel, args.batch,
                repeats=args.repeat, max_usd=args.max_usd, env_overrides=overrides, data_snapshot=snapshot,
                retry_errors=args.retry_errors,
            )  # fmt: skip
        else:
            batch_dir = RUNS_ROOT / args.batch
        report = render_report(suite, score_batch(suite, batch_dir), batch_dir.name)
        (batch_dir / "report.md").write_text(report, encoding="utf-8")
        if args.report:
            target = EVALS_ROOT / "reports" / f"{batch_dir.name}.md"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(report, encoding="utf-8")
        print(report)
        if args.command == "run" and not args.no_mlflow:
            # A resumed batch has new runs, so the batch replaces its earlier upload.
            configure_tracking()
            try:
                upload_batch(batch_dir, default_experiment(), replace=True)
            except Exception as error:
                retry = f"snm-evals mlflow-upload --replace {batch_dir.name}"
                raise SystemExit(f"MLflow upload failed ({error}); retry with `{retry}`") from error
    elif args.command == "mlflow-upload":
        configure_tracking()
        experiment = args.experiment or default_experiment()
        for name in args.batch:
            upload_batch(RUNS_ROOT / name, experiment, replace=args.replace)


if __name__ == "__main__":
    main()
