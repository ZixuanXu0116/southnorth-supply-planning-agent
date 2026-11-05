"""Move a planning run to another day (decided 2026-11-02: the case server always works on the real today,
so the synthetic planning data is moved to today before a test).

Every date of the run moves by the same number of days: date and timestamp columns, ISO dates inside text
(the engine's rationale, the levers' JSON), and the compact date in an engine case id. Nothing else changes:
the projection, quantities, costs, parameters, and engine version stay as they are. The moved run has a new
as-of date, so a long-lived case server drops its reads of the old one (``snm_cases.planning`` checks the
published run), and a case sync (``snm-cases sync``) gives the open cases their new dates.

In the lakehouse the moved rows replace the curated tables' rows with ``INSERT OVERWRITE``, the same load as
``snm-data plan`` uses, so the tables keep their grants, masks, and tags; nothing is re-created or re-granted.
"""

from __future__ import annotations

import json
import re
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from ..contracts import ContractRegistry, conform
from .run import VOLUME_PROFILE

TABLES = ("curated.replenishment_case", "curated.supply_position", "curated.supplier_profile")
ISO = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
COMPACT = re.compile(r"-(20\d{2})(\d{2})(\d{2})(?=-|$)")


def shift_text(text: str | None, days: int) -> str | None:
    """ISO dates and the compact date of a case id (C-US10-40000410-SH-20261124), moved by ``days``."""
    if text is None or days == 0:
        return text

    def iso(match: re.Match[str]) -> str:
        try:
            return (date(int(match[1]), int(match[2]), int(match[3])) + timedelta(days=days)).isoformat()
        except ValueError:
            return match[0]

    def compact(match: re.Match[str]) -> str:
        try:
            moved = date(int(match[1]), int(match[2]), int(match[3])) + timedelta(days=days)
        except ValueError:
            return match[0]
        return f"-{moved:%Y%m%d}"

    return COMPACT.sub(compact, ISO.sub(iso, text))


def shift_frame(frame: pl.DataFrame, days: int) -> pl.DataFrame:
    expressions = []
    for name, dtype in frame.schema.items():
        column = pl.col(name)
        if dtype in (pl.Date,) or isinstance(dtype, pl.Datetime):
            expressions.append(column + pl.duration(days=days))
        elif dtype == pl.String and frame.get_column(name).str.contains(r"20\d{2}-?\d{2}-?\d{2}").any():
            expressions.append(column.map_elements(lambda v: shift_text(v, days), return_dtype=pl.String))
    return frame.with_columns(expressions) if expressions else frame


def as_of_of(frames: dict[str, pl.DataFrame]) -> date:
    value = frames["curated.replenishment_case"].get_column("as_of_date")[0]
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def shift_local(planning_dir: Path, to: date) -> Path:
    """A local planning run moved to ``to``, written next to it as out/planning/<to>/."""
    curated = Path(planning_dir) / "curated"
    frames = {name: pl.read_parquet(curated / f"{name.split('.')[1]}.parquet") for name in TABLES}
    days = (to - as_of_of(frames)).days
    target = Path(planning_dir).parent / to.isoformat()
    (target / "curated").mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        shift_frame(frame, days).write_parquet(target / "curated" / f"{name.split('.')[1]}.parquet")
    report_path = Path(planning_dir) / "plan_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    report.update(
        as_of=to.isoformat(), shifted_from=as_of_of(frames).isoformat(), shifted_days=days, published=False
    )
    (target / "plan_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return target


def shift_lakehouse(settings: Any, registry: ContractRegistry, to: date, log=print) -> dict[str, Any]:
    """The published run moved to ``to``: read, moved, uploaded to the landing volume, and loaded with
    INSERT OVERWRITE (grants, masks, and tags stay)."""
    from ..databricks import sql_connection, workspace_client
    from ..ddl import insert_from_files
    from ..load import volume_path

    client = workspace_client(settings)
    frames: dict[str, pl.DataFrame] = {}
    with sql_connection(settings) as connection, connection.cursor() as cursor:
        for name in TABLES:
            cursor.execute(
                f"SELECT * FROM `{settings.catalog}`.`{name.split('.')[0]}`.`{name.split('.')[1]}`"
            )
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            frame = pl.DataFrame(
                [list(row) for row in rows], schema=columns, orient="row", infer_schema_length=None
            )
            frames[name] = conform(frame, registry[name])
        days = (to - as_of_of(frames)).days
        if days == 0:
            log(f"the published run is already as of {to}")
            return {"days": 0, "rows": {name: f.height for name, f in frames.items()}}
        with tempfile.TemporaryDirectory() as tmp:
            for name, frame in frames.items():
                local = Path(tmp) / f"{name}.parquet"
                shift_frame(frame, days).write_parquet(local, compression="zstd", statistics=True)
                path = volume_path(settings.catalog, VOLUME_PROFILE, name)
                with local.open("rb") as handle:
                    client.files.upload(path, handle, overwrite=True)
                cursor.execute(insert_from_files(registry[name], settings.catalog, path))
                log(f"{name}: {frame.height} rows moved by {days} days")
    return {"days": days, "rows": {name: f.height for name, f in frames.items()}}
