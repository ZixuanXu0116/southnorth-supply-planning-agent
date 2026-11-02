"""Command-line entry point: ``snm-evals``."""

from __future__ import annotations

import argparse
import json
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


def _planning(given: str | None) -> Path:
    """The planning run a planner suite plays in (default: the latest under out/planning/). Its as-of date
    becomes the case server's today (``SNM_CASE_TODAY``) unless one is set: the suite's supplier dates and
    validity are drawn in that world, and the engine's levers are dated from it."""
    from .flow import pin_world_day

    planning = Path(given) if given else sorted((REPO_ROOT / "out" / "planning").glob("*"))[-1]
    pin_world_day(planning)
    return planning


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
    planner.add_argument("--prompt-version", default="v6")
    planner.add_argument("--report", action="store_true", help="also write the report to evals/reports/")
    evaluate = commands.add_parser(
        "planner-evaluate", help="evaluate the planner's evaluate task per prompt version (paired)"
    )
    evaluate.add_argument("--planning", help="local planning run (default: latest out/planning/*)")
    evaluate.add_argument("--versions", nargs="+", default=["v6"])
    evaluate.add_argument("--per-mix", type=int, default=3)
    evaluate.add_argument("--parallel", type=int, default=3)
    evaluate.add_argument("--batch", default="planner-evaluate")
    evaluate.add_argument("--report", action="store_true", help="also write the report to evals/reports/")
    mutate = commands.add_parser(
        "verifier-mutations", help="catch rate and false alarms of the verifier on mutated recommendations"
    )
    mutate.add_argument("--planning", help="local planning run (default: latest out/planning/*)")
    mutate.add_argument("--per-mix", type=int, default=3)
    mutate.add_argument("--parallel", type=int, default=3)
    mutate.add_argument("--max-usd", type=float, default=5.0, help="stop verifying once this much is spent")
    mutate.add_argument("--batch", default="verifier-mutations")
    mutate.add_argument(
        "--from-store", help="mutate the recommendations already in this store (no planner runs)"
    )
    mutate.add_argument("--only", nargs="+", help="the mutations to run (default: all that apply)")
    mutate.add_argument("--report", action="store_true", help="also write the report to evals/reports/")
    form = commands.add_parser(
        "verifier-form", help="the planner's check forms (none, deterministic, self-check, sub-agent), paired"
    )
    form.add_argument("--planning", help="local planning run (default: latest out/planning/*)")
    form.add_argument(
        "--auditor-model", required=True, help="model of the auditor, another tier than arm D's"
    )
    form.add_argument("--arms", nargs="+", default=["none", "deterministic", "self_check", "subagent"])
    form.add_argument("--versions", nargs="+", default=["v6"], help="planner prompt versions to compare")
    form.add_argument("--repetitions", type=int, default=1)
    form.add_argument("--per-mix", type=int, default=3)
    form.add_argument("--parallel", type=int, default=4)
    form.add_argument("--max-usd", type=float, default=5.0, help="stop planning once this much is spent")
    form.add_argument("--batch", default="verifier-form")
    form.add_argument("--report", action="store_true", help="also write the report to evals/reports/")
    improve = commands.add_parser(
        "improve", help="the semi-automatic improvement loop (decision record 009)"
    ).add_subparsers(dest="step", required=True)
    collect = improve.add_parser("collect", help="draft failure cards for a batch's failures")
    collect.add_argument("--suite", required=True)
    collect.add_argument("--batch", required=True, help="local under runs/evals/, else fetched from MLflow")
    collect.add_argument("--at", help="replay: read the suite, answers, and knowledge base as of this commit")
    propose = improve.add_parser("propose", help="propose a fix for a card and run the guards")
    propose.add_argument("card")
    propose.add_argument(
        "--proposal", type=Path, help="a hand-written proposal (JSON) instead of the proposer"
    )
    screen = improve.add_parser("screen", help="screen a guarded proposal and write the packet")
    screen.add_argument("card")
    screen.add_argument(
        "--max-usd", type=float, default=4.0, help="the screen's cap (owner's approval above)"
    )
    screen.add_argument("--parallel", type=int, default=3)
    decide = improve.add_parser("decide", help="record the owner's decision on a card")
    decide.add_argument("card")
    decide.add_argument("decision", choices=["adopt", "reject", "wontfix", "iterate"])
    decide.add_argument("--note", default="", help="the owner's reason or, for iterate, what to change")
    intake = improve.add_parser("intake", help="cards from flagged answers of the question page")
    intake.add_argument("path", type=Path, help="runs/ask/flags.jsonl or a downloaded thread JSON")
    judge = commands.add_parser(
        "trace-judge", help="trace soundness: a sample of planner runs graded by a judge"
    )
    judge.add_argument("--batches", nargs="+", required=True, help="batch directories under runs/evals/")
    judge.add_argument("--sample", type=int, default=12)
    judge.add_argument("--seed", type=int, default=1)
    judge.add_argument("--batch", required=True, help="output directory under runs/evals/")
    judge.add_argument("--report", action="store_true", help="write evals/reports/<batch>.md")
    docs = commands.add_parser("documents", help="supplier documents: code-only vs. model extractors")
    docs.add_argument("--batch", required=True)
    docs.add_argument("--arms", default="rules,claude-haiku-5-5,claude-sonnet-5-5,claude-opus-5-5")
    docs.add_argument("--effort", default="low")
    docs.add_argument("--only", nargs="*", help="document name prefixes")
    docs.add_argument("--report", action="store_true", help="write evals/reports/<batch>.md")
    args = parser.parse_args(argv)

    load_env_file(REPO_ROOT / ".env")
    if args.command == "improve":
        _improve(args)
        return
    if args.command == "trace-judge":
        from .trace_judge import render as render_judgments
        from .trace_judge import run_trace_judge

        rows = run_trace_judge(args.batches, args.sample, args.seed, RUNS_ROOT / args.batch)
        report = render_judgments(rows, args.batches, args.seed)
        print(report)
        if args.report:
            (EVALS_ROOT / "reports" / f"{args.batch}.md").write_text(report, encoding="utf-8")
        return
    if args.command == "documents":
        from .documents import SET_DIR, render_documents_report, run_suite

        rows = run_suite(SET_DIR, args.arms.split(","), args.effort, RUNS_ROOT / args.batch, args.only)
        report = render_documents_report(rows, args.effort)
        print(report)
        if args.report:
            (EVALS_ROOT / "reports" / f"{args.batch}.md").write_text(report, encoding="utf-8")
        return
    if args.command == "verifier-form":
        from .verifier_form import render as render_form
        from .verifier_form import run_form_suite

        planning = _planning(args.planning)
        cost = REPO_ROOT / "out" / "small" / "raw_ext" / "cost_parameter.parquet"
        out_dir = RUNS_ROOT / args.batch
        rows, spent = run_form_suite(
            planning, cost, out_dir, args.auditor_model, args.arms, args.per_mix, args.parallel, args.max_usd,
            versions=args.versions, repetitions=args.repetitions,
        )  # fmt: skip
        report = render_form(rows, spent, args.auditor_model)
        print(report)
        if args.report:
            (EVALS_ROOT / "reports" / f"{args.batch}.md").write_text(report, encoding="utf-8")
        return
    if args.command == "verifier-mutations":
        from .verifier_mutations import render as render_mutations
        from .verifier_mutations import run_mutation_suite

        planning = _planning(args.planning)
        cost = REPO_ROOT / "out" / "small" / "raw_ext" / "cost_parameter.parquet"
        out_dir = RUNS_ROOT / args.batch
        if args.from_store:
            from .verifier_mutations import MUTATIONS, run_on_store

            names = args.only or list(MUTATIONS)
            rows = run_on_store(
                Path(args.from_store), planning, cost, out_dir, names, args.parallel, args.max_usd
            )
        else:
            rows = run_mutation_suite(planning, cost, out_dir, args.per_mix, args.parallel, args.max_usd)
        spent = json.loads((out_dir / "spent.json").read_text(encoding="utf-8"))["usd"]
        report = render_mutations(rows, spent)
        print(report)
        if args.report:
            (EVALS_ROOT / "reports" / f"{args.batch}.md").write_text(report, encoding="utf-8")
        return
    if args.command == "planner-evaluate":
        from .planner_evaluate import render as render_evaluate
        from .planner_evaluate import run_evaluate_suite

        planning = _planning(args.planning)
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

        planning = _planning(args.planning)
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


