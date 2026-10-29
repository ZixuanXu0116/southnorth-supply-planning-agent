"""Load generated Parquet files into Unity Catalog tables with contract comments and tags."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .contracts import Contract, ContractRegistry
from .databricks import DatabricksSettings, sql_connection, workspace_client
from .ddl import insert_from_files, quote_ident, schema_ddl, sql_string, table_ddl, tag_statements

LANDING_SCHEMA = "landing"
LANDING_VOLUME = "files"
# Schema properties that record which generated snapshot a schema holds (read by the evaluation runner).
SNAPSHOT_PREFIX = "snm."
_PROPERTY = re.compile(r"\(([^(),]+),([^()]*)\)")


@dataclass
class LoadReport:
    profile: str
    catalog: str
    tables: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    seconds: float = 0.0


def volume_path(catalog: str, profile: str, table: str) -> str:
    schema, name = table.split(".")
    return f"/Volumes/{catalog}/{LANDING_SCHEMA}/{LANDING_VOLUME}/{profile}/{schema}/{name}.parquet"


def setup_statements(catalog: str, schemas: set[str]) -> list[str]:
    """Catalog, schemas (including the landing schema), and the landing volume."""
    statements = [f"CREATE CATALOG IF NOT EXISTS {quote_ident(catalog)}"]
    statements += [schema_ddl(catalog, schema) for schema in sorted(schemas | {LANDING_SCHEMA})]
    statements.append(
        f"CREATE VOLUME IF NOT EXISTS "
        f"{quote_ident(catalog)}.{quote_ident(LANDING_SCHEMA)}.{quote_ident(LANDING_VOLUME)}"
    )
    return statements


def snapshot_of(manifest: dict) -> dict[str, str]:
    """Identity of a generated profile: the data snapshot that reference answers are bound to."""
    profile = manifest["profile"]
    return {
        "profile": str(profile["name"]),
        "generator_version": str(manifest["generator_version"]),
        "seed": str(profile["seed"]),
        "contracts_digest": str(manifest["contracts_digest"]),
    }


def snapshot_statements(
    catalog: str, schemas: set[str], snapshot: dict[str, str], status: str, at: datetime
) -> list[str]:
    """Record the snapshot and load status (`loading` or `complete`) as properties of each loaded schema."""
    properties = {**snapshot, "load_status": status, "loaded_at": at.isoformat(timespec="seconds")}
    rendered = ", ".join(
        f"{sql_string(SNAPSHOT_PREFIX + key)} = {sql_string(value)}" for key, value in properties.items()
    )
    return [
        f"ALTER SCHEMA {quote_ident(catalog)}.{quote_ident(schema)} SET DBPROPERTIES ({rendered})"
        for schema in sorted(schemas)
    ]


def parse_snapshot(properties: str) -> dict[str, str]:
    """Snapshot properties from the `Properties` row of `DESCRIBE SCHEMA EXTENDED` (prefix removed)."""
    pairs = (match.groups() for match in _PROPERTY.finditer(properties))
    return {
        key.strip()[len(SNAPSHOT_PREFIX) :]: value.strip()
        for key, value in pairs
        if key.strip().startswith(SNAPSHOT_PREFIX)
    }


def read_snapshots(settings: DatabricksSettings, schemas: list[str]) -> dict[str, dict[str, str]]:
    """The recorded snapshot of each schema (empty if it was loaded before snapshots were recorded)."""
    snapshots: dict[str, dict[str, str]] = {}
    with sql_connection(settings) as connection, connection.cursor() as cursor:
        for schema in schemas:
            cursor.execute(f"DESCRIBE SCHEMA EXTENDED {quote_ident(settings.catalog)}.{quote_ident(schema)}")
            rows = {row[0]: row[1] for row in cursor.fetchall()}
            snapshots[schema] = parse_snapshot(rows.get("Properties") or "")
    return snapshots


def table_statements(contract: Contract, catalog: str, profile: str) -> list[str]:
    """Create the table from its contract, load it from the landing volume, and apply tags."""
    path = volume_path(catalog, profile, contract.table)
    return [
        table_ddl(contract, catalog),
        insert_from_files(contract, catalog, path),
        *tag_statements(contract, catalog),
    ]


def load(out_dir: Path, registry: ContractRegistry, settings: DatabricksSettings, log=print) -> LoadReport:
    """Upload a generated profile to the landing volume and (re)create its tables."""
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest["violations"]:
        raise RuntimeError(f"Refusing to load data with {len(manifest['violations'])} contract violations")
    profile = manifest["profile"]["name"]
    expected: dict[str, int] = manifest["row_counts"]
    tables = sorted(expected)
    schemas = {table.split(".")[0] for table in tables}
    snapshot = snapshot_of(manifest)
    report = LoadReport(profile=profile, catalog=settings.catalog)
    started = time.perf_counter()
    client = workspace_client(settings)
    with sql_connection(settings) as connection, connection.cursor() as cursor:
        for statement in setup_statements(settings.catalog, schemas):
            cursor.execute(statement)
        # Marked as loading first, so an interrupted load is never mistaken for a complete snapshot.
        for statement in snapshot_statements(
            settings.catalog, schemas, snapshot, "loading", datetime.now(UTC)
        ):
            cursor.execute(statement)
        for table in tables:
            schema, name = table.split(".")
            with (out_dir / schema / f"{name}.parquet").open("rb") as handle:
                client.files.upload(volume_path(settings.catalog, profile, table), handle, overwrite=True)
            try:
                for statement in table_statements(registry[table], settings.catalog, profile):
                    cursor.execute(statement)
                cursor.execute(f"SELECT COUNT(*) FROM {quote_ident(settings.catalog)}.{schema}.{name}")
                loaded = cursor.fetchone()[0]
            except Exception as error:  # continue with other tables and report every failure
                report.errors.append(f"{table}: {str(error).splitlines()[0][:300]}")
                log(f"FAILED {table}")
                continue
            report.tables[table] = {"expected_rows": expected[table], "loaded_rows": loaded}
            if loaded != expected[table]:
                report.errors.append(f"{table}: loaded {loaded} rows, expected {expected[table]}")
            log(f"loaded {table}: {loaded:,} rows")
        if not report.errors:
            for statement in snapshot_statements(
                settings.catalog, schemas, snapshot, "complete", datetime.now(UTC)
            ):
                cursor.execute(statement)
    report.seconds = round(time.perf_counter() - started, 1)
    (out_dir / "load_report.json").write_text(
        json.dumps(report.__dict__, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return report
