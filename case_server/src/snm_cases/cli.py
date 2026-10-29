"""``snm-cases``: deploy the case store, sync engine runs into it, and record human actions.

``act`` is the command-line stand-in for the web app's buttons (send, decide, hand off, confirm receipt,
cancel, override triage); agents never get these actions.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from snm_data.contracts import ContractRegistry
from snm_data.ddl import schema_ddl, table_ddl_if_absent, tag_statements

from .planning import DatabricksPlanningData, LocalPlanningData
from .server import REPO_ROOT, default_planning_dir
from .service import Actor, CaseError, CaseService
from .store import ATTACHMENT_VOLUME, DatabricksCaseStore, LocalCaseStore

CONTRACTS = REPO_ROOT / "data_contracts"
HUMAN_ACTIONS = (
    "request_sent",
    "mandate_set",
    "decision_recorded",
    "handed_off",
    "receipt_confirmed",
    "case_cancelled",
    "triage_overridden",
    "note_added",
    "request_drafted",
    "transfer_prepared",
)


def case_tables(registry: ContractRegistry) -> list:
    return sorted((c for c in registry if c.layer == "cases"), key=lambda c: c.table)


def deploy_statements(registry: ContractRegistry, catalog: str) -> list[str]:
    """Create the ``cases`` schema, its append-only tables if they do not exist (never replaced), and the
    attachments volume (decision record 005)."""
    statements = [schema_ddl(catalog, "cases")]
    for contract in case_tables(registry):
        statements.append(table_ddl_if_absent(contract, catalog))
        name = ".".join(f"`{part}`" for part in (catalog, *contract.table.split(".")))
        statements.append(f"ALTER TABLE {name} SET TBLPROPERTIES ('delta.appendOnly' = 'true')")
        statements += tag_statements(contract, catalog)
    statements.append(f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`cases`.`{ATTACHMENT_VOLUME}`")
    return statements


def add_column_statements(
    registry: ContractRegistry, catalog: str, existing: dict[str, set[str]]
) -> list[str]:
    """ALTER TABLE ADD COLUMNS for contract columns an existing table lacks (a contract may grow; the
    tables are never replaced). ``existing`` maps ``schema.table`` to its current column names."""
    statements = []
    for contract in case_tables(registry):
        current = existing.get(contract.table)
        if current is None:
            continue
        missing = [column for column in contract.columns if column.name not in current]
        if missing:
            name = ".".join(f"`{part}`" for part in (catalog, *contract.table.split(".")))
            columns = ", ".join(f"`{c.name}` {c.type}" for c in missing)
            statements.append(f"ALTER TABLE {name} ADD COLUMNS ({columns})")
    return statements


def _local_service(args) -> CaseService:
    planning_dir = Path(args.planning) if args.planning else default_planning_dir()
    cost = REPO_ROOT / "out" / "small" / "raw_ext" / "cost_parameter.parquet"
    return CaseService(LocalCaseStore(Path(args.store)), LocalPlanningData(planning_dir, cost))


def _databricks_service() -> CaseService:
    from snm_data.databricks import DatabricksSettings, sql_connection, workspace_client
    from snm_tools.backend import DatabricksBackend

    settings = DatabricksSettings.from_env()
    store = DatabricksCaseStore(
        lambda: sql_connection(settings), settings.catalog, files=lambda: workspace_client(settings)
    )
    return CaseService(store, DatabricksPlanningData(DatabricksBackend(settings), settings.catalog))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="snm-cases", description=__doc__)
    parser.add_argument(
        "--store", default=str(REPO_ROOT / "out" / "cases"), help="local case store directory"
    )
    parser.add_argument("--planning", help="local planning run directory (default: latest out/planning/*)")
    parser.add_argument(
        "--databricks", action="store_true", help="use the lakehouse case store and planning tables"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "deploy", help="create the cases schema and tables in Unity Catalog (owner credentials)"
    )
    sync = commands.add_parser("sync", help="open, update, or clear cases from the latest engine run")
    sync.add_argument("--run-id", default=None)
    listing = commands.add_parser("list", help="list cases")
    listing.add_argument("--role", default="purchasing")
    listing.add_argument("--state")
    listing.add_argument("--triage-class")
    show = commands.add_parser("show", help="show one case")
    show.add_argument("case_id")
    show.add_argument("--role", default="purchasing")
    act = commands.add_parser("act", help="record a human action (the web app's buttons)")
    act.add_argument("case_id")
    act.add_argument("event_type", choices=HUMAN_ACTIONS)
    act.add_argument("--role", required=True, help="the person's role (purchasing or a logistics role)")
    act.add_argument("--payload", default="{}", help="event payload as JSON")
    supplier = commands.add_parser("supplier-message", help="record an inbound supplier message (portal)")
    supplier.add_argument("case_id")
    supplier.add_argument("body")
    args = parser.parse_args(argv)

    if args.command == "deploy":
        from snm_data.databricks import DatabricksSettings, sql_connection

        settings = DatabricksSettings.from_env()
        registry = ContractRegistry.load(CONTRACTS)
        with sql_connection(settings) as connection, connection.cursor() as cursor:
            for statement in deploy_statements(registry, settings.catalog):
                cursor.execute(statement)
            existing: dict[str, set[str]] = {}
            for contract in case_tables(registry):
                schema, name = contract.table.split(".")
                cursor.execute(f"SHOW COLUMNS IN `{settings.catalog}`.`{schema}`.`{name}`")
                existing[contract.table] = {row[0] for row in cursor.fetchall()}
            added = add_column_statements(registry, settings.catalog, existing)
            for statement in added:
                cursor.execute(statement)
        print(
            f"deployed {len(case_tables(registry))} case tables (append-only), the attachments volume, "
            f"{len(added)} added column set(s)"
        )
        return 0

    service = _databricks_service() if args.databricks else _local_service(args)
    try:
        if args.command == "sync":
            print(json.dumps(service.sync_engine_run(args.run_id)))
        elif args.command == "list":
            result = service.list_cases(
                Actor("human", args.role), state=args.state, triage_class=args.triage_class
            )
            print(json.dumps(result, indent=2, default=str))
        elif args.command == "show":
            print(
                json.dumps(service.get_case(Actor("human", args.role), args.case_id), indent=2, default=str)
            )
        elif args.command == "act":
            event = service.append(
                Actor("human", args.role), args.case_id, args.event_type, json.loads(args.payload)
            )
            print(f"{event.case_id} #{event.seq} {event.event_type} -> {event.state_after}")
        elif args.command == "supplier-message":
            event = service.record_message(
                Actor("system", "supplier_portal"), args.case_id, args.body, "inbound", "supplier"
            )
            print(f"{event.case_id} #{event.seq} message {event.payload['message_id']}")
    except CaseError as error:
        print(f"{error.code}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
