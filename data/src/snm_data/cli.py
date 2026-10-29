"""Command-line entry point: ``snm-data``."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .contracts import ContractRegistry
from .env import load_env_file

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONTRACTS = REPO_ROOT / "data_contracts"
DEFAULT_CREDENTIALS = REPO_ROOT / ".secrets" / "role-credentials.env"


def _cmd_validate_contracts(args: argparse.Namespace) -> int:
    registry = ContractRegistry.load(args.contracts)
    print(f"{len(registry)} contracts valid")
    return 0


def _cmd_generate(args: argparse.Namespace) -> int:
    from .generator.run import generate, resolve_profile, write

    profile = resolve_profile(args.profile)
    registry = ContractRegistry.load(args.contracts)
    started = time.perf_counter()
    result = generate(profile, registry)
    out_dir = args.out / profile.name
    manifest = write(result, profile, out_dir, args.contracts)
    elapsed = time.perf_counter() - started
    total = sum(frame.height for frame in result.tables.values())
    print(f"profile={profile.name} tables={len(result.tables)} rows={total:,} seconds={elapsed:.1f}")
    print(f"manifest: {manifest}")
    if result.violations:
        print(f"{len(result.violations)} contract violations:", file=sys.stderr)
        for violation in result.violations[:50]:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    return 0


def _cmd_load(args: argparse.Namespace) -> int:
    from .databricks import DatabricksSettings
    from .load import load, setup_statements, table_statements

    registry = ContractRegistry.load(args.contracts)
    out_dir = args.out / args.profile
    if args.dry_run:
        tables = sorted(json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))["row_counts"])
        catalog = os.environ.get("SNM_CATALOG", "snm")
        statements = setup_statements(catalog, {table.split(".")[0] for table in tables})
        for table in tables:
            statements += table_statements(registry[table], catalog, args.profile)
        print(";\n\n".join(statements) + ";")
        return 0
    settings = DatabricksSettings.from_env()
    report = load(out_dir, registry, settings)
    print(f"loaded {len(report.tables)} tables into catalog {report.catalog} in {report.seconds}s")
    for error in report.errors:
        print(f"  - {error}", file=sys.stderr)
    if report.errors:
        return 1
    from .governance import govern, is_deployed

    # Recreated tables lose their row filters, masks, and grants; restore them once governance exists.
    if is_deployed(settings):
        govern(registry, settings, identities=False, log=lambda _: None)
        print("re-applied row filters, column masks, and grants")
    return 0


def _cmd_govern(args: argparse.Namespace) -> int:
    from .databricks import DatabricksSettings
    from .governance import govern, issue_credentials, plan_statements, validate
    from .semantic import load_metric_views

    registry = ContractRegistry.load(args.contracts)
    if args.dry_run:
        metric_views = load_metric_views()
        problems = validate(registry, metric_views)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        catalog = os.environ.get("SNM_CATALOG", "snm")
        print(";\n\n".join(plan_statements(registry, catalog, metric_views)) + ";")
        return 1 if problems else 0
    settings = DatabricksSettings.from_env()
    if args.issue_credentials:
        issue_credentials(settings, args.issue_credentials, roles=args.roles)
        return 0
    report = govern(registry, settings, check_only=args.check, log=print if args.verbose else lambda _: None)
    if args.check:
        for item in report.drift:
            print(f"  - {item}")
        print("no drift" if not report.drift else f"{len(report.drift)} differences")
        return 1 if report.drift else 0
    print(f"governance deployed: {len(report.actions)} changes ({len(report.drift)} differences fixed)")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    from .databricks import DatabricksSettings, sql_connection
    from .governance import govern, is_deployed, validate
    from .semantic import build_statements, load_metric_views

    registry = ContractRegistry.load(args.contracts)
    metric_views = load_metric_views()
    problems = validate(registry, metric_views)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    if problems:
        return 1
    if args.dry_run:
        catalog = os.environ.get("SNM_CATALOG", "snm")
        print(";\n\n".join(build_statements(registry, metric_views, catalog)) + ";")
        return 0
    settings = DatabricksSettings.from_env()
    statements = build_statements(registry, metric_views, settings.catalog)
    with sql_connection(settings) as connection, connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    print(f"built {len(statements)} schemas and views ({len(metric_views)} metric views)")
    if is_deployed(settings):
        govern(registry, settings, identities=False, log=lambda _: None)
        print("re-applied grants")
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    from datetime import date

    from .planning.params import PlanningParams
    from .planning.run import run_plan

    registry = ContractRegistry.load(args.contracts)
    params = PlanningParams.load(args.params)
    profile_dir = (args.out / args.from_profile) if args.from_profile else None
    if args.as_of:
        as_of = date.fromisoformat(args.as_of)
    elif profile_dir is not None:
        from .generator.run import resolve_profile

        as_of = resolve_profile(args.from_profile).ecc_as_of.date()
    else:
        from .databricks import DatabricksSettings
        from .generator.run import resolve_profile
        from .load import read_snapshots

        # The as-of date is the ECC extraction date of the loaded profile.
        loaded = read_snapshots(DatabricksSettings.from_env(), ["raw_sap_ecc"]).get("raw_sap_ecc", {})
        as_of = resolve_profile(loaded.get("profile", "small")).ecc_as_of.date()
    settings = None
    if profile_dir is None:
        from .databricks import DatabricksSettings

        settings = DatabricksSettings.from_env()
    out_dir = args.out / "planning" / as_of.isoformat()
    report = run_plan(registry, out_dir, as_of, params, settings=settings, profile_dir=profile_dir)
    print(
        f"as_of={report.as_of} engine={report.engine_version} params={report.params_digest} "
        f"rows={report.rows} cases={report.cases} seconds={report.seconds} published={report.published}"
    )
    for violation in report.violations[:20]:
        print(f"  - {violation}", file=sys.stderr)
    return 1 if report.violations else 0


def _cmd_snapshot(args: argparse.Namespace) -> int:
    from .databricks import DatabricksSettings
    from .load import read_snapshots

    snapshots = read_snapshots(DatabricksSettings.from_env(), args.schema)
    print(json.dumps(snapshots, indent=2))
    return 0


def _cmd_oracle(args: argparse.Namespace) -> int:
    import json
    from datetime import date, timedelta

    from .planning.history import HISTORY_TABLES, load_tables
    from .planning.oracle import evaluate_day, summarize
    from .planning.params import PlanningParams
    from .planning.run import RAW_INPUT_TABLES

    tables = load_tables(args.out / args.profile, RAW_INPUT_TABLES + HISTORY_TABLES)
    params = PlanningParams.load(args.params)
    start = date.fromisoformat(args.start)
    outcomes = []
    for week in range(args.weeks):
        outcomes += evaluate_day(
            tables, start + timedelta(weeks=week), args.horizon, params, args.late_split_discount
        )
    report = {"cases": len(outcomes), "all": summarize(outcomes)}
    for condition in ("shortage", "warning"):
        subset = [outcome for outcome in outcomes if outcome.condition == condition]
        report[condition] = {"cases": len(subset), **summarize(subset)}
    for level in ("A", "B", "C"):
        subset = [outcome for outcome in outcomes if outcome.criticality == level]
        report[f"class_{level}"] = {"cases": len(subset), **summarize(subset)}
    report["qualified_cases"] = sum(outcome.qualified for outcome in outcomes)
    print(json.dumps(report, indent=1))
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    import json
    import statistics
    from datetime import date, timedelta

    from .planning.history import HISTORY_TABLES, load_tables, replay
    from .planning.params import PlanningParams
    from .planning.run import RAW_INPUT_TABLES

    tables = load_tables(args.out / args.profile, RAW_INPUT_TABLES + HISTORY_TABLES)
    start = date.fromisoformat(args.start)
    days = [start + timedelta(weeks=week) for week in range(args.weeks)]
    rows = replay(tables, days, args.horizon, PlanningParams.load(args.params))
    totals = {
        key: sum(getattr(row, key) for row in rows)
        for key in ("predicted", "realized", "hits", "late", "flagged")
    }
    errors = [error for row in rows for error in row.timing_errors]
    totals["recall"] = round(totals["hits"] / totals["realized"], 3) if totals["realized"] else None
    totals["flagged_share"] = round(totals["flagged"] / totals["realized"], 3) if totals["realized"] else None
    totals["median_timing_error_days"] = statistics.median(errors) if errors else None
    print(json.dumps(totals))
    return 0


def _cmd_forecast_backtest(args: argparse.Namespace) -> int:
    from datetime import date, timedelta

    from .planning.backtest import accuracy, forecast_windows, render_report
    from .planning.history import HISTORY_TABLES, load_tables
    from .planning.run import RAW_INPUT_TABLES

    tables = load_tables(args.out / args.profile, RAW_INPUT_TABLES + HISTORY_TABLES)
    start = date.fromisoformat(args.start)
    days = [start + timedelta(weeks=week) for week in range(args.weeks)]
    windows = forecast_windows(tables, days)
    target = args.out / "backtest" / f"{args.report}.parquet"
    target.parent.mkdir(parents=True, exist_ok=True)
    windows.write_parquet(target)
    report = REPO_ROOT / "evals" / "reports" / f"{args.report}.md"
    report.write_text(render_report(windows, args.report, days, args.profile), encoding="utf-8")
    print(
        accuracy(windows, ["horizon"]).select(
            "horizon", "windows", "bias", "wape", "band", "coverage", "verdict"
        )
    )
    print(f"windows: {target}; report: {report}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snm-data", description=__doc__)
    parser.add_argument("--contracts", type=Path, default=DEFAULT_CONTRACTS, help="contracts directory")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate-contracts", help="validate all table contracts")
    validate.set_defaults(func=_cmd_validate_contracts)

    generate = commands.add_parser("generate", help="generate synthetic data as Parquet")
    generate.add_argument("--profile", default="small", help="test | small | full")
    generate.add_argument("--out", type=Path, default=REPO_ROOT / "out", help="output root directory")
    generate.set_defaults(func=_cmd_generate)

    load = commands.add_parser("load", help="load a generated profile into Databricks (env credentials)")
    load.add_argument("--profile", default="small", help="profile directory under --out")
    load.add_argument("--out", type=Path, default=REPO_ROOT / "out", help="output root directory")
    load.add_argument("--dry-run", action="store_true", help="print the SQL instead of executing it")
    load.set_defaults(func=_cmd_load)

    govern = commands.add_parser(
        "govern", help="deploy role identities, grants, row filters, and column masks (owner credentials)"
    )
    govern.add_argument("--dry-run", action="store_true", help="print the SQL of a full deployment")
    govern.add_argument("--check", action="store_true", help="report drift only (exit 1 on drift)")
    govern.add_argument("--verbose", action="store_true", help="print every change")
    govern.add_argument(
        "--issue-credentials",
        type=Path,
        nargs="?",
        const=DEFAULT_CREDENTIALS,
        help=f"rotate the role principals' OAuth secrets and write them to a private file "
        f"(default {DEFAULT_CREDENTIALS.relative_to(REPO_ROOT)})",
    )
    govern.add_argument(
        "--roles",
        nargs="+",
        help="with --issue-credentials: only these roles (the others keep their secrets)",
    )
    govern.set_defaults(func=_cmd_govern)

    build = commands.add_parser(
        "build", help="create the curated views and metric views from data/pipelines and semantic/ (owner)"
    )
    build.add_argument("--dry-run", action="store_true", help="print the SQL instead of executing it")
    build.set_defaults(func=_cmd_build)

    plan = commands.add_parser(
        "plan", help="run the replenishment engine and publish the planning tables (owner credentials)"
    )
    plan.add_argument("--as-of", help="as-of date (YYYY-MM-DD); default the ECC extraction date")
    plan.add_argument("--params", type=Path, help="YAML file overriding engine parameters")
    plan.add_argument("--out", type=Path, default=REPO_ROOT / "out", help="output root directory")
    plan.add_argument(
        "--from-profile", help="offline: read inputs from a generated profile under --out, not Databricks"
    )
    plan.set_defaults(func=_cmd_plan)

    replay = commands.add_parser(
        "replay", help="run the engine on past days of a generated profile and score it against what followed"
    )
    replay.add_argument("--profile", default="small", help="generated profile under --out")
    replay.add_argument("--out", type=Path, default=REPO_ROOT / "out", help="output root directory")
    replay.add_argument("--start", default="2026-05-04", help="first as-of day (YYYY-MM-DD)")
    replay.add_argument("--weeks", type=int, default=16, help="number of weekly as-of days")
    replay.add_argument("--horizon", type=int, default=30, help="days scored after each as-of day")
    replay.add_argument("--params", type=Path, help="YAML file overriding engine parameters")
    replay.set_defaults(func=_cmd_replay)

    backtest = commands.add_parser(
        "forecast-backtest", help="measure the engine's demand forecast against what followed (record 002)"
    )
    backtest.add_argument("--profile", default="small", help="generated profile under --out")
    backtest.add_argument("--out", type=Path, default=REPO_ROOT / "out", help="output root directory")
    backtest.add_argument("--start", default="2025-08-04", help="first weekly as-of day (YYYY-MM-DD)")
    backtest.add_argument("--weeks", type=int, default=52, help="number of weekly as-of days")
    backtest.add_argument(
        "--report", default="forecast-accuracy-small", help="report name under evals/reports"
    )
    backtest.set_defaults(func=_cmd_forecast_backtest)

    oracle = commands.add_parser(
        "oracle", help="price every option of past engine cases on the realized demand and compare policies"
    )
    oracle.add_argument("--profile", default="small", help="generated profile under --out")
    oracle.add_argument("--out", type=Path, default=REPO_ROOT / "out", help="output root directory")
    oracle.add_argument("--start", default="2026-05-04", help="first as-of day (YYYY-MM-DD)")
    oracle.add_argument("--weeks", type=int, default=12, help="number of weekly as-of days")
    oracle.add_argument("--horizon", type=int, default=60, help="days of realized demand per case")
    oracle.add_argument("--params", type=Path, help="YAML file overriding engine parameters")
    oracle.add_argument(
        "--late-split-discount",
        type=float,
        help="add a synthetic later, cheaper split offer at this discount",
    )
    oracle.set_defaults(func=_cmd_oracle)

    snapshot = commands.add_parser("snapshot", help="show the data snapshot recorded on the loaded schemas")
    snapshot.add_argument(
        "--schema", action="append", default=None, help="schema (repeatable); default the raw schemas"
    )
    snapshot.set_defaults(func=_cmd_snapshot)

    args = parser.parse_args(argv)
    if args.command == "snapshot" and not args.schema:
        args.schema = ["raw_sap_ecc", "raw_sap_ewm", "raw_ext"]
    load_env_file(REPO_ROOT / ".env")
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