def _improve(args: argparse.Namespace) -> None:
    from .improve import loop
    from .improve.intake import intake_flags

    if args.step == "collect":
        ids, skipped = loop.collect_cards(args.suite, args.batch, at=args.at)
        for card_id in ids:
            print(f"card {card_id}: runs/improve/{card_id}/card.md")
        for case_id, reason in skipped:
            print(f"skipped {case_id}: {reason}")
    elif args.step == "propose":
        outcome = loop.propose(args.card, args.proposal)
        proposal, report = outcome["proposal"], outcome["guards"]
        print(f"{args.card}: {proposal.get('root_cause')} -> {proposal.get('layer')}; guards ok={report.ok}")
        for problem in report.problems:
            print(f"  problem: {problem}")
    elif args.step == "screen":
        build_harness()
        state = loop.load_state(args.card)
        suite, _ = loop.suite_at(
            state["suite"], state.get("at"), loop.IMPROVE_ROOT / ".work" / state["batch"]
        )
        settings = DatabricksSettings.from_env()
        snapshot = verify_loaded_snapshot(suite.data_snapshot, settings)
        backend = DatabricksBackend(settings)
        try:
            packet = loop.screen(
                args.card,
                max_usd=args.max_usd,
                parallel=args.parallel,
                backend=backend,
                data_snapshot=snapshot,
            )
        finally:
            backend.close()
        print(Path(packet).read_text(encoding="utf-8"))
    elif args.step == "decide":
        for path in loop.decide(args.card, args.decision, args.note):
            print(f"wrote {path}")
    elif args.step == "intake":
        for card_id in intake_flags(args.path):
            print(f"card {card_id}: runs/improve/{card_id}/card.md (needs a regression case)")


if __name__ == "__main__":
    main()
