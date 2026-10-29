"""Run the engine and publish its tables (``snm-data plan``).

Inputs come from the lakehouse (``from_databricks``) or, offline, from a generated profile's Parquet files.
Outputs are conformed to their contracts, written as Parquet, and (unless offline) uploaded to the landing
volume and loaded into the curated tables with the same statements the data loader uses; governance is
re-applied afterwards because recreated tables lose their filters, masks, and grants.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ..contracts import ContractRegistry, check_frame, conform
from .engine import PlanningOutputs, plan
from .inputs import PlanningInputs
from .params import ENGINE_VERSION, PlanningParams

OUTPUT_TABLES = ("curated.supply_position", "curated.replenishment_case", "curated.supplier_profile")
VOLUME_PROFILE = "planning"
RAW_INPUT_TABLES = (
    "raw_sap_ecc.marc", "raw_sap_ecc.mbew", "raw_sap_ecc.makt", "raw_sap_ecc.mard", "raw_sap_ecc.ekko",
    "raw_sap_ecc.ekpo", "raw_sap_ecc.eket", "raw_sap_ecc.mseg", "raw_sap_ecc.lfa1", "raw_sap_ecc.likp",
    "raw_sap_ecc.lips", "raw_sap_ecc.resb",
    "raw_ext.supplier_condition", "raw_ext.cost_parameter", "raw_ext.purchasing_interaction",
)  # fmt: skip


@dataclass
class PlanReport:
    as_of: date
    engine_version: str = ENGINE_VERSION
    params_digest: str = ""
    rows: dict[str, int] = field(default_factory=dict)
    cases: dict[str, int] = field(default_factory=dict)
    violations: list[str] = field(default_factory=list)
    seconds: float = 0.0
    published: bool = False


def inputs_from_profile(out_dir: Path, as_of: date, params: PlanningParams) -> PlanningInputs:
    """Offline inputs from a generated profile directory (``snm-data generate --out``)."""
    tables = {}
    for name in RAW_INPUT_TABLES:
        schema, table = name.split(".")
        path = out_dir / schema / f"{table}.parquet"
        if path.exists():
            tables[name] = pl.read_parquet(path)
    return PlanningInputs.from_tables(tables, as_of, params)


def conformed_outputs(
    outputs: PlanningOutputs, registry: ContractRegistry
) -> tuple[dict[str, pl.DataFrame], list[str]]:
    frames = {
        "curated.supply_position": outputs.supply_position,
        "curated.replenishment_case": outputs.replenishment_case,
        "curated.supplier_profile": outputs.supplier_profile,
    }
    conformed, violations = {}, []
    for name, frame in frames.items():
        contract = registry[name]
        if frame.height == 0:
            conformed[name] = pl.DataFrame(schema=contract.polars_schema())
            continue
        conformed[name] = conform(frame, contract)
        violations += check_frame(conformed[name], contract)
    return conformed, violations


def write_outputs(frames: dict[str, pl.DataFrame], out_dir: Path) -> None:
    for name, frame in frames.items():
        schema, table = name.split(".")
        target = out_dir / schema / f"{table}.parquet"
        target.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(target, compression="zstd", statistics=True)


def publish(
    frames: dict[str, pl.DataFrame], out_dir: Path, registry: ContractRegistry, settings: Any
) -> None:
    """Upload the Parquet files and (re)create the curated tables; then re-apply governance."""
    from ..databricks import sql_connection, workspace_client
    from ..governance import govern, is_deployed
    from ..load import table_statements, volume_path

    client = workspace_client(settings)
    with sql_connection(settings) as connection, connection.cursor() as cursor:
        for name in frames:
            schema, table = name.split(".")
            with (out_dir / schema / f"{table}.parquet").open("rb") as handle:
                path = volume_path(settings.catalog, VOLUME_PROFILE, name)
                client.files.upload(path, handle, overwrite=True)
            for statement in table_statements(registry[name], settings.catalog, VOLUME_PROFILE):
                cursor.execute(statement)
    if is_deployed(settings):
        govern(registry, settings, identities=False, log=lambda _: None)


def run_plan(
    registry: ContractRegistry,
    out_dir: Path,
    as_of: date,
    params: PlanningParams,
    *,
    settings: Any | None = None,
    profile_dir: Path | None = None,
    log=print,
) -> PlanReport:
    """Compute and publish (or, with ``profile_dir``, compute offline) the planning tables."""
    started = time.perf_counter()
    if profile_dir is not None:
        inputs = inputs_from_profile(profile_dir, as_of, params)
    elif settings is not None:
        inputs = PlanningInputs.from_databricks(settings, as_of, params)
    else:
        raise ValueError("either settings (lakehouse inputs) or profile_dir (offline inputs) is required")
    log(f"inputs: {inputs.materials.height} materials, {inputs.open_orders.height} open order lines")
    outputs = plan(inputs, params)
    frames, violations = conformed_outputs(outputs, registry)
    report = PlanReport(as_of=as_of, params_digest=params.digest(), violations=violations)
    report.rows = {name: frame.height for name, frame in frames.items()}
    cases = frames["curated.replenishment_case"]
    if cases.height:
        counts = cases.group_by(["condition", "triage_class"]).len().sort(["condition", "triage_class"])
        report.cases = {f"{row[0]}/{row[1]}": int(row[2]) for row in counts.iter_rows()}
    write_outputs(frames, out_dir)
    if violations:
        log(f"{len(violations)} contract violations; not published")
    elif settings is not None and profile_dir is None:
        publish(frames, out_dir, registry, settings)
        report.published = True
    report.seconds = round(time.perf_counter() - started, 1)
    (out_dir / "plan_report.json").write_text(
        json.dumps({**report.__dict__, "as_of": as_of.isoformat(), "run_at": datetime.now(UTC).isoformat()},
                   indent=2, default=str) + "\n",
        encoding="utf-8",
    )  # fmt: skip
    return report
