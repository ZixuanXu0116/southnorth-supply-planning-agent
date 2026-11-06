"""Deploy the workflow's operations: the ``snm.ops`` tables, two jobs, and the dashboard (docs/DEPLOYMENT.md).

    uv run python deploy/databricks/ops_deploy.py tables        # schema and tables, from data_contracts/ops
    uv run python deploy/databricks/ops_deploy.py bundle        # upload the job scripts and their modules
    uv run python deploy/databricks/ops_deploy.py jobs          # the daily job (on), uptime check (paused)
    uv run python deploy/databricks/ops_deploy.py dashboard     # create or update, then publish it
    uv run python deploy/databricks/ops_deploy.py check         # run every dataset of the dashboard once
    uv run python deploy/databricks/ops_deploy.py recount       # the cards' SQL against snm_evals.ops.cards
    uv run python deploy/databricks/ops_deploy.py uptime on|off # resume or pause the uptime check

Run with the owner's workspace settings (DATABRICKS_HOST, DATABRICKS_TOKEN, DATABRICKS_WAREHOUSE_ID). The
jobs run serverless, as the owner, with the modules uploaded to ``<home>/snm-ops``.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).parent))

import ops_dashboard  # noqa: E402

DAILY_JOB, UPTIME_JOB = "snm-ops-daily", "snm-uptime-check"
DASHBOARD = "Replenishment workflow operations"
DAILY_CRON = "0 17 10 * * ?"  # 10:17 UTC (04:17 CT, 05:17 in summer), once a day
UPTIME_CRON = "0 0/10 * * * ?"  # every 10 minutes while resumed
MODULES = ["__init__.py", "ops.py", "trace_checks.py", "workflow_mlflow.py", "tracking.py"]


def _client():
    from snm_data.databricks import DatabricksSettings, workspace_client

    return workspace_client(DatabricksSettings.from_env())


def _home(client) -> str:
    return f"/Workspace/Users/{client.current_user.me().user_name}/snm-ops"


def tables() -> None:
    from snm_data.contracts import ContractRegistry
    from snm_data.databricks import DatabricksSettings, sql_connection
    from snm_data.ddl import schema_ddl, table_ddl_if_absent, tag_statements

    settings = DatabricksSettings.from_env()
    registry = ContractRegistry.load(REPO / "data_contracts")
    statements = [schema_ddl(settings.catalog, "ops")]
    for contract in sorted((c for c in registry if c.layer == "ops"), key=lambda c: c.table):
        statements += [
            table_ddl_if_absent(contract, settings.catalog),
            *tag_statements(contract, settings.catalog),
        ]
    with sql_connection(settings) as connection, connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
    print(f"{len(statements)} statements: schema ops and its tables")


def bundle(client) -> str:
    from databricks.sdk.service.workspace import ImportFormat

    home = _home(client)
    client.workspace.mkdirs(f"{home}/snm_evals")
    files = {f"{home}/jobs/{p.name}": p for p in (REPO / "deploy/databricks/jobs").glob("*.py")}
    files |= {f"{home}/snm_evals/{name}": REPO / "evals/src/snm_evals" / name for name in MODULES}
    client.workspace.mkdirs(f"{home}/jobs")
    for target, source in files.items():
        client.workspace.upload(
            target, io.BytesIO(source.read_bytes()), format=ImportFormat.AUTO, overwrite=True
        )
    print(f"uploaded {len(files)} files to {home}")
    return home


def _job_settings(name: str, script: str, parameters: list[str], cron: str, paused: bool, home: str):
    from databricks.sdk.service import compute, jobs

    return {
        "name": name,
        "tasks": [
            jobs.Task(
                task_key=name.replace("-", "_"),
                spark_python_task=jobs.SparkPythonTask(
                    python_file=f"{home}/jobs/{script}", parameters=parameters
                ),
                environment_key="default",
            )
        ],
        "environments": [
            jobs.JobEnvironment(environment_key="default", spec=compute.Environment(client="2"))
        ],
        "schedule": jobs.CronSchedule(
            quartz_cron_expression=cron,
            timezone_id="UTC",
            pause_status=jobs.PauseStatus.PAUSED if paused else jobs.PauseStatus.UNPAUSED,
        ),
        "max_concurrent_runs": 1,
    }


def _upsert_job(client, settings: dict) -> int:
    from databricks.sdk.service import jobs

    found = next(iter(client.jobs.list(name=settings["name"])), None)
    if found is None:
        job_id = client.jobs.create(**settings).job_id
        print(f"job {settings['name']}: created ({job_id})")
        return job_id
    client.jobs.reset(found.job_id, jobs.JobSettings(**settings))
    print(f"job {settings['name']}: updated ({found.job_id})")
    return found.job_id


def deploy_jobs(client) -> None:
    home = _home(client)
    _upsert_job(client, _job_settings(DAILY_JOB, "ops_daily.py", ["--bundle", home], DAILY_CRON, False, home))
    _upsert_job(client, _job_settings(UPTIME_JOB, "uptime_check.py", [], UPTIME_CRON, True, home))


def set_uptime(client, on: bool) -> None:
    from databricks.sdk.service import jobs

    found = next(iter(client.jobs.list(name=UPTIME_JOB)), None)
    if found is None:
        raise SystemExit(f"no job {UPTIME_JOB}; run `jobs` first")
    current = client.jobs.get(found.job_id).settings
    schedule = jobs.CronSchedule(
        quartz_cron_expression=current.schedule.quartz_cron_expression,
        timezone_id=current.schedule.timezone_id,
        pause_status=jobs.PauseStatus.UNPAUSED if on else jobs.PauseStatus.PAUSED,
    )
    client.jobs.update(found.job_id, new_settings=jobs.JobSettings(schedule=schedule))
    print(f"{UPTIME_JOB}: {'resumed' if on else 'paused'}")


def dashboard(client) -> str:
    from databricks.sdk.service.dashboards import Dashboard

    warehouse = os.environ["DATABRICKS_WAREHOUSE_ID"]
    home = _home(client)
    client.workspace.mkdirs(home)
    body = Dashboard(
        display_name=DASHBOARD,
        parent_path=home,
        serialized_dashboard=ops_dashboard.serialized(),
        warehouse_id=warehouse,
    )
    try:  # found by its file, not its name: a listing of dashboards can lag behind a create
        existing = client.workspace.get_status(f"{home}/{DASHBOARD}.lvdash.json").resource_id
    except Exception:
        existing = None
    made = client.lakeview.create(body) if existing is None else client.lakeview.update(existing, body)
    client.lakeview.publish(made.dashboard_id, warehouse_id=warehouse, embed_credentials=True)
    print(f"dashboard {DASHBOARD}: {made.dashboard_id}, published")
    return made.dashboard_id


def check() -> None:
    """Run every dataset with its default parameters, and the cards again with source = real."""
    from snm_data.databricks import DatabricksSettings, sql_connection

    defaults = {
        k: (date.fromisoformat(v) if t == "DATE" else v) for k, (t, v) in ops_dashboard.PARAMS.items()
    }
    with sql_connection(DatabricksSettings.from_env()) as connection, connection.cursor() as cursor:
        for name, (_, sql) in ops_dashboard.DATASETS.items():
            params = {k: defaults[k] for k in ops_dashboard.parameters_of(sql)}
            cursor.execute(sql, params)
            rows = cursor.fetchall()
            print(
                f"{name}: {len(rows)} rows" + (f"; first {tuple(rows[0])}" if rows and len(rows) == 1 else "")
            )


CARD_FIELDS = {
    "cards_runs": ["trace_check_pass_rate", "refusal_rate", "error_rate", "latency_p50", "latency_p95",
                   "cost_p50", "cost_p95", "total_cost", "cost_per_case"],
    "cards_decisions": ["acceptance_rate"],
    "cards_documents": ["extractor_accuracy"],
    "cards_uptime": ["uptime"],
}  # fmt: skip
DAY = {
    "agent_runs": "run_date",
    "decisions": "decision_date",
    "documents": "confirm_date",
    "uptime_checks": "check_date",
}


def _same(a, b) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(float(a) - float(b)) < 1e-9)


def recount() -> int:
    """The cards as the dashboard computes them (its SQL, on the demo rows) against ``snm_evals.ops.cards`` on
    the same rows, for the default range; 1 when a card differs."""
    sys.path.insert(0, str(REPO / "evals/src"))
    from snm_data.databricks import DatabricksSettings, sql_connection
    from snm_evals.ops import SqlStore, cards

    settings = DatabricksSettings.from_env()
    first, last = (date.fromisoformat(ops_dashboard.PARAMS[k][1]) for k in ("from_date", "to_date"))
    differ = 0
    with sql_connection(settings) as connection:
        store = SqlStore(connection, settings.catalog)
        kept = {
            table: [r for r in store._rows(f"SELECT * FROM {store._name(table)} WHERE source = 'demo'")
                    if first <= r[DAY[table]] <= last]
            for table in DAY
        }  # fmt: skip
        python = cards(kept["agent_runs"], kept["decisions"], kept["documents"], kept["uptime_checks"])
        params = {"from_date": first, "to_date": last, "agent_task": "all", "case_id": "all"}
        for dataset, fields in CARD_FIELDS.items():
            sql = ops_dashboard.DATASETS[dataset][1]
            with connection.cursor() as cursor:
                cursor.execute(sql, {k: params[k] for k in ops_dashboard.parameters_of(sql)})
                columns = [d[0] for d in cursor.description]
                values = dict(zip(columns, cursor.fetchone(), strict=True))
            for field in fields:
                same = _same(values[field], python[field])
                differ += not same
                verdict = "same" if same else "DIFFERENT"
                print(f"{field:<22} SQL {values[field]!s:<22} Python {python[field]!s:<22} {verdict}")
    return 1 if differ else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "step", choices=["tables", "bundle", "jobs", "dashboard", "check", "recount", "uptime"]
    )
    parser.add_argument("state", nargs="?", choices=["on", "off"])
    args = parser.parse_args()
    if args.step == "tables":
        tables()
    elif args.step == "check":
        check()
    elif args.step == "recount":
        return recount()
    else:
        client = _client()
        if args.step == "bundle":
            bundle(client)
        elif args.step == "jobs":
            deploy_jobs(client)
        elif args.step == "dashboard":
            dashboard(client)
        elif args.step == "uptime":
            if args.state is None:
                raise SystemExit("uptime on|off")
            set_uptime(client, args.state == "on")
    return 0


if __name__ == "__main__":
    sys.exit(main())
